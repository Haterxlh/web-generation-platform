# app/utils/utils_check/check_web_agent.py —— 开发期自检：web-agent 循环与整条流水线（阶段 6）
#
# 运行（在 backend-uv-fastapi 下，必须用 -m 让 app 包可导入）：
#   uv run python -m app.utils.utils_check.check_web_agent
#
# 三段：
#
#   第一段（离线，假模型）：门禁、工具报错改正、三道刹车 —— 不花钱、可随时跑。
#   第二段（⚠️ 真实模型，按 token 计费；不需要 API）：
#        方案的"三层自主性"验收 + **思考 / 非思考两种客户端对照**：
#          ① 是否自主调工具（没有"必须调用工具"的指令，模型会不会主动 write_file）
#          ② 是否自主拆解（是否自己分多轮交付多个文件，而不是要一次性全给）
#          ③ 是否响应失败（**故障注入**：第一次 write_file 必失败，模型是否读懂错误并改正）
#        两个客户端跑**同一份交付清单**，对照步数 / token / 门禁结果，用于确定默认客户端。
#   第三段（端到端，需要 API + worker + Redis 在跑）：真实 HTTP 提交 agent 任务 →
#        轮询阶段 → 终态 → 预览产物，并校验 `generation_plan` 的预估与实际两侧都有值。

import sys
import time
import uuid

import httpx
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from app.agents.plan.plan_agent import plan as run_plan
from app.agents.state import FinalRequirement, RequirementSlots
from app.agents.web import web_agent
from app.agents.web.tools import build_agent_tools
from app.core.pg_db import PgSessionLocal
from app.repositories.agent import GenerationPlanRepository
from app.utils.weg_gen.file_store import FileStore

# ⚠️ Windows 中文控制台默认编码是 GBK，打印 ✅/⚠️ 会 UnicodeEncodeError 崩掉脚本
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

API_BASE = "http://127.0.0.1:8000/api"
PASSWORD = "probe123456"

# 第二段与第三段用的需求（同一句话，便于对照）
PROMPT = (
    "做一个单页的待办清单：能添加、勾选完成、删除条目；"
    "数据存在浏览器 localStorage 里；风格极简白底、圆角卡片、无衬线字体；给普通个人用户用。"
)

REQUIREMENT = FinalRequirement(
    summary=PROMPT,
    slots=RequirementSlots(
        site_kind="单页展示",
        features=["添加待办", "勾选完成", "删除条目"],
        audience="普通个人用户",
        style="极简白底、圆角卡片、无衬线字体",
        need_persistence=True,
    ),
    constraints=["必须响应式"],
)


# ---------------------------------------------------------------------------
# 第一段：离线（假模型）
# ---------------------------------------------------------------------------


def _write(files: dict[str, str], call_id: str = "c1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "write_file", "args": {"files": files}, "id": call_id, "type": "tool_call"}
        ],
    )


class _ScriptedModel:
    """按剧本返回 AIMessage 的假模型（记录每轮收到的消息，用于验证"模型看到了什么"）。"""

    def __init__(self, turns: list[AIMessage]) -> None:
        self.turns = turns
        self.calls = 0
        self.seen: list[list] = []

    def invoke(self, messages: list) -> AIMessage:
        self.seen.append(list(messages))
        self.calls += 1
        index = self.calls - 1
        return self.turns[index] if index < len(self.turns) else AIMessage(content="完成")


def check_offline() -> bool:
    """第一段：门禁、报错改正、刹车（无模型）。"""
    print("=" * 78)
    print("1) 循环行为（离线，假模型）")

    problems: list[str] = []
    from app.agents.state import FilePlan, PlannedFile

    plan_3 = FilePlan(
        difficulty="medium",
        entry_file="index.html",
        files=[
            PlannedFile(name="index.html", role="markup"),
            PlannedFile(name="style.css", role="style"),
            PlannedFile(name="app.js", role="script"),
        ],
    )

    # ① 门禁：只写 1 个文件就宣布完成 → 必须不通过
    result = web_agent.generate(
        plan_3,
        REQUIREMENT,
        model=_ScriptedModel([_write({"index.html": "A"}), AIMessage(content="我完成了")]),
        max_repair_rounds=0,
    )
    ok = (not result.gate_passed) and set(result.missing) == {"style.css", "app.js"}
    print(f"   [门禁]     只写 1/3 → gate_passed={result.gate_passed} missing={result.missing} -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("门禁必须拦住只写了 1 个文件的交付")

    # ② 工具报错后改正：第一次写入必失败，模型必须读到错误并改用合法方式交付
    marks = {"failed": False}

    def _wrapper(store: FileStore, tools: list) -> list:
        """包装工具集：第一次 write_file 直接返回错误，之后委托给**同一个 store** 上的真工具。"""
        real = {item.name: item for item in tools}

        def _flaky(files: dict[str, str]) -> str:
            if not marks["failed"]:
                marks["failed"] = True
                return "写入失败：文件名不合法。请换名后重试"
            return str(real["write_file"].invoke({"files": files}))

        return [
            StructuredTool.from_function(func=_flaky, name="write_file", description="写入文件"),
            real["read_file"],
            real["list_files"],
        ]

    model = _ScriptedModel(
        [
            _write({"../evil.html": "X"}, "c1"),
            _write({"index.html": "A"}, "c2"),
            AIMessage(content="完成"),
        ]
    )
    result = web_agent.generate(
        FilePlan(
            difficulty="easy",
            entry_file="index.html",
            files=[PlannedFile(name="index.html", role="markup")],
        ),
        REQUIREMENT,
        model=model,
        tool_wrapper=_wrapper,
    )
    saw_error = (
        len(model.seen) > 1
        and "写入失败" in "\n".join(str(message.content) for message in model.seen[1])
    )
    ok = result.gate_passed and marks["failed"] and saw_error
    print(f"   [报错改正] 首次注入失败 → 模型看到错误={saw_error}"
          f" gate_passed={result.gate_passed} 步数={result.steps_used} -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("工具报错后模型应能读到错误并改正，最终交付完整")

    # ③ 三道刹车
    turns = [_write({"index.html": "A"}, f"c{index}") for index in range(1, 30)]
    braked = web_agent.generate(
        plan_3, REQUIREMENT, model=_ScriptedModel(turns), max_steps=2
    )
    ok = braked.steps_used == 2 and braked.stop_reason == "max_steps" and braked.files
    print(f"   [步数刹车] steps={braked.steps_used} stop={braked.stop_reason} 保留产物={bool(braked.files)} -> {'通过' if ok else '失败'}")
    if not ok:
        problems.append("步数上限必须生效，且停之前写下的文件不能丢")

    if problems:
        print("   结论   -> 不通过：")
        for item in problems:
            print(f"             · {item}")
        return False
    print("   结论   -> 通过")
    return True


# ---------------------------------------------------------------------------
# 第二段：三层自主性 + 思考/非思考对照（真实模型）
# ---------------------------------------------------------------------------


def _probe_once(*, thinking: bool, plan, inject_failure: bool) -> dict:
    """跑一次真实循环，返回观测数据。

    Args:
        thinking: 用思考模式还是非思考客户端。
        plan: 交付清单（两个客户端共用同一份，保证对照公平）。
        inject_failure: 是否注入"第一次 write_file 必失败"。

    Returns:
        观测结果字典（步数 / 用量 / 门禁 / trace / 是否发生注入失败）。
    """
    marks = {"injected": False}

    def _wrapper(store: FileStore, tools: list) -> list:
        """故障注入：第一次 write_file 返回错误，之后委托给同一个 store 上的真工具。"""
        real = {item.name: item for item in tools}

        def _flaky(files: dict[str, str]) -> str:
            if not marks["injected"]:
                marks["injected"] = True
                return (
                    "写入失败：文件名不合法（不支持路径与非法字符）。"
                    "请改用交付清单里给出的文件名后重试。"
                )
            return str(real["write_file"].invoke({"files": files}))

        return [
            StructuredTool.from_function(
                func=_flaky, name="write_file", description=real["write_file"].description
            ),
            real["read_file"],
            real["list_files"],
        ]

    started = time.perf_counter()
    steps: list[str] = []
    result = web_agent.generate(
        plan,
        REQUIREMENT,
        tool_wrapper=_wrapper if inject_failure else None,
        thinking=thinking,
        on_step=lambda step, detail: steps.append(detail),
    )
    elapsed = time.perf_counter() - started

    return {
        "thinking": thinking,
        "steps": result.steps_used,
        "rounds": result.rounds,
        "gate": result.gate_passed,
        "missing": result.missing,
        "stop_reason": result.stop_reason,
        "usage": result.usage,
        "files": sorted(result.files),
        "seconds": elapsed,
        "injected_failure_seen": marks["injected"],
        "trace_lines": len(result.trace_jsonl.splitlines()),
        "details": steps,
        "warnings": result.warnings,
    }


def check_autonomy() -> bool:
    """第二段：三层自主性 + 两种客户端对照（⚠️ 计费）。"""
    print("=" * 78)
    print("2) 三层自主性 + 思考/非思考对照（⚠️ 真实模型）")

    # 先用真实 plan-agent 规划一次，两个客户端共用结果（对照才公平）
    try:
        plan_result = run_plan(REQUIREMENT)
    except Exception as error:  # noqa: BLE001
        print(f"   跳过   -> 模型不可用（{type(error).__name__}: {error}）")
        return True

    plan = plan_result.plan
    print(f"   规划     -> 难度={plan.difficulty}（模型声明 {plan_result.difficulty_declared}）"
          f" 清单={plan.names()}")
    print(f"   预算     -> 步数≤{plan_result.budget.max_steps} token≤{plan_result.budget.max_output_tokens}")

    observations: list[dict] = []
    for thinking in (True, False):
        label = "思考模式" if thinking else "非思考模式"
        try:
            data = _probe_once(thinking=thinking, plan=plan, inject_failure=True)
        except Exception as error:  # noqa: BLE001
            print(f"   {label} -> 失败（{type(error).__name__}: {error}）")
            continue
        observations.append(data)
        print("-" * 78)
        print(f"   [{label}]（故障注入：第一次写入必失败）")
        print(f"     ① 自主调工具 -> trace {data['trace_lines']} 轮，写入文件 {data['files']}")
        print(f"     ② 自主拆解   -> 步数={data['steps']} 轮数={data['rounds']}"
              f"（清单 {len(plan.files)} 个文件）")
        print(f"     ③ 响应失败   -> 注入失败={data['injected_failure_seen']}"
              f" 最终门禁={'通过' if data['gate'] else '未通过'}"
              f"{'，缺 ' + '、'.join(data['missing']) if data['missing'] else ''}")
        print(f"     结果         -> stop={data['stop_reason']} 耗时={data['seconds']:.1f}s"
              f" token in={data['usage'].input_tokens} out={data['usage'].output_tokens}"
              f" reasoning={data['usage'].reasoning_tokens}")
        for detail in data["details"][:8]:
            print(f"       · {detail}")
        if data["warnings"]:
            for item in data["warnings"]:
                print(f"       ⚠ {item}")

    if not observations:
        print("   结论   -> 无法评估（模型不可用）")
        return True

    print("-" * 78)
    print("   对照表：")
    print("     客户端       门禁   步数  轮数  输入token  输出token  思考token  耗时")
    for data in observations:
        print(
            f"     {'思考' if data['thinking'] else '非思考':<10} "
            f"{'✅' if data['gate'] else '❌':<5} {data['steps']:<5} {data['rounds']:<5} "
            f"{data['usage'].input_tokens:<10} {data['usage'].output_tokens:<10} "
            f"{data['usage'].reasoning_tokens:<10} {data['seconds']:.1f}s"
        )

    passed = [data for data in observations if data["gate"]]
    if not passed:
        print("   结论   -> 两种客户端都没能交付完整清单（看上面的停止原因与警告）")
        return False
    if len(passed) == 1:
        best = passed[0]
        print(f"   结论   -> 只有「{'思考' if best['thinking'] else '非思考'}模式」交付完整；"
              f"建议默认沿用该模式")
        return True

    cheaper = min(passed, key=lambda item: item["usage"].output_tokens)
    print(f"   结论   -> 两种都交付完整；输出 token 更省的是"
          f"「{'思考' if cheaper['thinking'] else '非思考'}模式」"
          f"（{cheaper['usage'].output_tokens}），可作为默认客户端的依据")
    return True


# ---------------------------------------------------------------------------
# 第三段：端到端（需要 API + worker）
# ---------------------------------------------------------------------------


def check_end_to_end() -> bool:
    """第三段：真实 HTTP 提交 agent 任务 → 轮询阶段 → 预览（需要 API 与 worker 在跑）。"""
    print("=" * 78)
    print("3) 端到端：create(agent) → 轮询阶段 → 产物预览（需要 API + worker）")

    try:
        with httpx.Client(timeout=120) as client:
            account = f"wagent{uuid.uuid4().hex[:10]}"
            client.post(
                f"{API_BASE}/user/register",
                json={"user_account": account, "user_password": PASSWORD},
            ).raise_for_status()
            token = client.post(
                f"{API_BASE}/user/login",
                json={"user_account": account, "user_password": PASSWORD},
            ).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            created = client.post(
                f"{API_BASE}/generation/create",
                headers=headers,
                json={"prompt": PROMPT, "gen_type": "agent"},
            )
            created.raise_for_status()
            task_uuid = created.json()["task_uuid"]
            print(f"   提交     -> task_uuid={task_uuid[:12]}… stage={created.json()['stage']}")

            seen_stages: list[str] = []
            detail = {}
            deadline = time.time() + 420
            while time.time() < deadline:
                detail = client.get(
                    f"{API_BASE}/generation/{task_uuid}", headers=headers
                ).json()
                stage = detail.get("stage")
                if not seen_stages or seen_stages[-1] != stage:
                    seen_stages.append(stage)
                    print(f"   轮询     -> stage={stage}（{detail.get('stage_text')}）"
                          f" progress={detail.get('progress')}%"
                          f" detail={detail.get('stage_detail') or ''}")
                if detail.get("status") in ("success", "failed"):
                    break
                time.sleep(2)

            print(f"   终态     -> status={detail.get('status')} stage={detail.get('stage')}"
                  f" 文件={detail.get('file_list')} 耗时={detail.get('duration_ms')}ms")
            print(f"   阶段序列 -> {seen_stages}")

            problems: list[str] = []
            if detail.get("status") != "success":
                problems.append(f"任务未成功：{detail.get('error_msg') or detail.get('stage_text')}")
            if not detail.get("file_list"):
                problems.append("成功任务却没有产物文件")
            if "generating" not in seen_stages:
                problems.append("轮询从未看到 generating 阶段（阶段上报没接上）")

            # 预览：签发票据后打开入口文件
            if detail.get("status") == "success":
                ticket = client.post(
                    f"{API_BASE}/generation/{task_uuid}/preview-ticket", headers=headers
                )
                ticket.raise_for_status()
                entry = detail.get("preview_url") or f"/preview/{ticket.json().get('result_dir')}/index.html"
                preview = client.get(f"http://127.0.0.1:8000{entry}")
                print(f"   预览     -> {entry} HTTP {preview.status_code}（{len(preview.text)} 字符）")
                if preview.status_code != 200:
                    problems.append(f"预览入口返回 {preview.status_code}")

            # 对账：generation_plan 必须同时有预估与实际
            pg = PgSessionLocal()
            try:
                row = GenerationPlanRepository.get_latest_by_task_uuid(pg, task_uuid)
            finally:
                pg.close()
            if row is None:
                problems.append("没有 generation_plan 记录（规划侧没落库）")
            else:
                print(f"   对账     -> 模型难度={row.difficulty_declared} 最终难度={row.difficulty}"
                      f" 计划文件={row.planned_file_count} 预算步数={row.budget_steps}"
                      f" | 实际步数={row.actual_steps} 实际文件={row.actual_file_count}"
                      f" 结果={row.outcome_status}")
                if row.actual_steps is None or row.outcome_status is None:
                    problems.append("generation_plan 的实际侧没有回填（预估 vs 实际对账缺一半）")

            if problems:
                print("   结论   -> 不通过：")
                for item in problems:
                    print(f"             · {item}")
                return False
            print("   结论   -> 通过")
            return True
    except httpx.HTTPError as error:
        print(f"   跳过   -> API 不可用（{type(error).__name__}）；"
              f"先执行 `uv run fastapi dev` 与 `arq app.core.worker.WorkerSettings` 再重跑本段")
        return True


def check_pipeline_in_process() -> bool:
    """第四段：**进程内**跑一次完整 agent 流水线（真模型 + 真 MySQL/PG + 真落盘）。

    为什么需要这一段：HTTP 段要求 API 与 worker **两个进程都是新代码** ——
    而 arq worker 不会热重载（FastAPI dev 会）。这一段把"编排 → 落库 → 落盘 → 回填实际值"
    整条链路验证掉，剩下的缺口只有"队列与接口"那一层。

    ⚠️ 会真实调用模型（一次完整流水线约 5~8 次调用），并写真实数据；脚本结束前全部清理。
    """
    print("=" * 78)
    print("4) 进程内流水线（真模型 + 真库 + 真落盘；结束后清理）")

    from sqlalchemy import delete, text

    from app.core.mysql_db import MysqlSessionLocal
    from app.models.generation_task import GenerationTask
    from app.repositories.generation_repository import GenerationTaskRepository
    from app.services.agent_generation_service import AgentGenerationService
    from app.utils.weg_gen.file_writer import task_dir

    db = MysqlSessionLocal()
    # 探针用一个**真实存在的用户 id**：检索层会拒绝非正数 user_id（越权防线，阶段 4 建），
    # 所以不能图省事用 0 —— 那正是这道防线存在的意义。
    probe_user_id = db.execute(
        text("SELECT id FROM user WHERE isDelete = 0 ORDER BY id LIMIT 1")
    ).scalar()
    if probe_user_id is None:
        print("   跳过   -> 库里没有可用用户（先注册一个账号再跑本段）")
        db.close()
        return True

    task_uuid = f"checkpipe{uuid.uuid4().hex[:10]}"
    problems: list[str] = []
    try:
        task = GenerationTask(
            task_uuid=task_uuid,
            user_id=int(probe_user_id),
            prompt=PROMPT,
            gen_type="agent",
            session_uuid=None,
            status="running",
        )
        task = GenerationTaskRepository.create(db, task)
        print(f"   建任务   -> {task_uuid}（探针用户 {probe_user_id}）")

        started = time.perf_counter()
        AgentGenerationService.execute(db, task, started)
        db.refresh(task)

        print(f"   终态     -> status={task.status} 阶段={task.stage}"
              f" 文件={task.file_list} 耗时={task.duration_ms}ms")
        print(f"   用量     -> in={task.input_tokens} out={task.output_tokens}"
              f" reasoning={task.reasoning_tokens}")
        print(f"   阶段明细 -> {task.stage_detail or '（无）'}")

        if task.status != "success":
            problems.append(f"任务未成功：{task.error_msg or task.stage_detail}")
        if not task.file_list:
            problems.append("成功任务却没有产物文件")
        else:
            import json as _json

            names = _json.loads(task.file_list)
            directory = task_dir(int(probe_user_id), task_uuid)
            missing_on_disk = [name for name in names if not (directory / name).is_file()]
            print(f"   产物     -> 目录={task.result_dir} 文件={names}"
                  f" 缺盘={missing_on_disk or '无'}")
            if missing_on_disk:
                problems.append(f"产物没有真的落盘：{missing_on_disk}")
            trace = directory / "_debug_trace.jsonl"
            print(f"   trace    -> {'有' if trace.is_file() else '无'}"
                  f"（{trace.stat().st_size if trace.is_file() else 0} 字节）")

        # 对账：预估与实际两侧都必须在
        pg = PgSessionLocal()
        try:
            row = GenerationPlanRepository.get_latest_by_task_uuid(pg, task_uuid)
            if row is None:
                problems.append("没有 generation_plan 记录")
            else:
                print(f"   对账     -> 模型难度={row.difficulty_declared} 最终难度={row.difficulty}"
                      f" 计划文件={row.planned_file_count} 预算步数={row.budget_steps}"
                      f" | 实际步数={row.actual_steps} 实际文件={row.actual_file_count}"
                      f" 结果={row.outcome_status}")
                if row.actual_steps is None or row.outcome_status is None:
                    problems.append("generation_plan 的实际侧没有回填")
        finally:
            pg.close()
    except Exception as error:  # noqa: BLE001 —— 模型/库不可用时按跳过处理
        print(f"   跳过   -> 运行失败（{type(error).__name__}: {error}）")
        return True
    finally:
        # 自检不留痕：任务行、规划行、产物目录全部清掉
        try:
            db.execute(delete(GenerationTask).where(GenerationTask.task_uuid == task_uuid))
            db.commit()
        except Exception:  # noqa: BLE001
            pass
        try:
            pg = PgSessionLocal()
            try:
                for item in GenerationPlanRepository.list_recent(
                    pg, user_id=int(probe_user_id), limit=50
                ):
                    if item.task_uuid == task_uuid:
                        pg.delete(item)
                pg.commit()
            finally:
                pg.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            import shutil

            shutil.rmtree(task_dir(int(probe_user_id), task_uuid), ignore_errors=True)
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
    results = {
        "循环行为（离线）": check_offline(),
        "三层自主性与客户端对照": check_autonomy(),
        "进程内流水线（真库真模型）": check_pipeline_in_process(),
        "端到端（HTTP + worker）": check_end_to_end(),
    }

    print("=" * 78)
    for name, passed in results.items():
        print(f"   {'✅' if passed else '❌'} {name}")
    passed_all = all(results.values())
    print(f"\n汇总：{'全部通过' if passed_all else '存在失败项'}")
    return 0 if passed_all else 1


if __name__ == "__main__":
    sys.exit(main())
