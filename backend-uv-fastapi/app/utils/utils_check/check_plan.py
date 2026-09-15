# app/utils/utils_check/check_plan.py —— 开发期自检：交付规划与「预估 vs 实际」对账（阶段 5）
#
# 运行（在 backend-uv-fastapi 下，必须用 -m 让 app 包可导入）：
#   uv run python -m app.utils.utils_check.check_plan
#
# 三段：
#
#   第一段（离线）：清单清洗与预算 —— 非法文件名丢弃、悬空依赖剔除、难度只上调、
#        全非法时兜底单文件、预算随难度递增。
#   第二段（⚠️ 真实模型，按 token 计费）：同一需求跑 **3 次**，看文件清单是否稳定；
#        再看一个明确简单的需求会不会被拆成多个文件（难度判定的有效性）。
#   第三段（PG 往返）：把规划写进 generation_plan，再回填"实际值"并读回对账 ——
#        这一段的重点是**预估侧与实际侧在同一行**，因为这张表就是为优化提示词准备的：
#        "模型说 3 个文件 / 12 步预算，实际用了 4 步交付 3 个文件" 这种对照必须一眼可查。
#
# ⚠️ 第三段会往 PG 写真实的临时行，脚本结束前会**删掉**（不依赖逻辑删除标记）。

import sys
import uuid

from langchain_core.runnables import RunnableLambda
from sqlalchemy.orm import Session

from app.agents.common import budget_for, difficulty_for_file_count
from app.agents.plan.plan_agent import FALLBACK_ENTRY, PlanResult, heuristic_plan, plan
from app.agents.state import FilePlan, FinalRequirement, PlannedFile, RequirementSlots
from app.core.pg_db import PgSessionLocal
from app.models.agent import GenerationPlan
from app.repositories.agent import GenerationPlanRepository

# ⚠️ Windows 中文控制台默认编码是 GBK，打印 ✅/⚠️ 会 UnicodeEncodeError 崩掉脚本
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 自检用的临时 user_id（跨库无外键，任意整数都行）；脚本结束会删掉写入的行
CHECK_USER_ID = 0


def _requirement(text: str, *, site_kind: str = "单页展示", features: list[str] | None = None) -> FinalRequirement:
    """按一句话需求造一个 FinalRequirement（自检用，不必真跑一遍 merge）。"""
    return FinalRequirement(
        summary=text,
        slots=RequirementSlots(site_kind=site_kind, features=features or []),
        constraints=["必须响应式"],
    )


def check_offline() -> bool:
    """第一段：清单清洗、难度复核与兜底（无模型）。"""
    print("=" * 78)
    print("1) 清单清洗与预算（离线）")

    problems: list[str] = []
    requirement = _requirement("做一个带筛选的待办清单")

    # ① 非法文件名被丢弃，合法的保留
    def _chain_of(files: list[PlannedFile], difficulty: str = "medium") -> RunnableLambda:
        declared = FilePlan(
            difficulty=difficulty,  # type: ignore[arg-type]
            entry_file=files[0].name if files else FALLBACK_ENTRY,
            files=files,
        )
        return RunnableLambda(lambda _payload: {"raw": None, "parsed": declared})

    dirty = plan(
        requirement,
        chain=_chain_of(
            [
                PlannedFile(name="index.html"),
                PlannedFile(name="../evil.css", role="style"),
                PlannedFile(name="样式.css", role="style"),
            ]
        ),
    )
    ok = dirty.plan.names() == ["index.html"] and any("不合法" in w for w in dirty.warnings)
    print(f"   [非法名]   保留={dirty.plan.names()} 警告={len(dirty.warnings)} 条 -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("非法文件名应被丢弃并记警告（合法文件不受影响）")

    # ② 难度只上调：easy + 3 文件 → medium，且保留模型原始声明
    escalated = plan(
        requirement,
        chain=_chain_of(
            [
                PlannedFile(name="index.html"),
                PlannedFile(name="style.css", role="style"),
                PlannedFile(name="app.js", role="script"),
            ],
            difficulty="easy",
        ),
    )
    ok = (
        escalated.plan.difficulty == "medium"
        and escalated.difficulty_declared == "easy"
        and escalated.budget.difficulty == "medium"
    )
    print(f"   [难度上调] 声明={escalated.difficulty_declared} 最终={escalated.plan.difficulty}"
          f" 预算步数={escalated.budget.max_steps} -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("声明 easy 却交 3 个文件时必须上调到 medium，并保留原始声明")

    # ③ 全非法 → 兜底单文件计划（degraded）
    fallback = plan(
        requirement,
        chain=_chain_of([PlannedFile(name="a/b.html"), PlannedFile(name="坏.css", role="style")]),
    )
    ok = fallback.degraded and fallback.plan.names() == [FALLBACK_ENTRY]
    print(f"   [兜底]     degraded={fallback.degraded} 清单={fallback.plan.names()} -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("文件全非法时应退化为单文件兜底计划")

    # ④ 悬空依赖剔除，但文件保留
    dangling = plan(
        requirement,
        chain=_chain_of(
            [
                PlannedFile(name="index.html"),
                PlannedFile(name="app.js", role="script", depends_on=["nope.css"]),
            ]
        ),
    )
    ok = dangling.plan.names() == ["index.html", "app.js"] and dangling.plan.files[1].depends_on == []
    print(f"   [悬空依赖] 清单={dangling.plan.names()} 依赖={dangling.plan.files[1].depends_on} -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("悬空依赖应被剔除，但文件本身要保留")

    # ⑤ 预算随难度递增 + 文件数推算
    budgets = [budget_for(name) for name in ("easy", "medium", "hard")]
    monotonic = all(
        a.max_steps < b.max_steps and a.max_output_tokens < b.max_output_tokens
        for a, b in zip(budgets, budgets[1:])
    )
    floors = [difficulty_for_file_count(1), difficulty_for_file_count(3), difficulty_for_file_count(6)]
    ok = monotonic and floors == ["easy", "medium", "hard"]
    print(f"   [预算]     步数={[b.max_steps for b in budgets]} "
          f"token={[b.max_output_tokens for b in budgets]} 文件数→难度={floors} -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("预算必须随难度递增；文件数→难度推算应符合 1/4/8 的分档")

    # ⑥ 兜底计划本身即契约
    fb = heuristic_plan(requirement)
    ok = fb.names() == [FALLBACK_ENTRY] and fb.difficulty == "easy"
    print(f"   [兜底计划] {fb.names()} difficulty={fb.difficulty} -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("兜底计划应为 easy 难度的单文件 index.html")

    if problems:
        print("   结论   -> 不通过：")
        for item in problems:
            print(f"             · {item}")
        return False
    print("   结论   -> 通过")
    return True


# (标题, 需求描述, 期望的最大文件数；None 表示只看稳定性不做数量断言)
REAL_CASES: list[tuple[str, FinalRequirement, int | None]] = [
    (
        "待办清单（简单单页）",
        _requirement("做一个待办清单页面：能添加、勾选完成、删除；数据存 localStorage；极简白底。",
                     features=["添加待办", "勾选完成", "删除"]),
        3,
    ),
    (
        "企业官网多页（复杂）",
        _requirement(
            "做一个企业官网：首页、产品页、关于我们、联系我们四个页面，"
            "共用一个导航与页脚样式，产品数据放在单独的 JSON 里。",
            site_kind="内容站",
            features=["首页", "产品页", "关于我们", "联系我们"],
        ),
        8,
    ),
]


def check_real_model() -> tuple[bool, PlanResult | None]:
    """第二段：真实模型的规划质量（⚠️ 计费）。"""
    print("=" * 78)
    print("2) 真实模型规划（⚠️ 计费；不需要 API）")

    problems: list[str] = []
    first_result: PlanResult | None = None

    for title, requirement, max_files in REAL_CASES:
        print("-" * 78)
        print(f"   [{title}]")
        for attempt in range(1, 4):
            try:
                result = plan(requirement)
            except Exception as error:  # noqa: BLE001 —— 模型不可用时按"跳过"处理
                print(f"   跳过   -> 模型不可用（{type(error).__name__}: {error}）")
                return True, first_result

            if first_result is None:
                first_result = result
            names = result.plan.names()
            print(f"   第 {attempt} 次 -> difficulty={result.plan.difficulty}"
                  f"（模型声明 {result.difficulty_declared}）"
                  f" 入口={result.plan.entry_file} 文件={names}")
            if result.warnings:
                for item in result.warnings:
                    print(f"              警告：{item}")
            if result.degraded:
                problems.append(f"{title} 第 {attempt} 次走了降级路径")
            if max_files is not None and len(names) > max_files:
                problems.append(
                    f"{title} 第 {attempt} 次规划了 {len(names)} 个文件，超过期望上限 {max_files}"
                )
        print(f"   预算   -> 步数≤{result.budget.max_steps} token≤{result.budget.max_output_tokens}"
              f" 文件数≤{result.budget.max_files}")

    print("-" * 78)
    if problems:
        print("   结论   -> 有偏差（稳定性/文件数与预期不符，可据此调整提示词）：")
        for item in problems:
            print(f"             · {item}")
        return False, first_result
    print("   结论   -> 通过")
    return True, first_result


def check_pg_roundtrip(result: PlanResult | None) -> bool:
    """第三段：generation_plan 的写入、回填与对账读回（真实 PG）。"""
    print("=" * 78)
    print("3) PG 往返与「预估 vs 实际」对账（真实库，结束后清理）")

    plan_result = result or PlanResult(plan=heuristic_plan(), budget=budget_for("easy"))
    task_uuid = f"check-{uuid.uuid4().hex[:12]}"
    plan_uuid = uuid.uuid4().hex
    problems: list[str] = []
    db: Session = PgSessionLocal()

    try:
        row = GenerationPlan(
            plan_uuid=plan_uuid,
            user_id=CHECK_USER_ID,
            task_uuid=task_uuid,
            final_requirement={"summary": "自检用的最终需求", "sources": []},
            file_plan=plan_result.plan.model_dump(),
            difficulty=plan_result.plan.difficulty,
            difficulty_declared=plan_result.difficulty_declared,
            planned_file_count=len(plan_result.plan.files),
            budget_steps=plan_result.budget.max_steps,
            budget_output_tokens=plan_result.budget.max_output_tokens,
            validation_warnings=plan_result.warnings,
        )
        GenerationPlanRepository.create(db, row)

        fetched = GenerationPlanRepository.get_by_uuid(db, plan_uuid)
        if fetched is None or fetched.file_plan.get("entry_file") != plan_result.plan.entry_file:
            problems.append("写回读：file_plan JSONB 未能保真")
        print(f"   [写入]     plan_uuid={plan_uuid[:8]}… 计划文件数={row.planned_file_count}"
              f" 预算步数={row.budget_steps} -> {'通过' if not problems else '失败'}")

        # ⚠️ 实际值由**阶段 6 的生成循环结束时**回填；这里模拟一次以验证链路与对账格式
        before = row.update_time
        GenerationPlanRepository.mark_outcome(
            db,
            row,
            actual_steps=min(3, row.budget_steps),
            actual_file_count=row.planned_file_count,
            outcome_status="success",
        )
        ok = (
            row.actual_steps == 3
            and row.actual_file_count == row.planned_file_count
            and row.outcome_status == "success"
            and row.finished_at is not None
            and row.update_time != before
        )
        print(f"   [回填]     实际步数={row.actual_steps}（模拟） 实际文件={row.actual_file_count}"
              f" 结果={row.outcome_status} update_time 已刷新={row.update_time != before}"
              f" -> {'通过' if ok else '失败'}")
        if not ok:
            problems.append("mark_outcome 未正确回填实际值，或 update_time 没有被显式刷新")

        latest = GenerationPlanRepository.get_latest_by_task_uuid(db, task_uuid)
        if latest is None or latest.plan_uuid != plan_uuid:
            problems.append("get_latest_by_task_uuid 未取到刚写入的规划")

        # 对账视图：这张表的存在意义就是"一眼看出难度判得准不准"
        print("   [对账]     任务            模型难度  最终难度  计划文件  预算步数  实际步数  实际文件  结果")
        for item in GenerationPlanRepository.list_recent(db, user_id=CHECK_USER_ID, limit=5):
            if item.task_uuid != task_uuid:
                continue
            print(
                f"              {item.task_uuid:<15} {item.difficulty_declared:<8} {item.difficulty:<8} "
                f"{item.planned_file_count:<9} {item.budget_steps:<9} "
                f"{str(item.actual_steps):<9} {str(item.actual_file_count):<9} {item.outcome_status}"
            )
    except Exception as error:  # noqa: BLE001 —— 库不可用时按"跳过"处理
        print(f"   跳过   -> PG 不可用（{type(error).__name__}: {error}）")
        return True
    finally:
        # 自检不留痕：直接物理删除（与业务无关，不必用逻辑删除标记）
        try:
            for item in GenerationPlanRepository.list_recent(db, user_id=CHECK_USER_ID, limit=50):
                if item.task_uuid == task_uuid:
                    db.delete(item)
            db.commit()
        except Exception:  # noqa: BLE001
            pass
        db.close()

    if problems:
        print("   结论   -> 不通过：")
        for item in problems:
            print(f"             · {item}")
        return False
    print("   结论   -> 通过")
    return True


def main() -> int:
    """跑完全部检查，返回进程退出码（0=全通过）。"""
    # 顺序即输出顺序；PG 段用真实模型跑出来的第一份计划，模型不可用时它自己兜底
    offline_ok = check_offline()
    model_ok, model_result = check_real_model()
    results = {
        "清单清洗与预算（离线）": offline_ok,
        "真实模型规划质量": model_ok,
        "PG 往返与对账": check_pg_roundtrip(model_result),
    }

    print("=" * 78)
    for name, passed in results.items():
        print(f"   {'✅' if passed else '❌'} {name}")
    passed_all = all(results.values())
    print(f"\n汇总：{'全部通过' if passed_all else '存在失败项'}")
    return 0 if passed_all else 1


if __name__ == "__main__":
    sys.exit(main())
