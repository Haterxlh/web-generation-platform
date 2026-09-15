"""阶段 5 的离线测试（二）：交付规划节点（plan-agent）。

用假链替代模型，重点验证**三道 Python 硬保证**（它们都是"真跑模型时很难观察、
但出错代价很大"的地方）：

- **文件名必须过白名单**：模型给过 `styles.css` 与带路径的名字，文件名不能由它说了算；
  非法名要逐个丢弃并记警告，而不是让整次规划失败；
- **难度只能上调**：低报难度会让循环半路被预算掐断；
- **必须有兜底计划**：规划失败或文件全非法时退化为单文件 `index.html` 并标降级 ——
  生成链路不该因为"规划这一步失败"整体失败。
"""

import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agents.common import DEFAULT_DIFFICULTY
from app.agents.plan.plan_agent import (
    FALLBACK_ENTRY,
    MAX_PLANNED_FILES,
    PlanResult,
    build_plan_chain,
    heuristic_plan,
    plan,
    validate_plan,
)
from app.agents.state import FilePlan, FinalRequirement, PlannedFile, RequirementSlots
from app.utils.weg_gen.prompt_loader import load_prompt


def _raw(input_tokens: int = 200, output_tokens: int = 60) -> AIMessage:
    return AIMessage(
        content="",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )


def _chain(parsed: object, raw: AIMessage | None = None) -> RunnableLambda:
    return RunnableLambda(lambda _payload: {"raw": raw, "parsed": parsed})


def _boom_chain(message: str = "模型服务超时") -> RunnableLambda:
    def _boom(_payload: object) -> object:
        raise RuntimeError(message)

    return RunnableLambda(_boom)


def _file(name: str, role: str = "markup", **kwargs: object) -> PlannedFile:
    return PlannedFile(name=name, role=role, **kwargs)  # type: ignore[arg-type]


def _plan(files: list[PlannedFile], **kwargs: object) -> FilePlan:
    payload: dict = {"difficulty": "medium", "entry_file": files[0].name if files else "index.html"}
    payload.update(kwargs)
    return FilePlan(files=files, **payload)  # type: ignore[arg-type]


REQUIREMENT = FinalRequirement(
    summary="做一个待办清单页面，支持添加与删除",
    slots=RequirementSlots(site_kind="单页展示", features=["添加待办"]),
    constraints=["必须响应式"],
    uncertainty=["是否需要登录？"],
)


# --------------------------------------------------------------------------
# 1. 正常路径
# --------------------------------------------------------------------------


def test_plan_passes_through_valid_files() -> None:
    """合法计划原样通过：文件、角色、入口、约束、资源都在，难度与预算对齐。"""
    declared = _plan(
        [
            _file("index.html", "markup", summary="页面结构"),
            _file("style.css", "style", depends_on=["index.html"]),
            _file("app.js", "script", depends_on=["index.html"]),
        ],
        difficulty="medium",
        tech_constraints=["只用原生 JS"],
        assets=["Google Fonts: Inter"],
    )

    result = plan(REQUIREMENT, chain=_chain(declared, _raw(input_tokens=333)))

    assert result.degraded is False
    assert result.plan.names() == ["index.html", "style.css", "app.js"]
    assert result.plan.entry_file == "index.html"
    assert result.plan.tech_constraints == ["只用原生 JS"]
    assert result.plan.assets == ["Google Fonts: Inter"]
    assert result.difficulty_declared == "medium"
    assert result.budget.difficulty == "medium"
    assert result.usage.input_tokens == 333


def test_message_contains_requirement_and_uncertainty_hint() -> None:
    """规划要看得到最终需求；有未确认项时要提醒保守处理。"""
    seen: list[str] = []

    def _record(payload: list) -> dict:
        seen.append(payload[-1].content)
        return {"raw": _raw(), "parsed": _plan([_file("index.html")])}

    plan(REQUIREMENT, chain=RunnableLambda(_record))

    text = seen[0]
    assert "做一个待办清单页面" in text
    assert "必须响应式" in text
    assert "不确定项" in text and "不要为这些点额外增加文件" in text


# --------------------------------------------------------------------------
# 2. 文件名白名单（第一道硬保证）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_name",
    ["../evil.html", "js/app.js", "样式.css", "my page.html", "index.html.bak/../x", "", "  "],
)
def test_invalid_file_names_are_dropped(bad_name: str) -> None:
    """非法文件名逐个丢弃并记警告，合法的不受影响。"""
    result = plan(
        REQUIREMENT,
        chain=_chain(_plan([_file("index.html"), _file(bad_name, "style")])),
    )

    assert result.plan.names() == ["index.html"]
    assert any("不合法" in item for item in result.warnings)
    assert result.degraded is False, "只要还有合法文件就不算降级"


def test_duplicate_names_are_merged() -> None:
    """同名文件出现两次会让门禁与落盘产生歧义，必须合并。"""
    result = plan(
        REQUIREMENT,
        chain=_chain(_plan([_file("index.html"), _file("index.html", "style")])),
    )

    assert result.plan.names() == ["index.html"]
    assert any("重复出现" in item for item in result.warnings)


def test_dangling_dependencies_are_removed() -> None:
    """悬空依赖（指向不存在或被丢弃的文件）要剔除，但**文件本身保留**。"""
    result = plan(
        REQUIREMENT,
        chain=_chain(
            _plan([_file("index.html"), _file("app.js", "script", depends_on=["missing.css"])])
        ),
    )

    assert result.plan.names() == ["index.html", "app.js"]
    assert result.plan.files[1].depends_on == []
    assert any("依赖指向" in item for item in result.warnings)


def test_file_list_is_capped() -> None:
    """文件数超上限要截断（再多的文件不是"规划得更细"，而是更容易缺件）。"""
    many = [_file(f"page{index}.html") for index in range(MAX_PLANNED_FILES + 3)]

    result = plan(REQUIREMENT, chain=_chain(_plan(many)))

    assert len(result.plan.files) == MAX_PLANNED_FILES
    assert any("超过上限" in item for item in result.warnings)


def test_entry_file_fallback_to_markup_file() -> None:
    """入口不在清单里时改用第一个 markup 文件（否则预览打不开任何东西）。"""
    result = plan(
        REQUIREMENT,
        chain=_chain(
            _plan([_file("style.css", "style"), _file("main.html", "markup")], entry_file="index.html")
        ),
    )

    assert result.plan.entry_file == "main.html"
    assert any("入口文件" in item for item in result.warnings)


def test_entry_file_fallback_when_no_markup() -> None:
    """连 markup 都没有时退到第一个文件（比"没有入口"强）。"""
    result = plan(
        REQUIREMENT,
        chain=_chain(_plan([_file("data.json", "data")], entry_file="index.html")),
    )

    assert result.plan.entry_file == "data.json"


# --------------------------------------------------------------------------
# 3. 难度只能上调（第二道硬保证）
# --------------------------------------------------------------------------


def test_declared_low_difficulty_is_escalated() -> None:
    """⚠️ 声明 easy 却交 3 个文件 → 上调到 medium，并保留模型的原始声明。"""
    result = plan(
        REQUIREMENT,
        chain=_chain(
            _plan(
                [
                    _file("index.html"),
                    _file("style.css", "style"),
                    _file("app.js", "script"),
                ],
                difficulty="easy",
            )
        ),
    )

    assert result.plan.difficulty == "medium"
    assert result.difficulty_declared == "easy", "原始声明要留着，用于对照优化提示词"
    assert result.budget.difficulty == "medium", "预算必须跟着最终难度走"
    assert result.budget.max_files >= 3
    assert any("上调" in item for item in result.warnings)


def test_declared_high_difficulty_is_kept() -> None:
    """声明 hard 但只有 1 个文件 → 不降级（高报只是多给预算）。"""
    result = plan(REQUIREMENT, chain=_chain(_plan([_file("index.html")], difficulty="hard")))

    assert result.plan.difficulty == "hard"
    assert result.difficulty_declared == "hard"


def test_budget_matches_final_difficulty_for_easy() -> None:
    """单文件规划的预算是 easy 档（6 步 / 12k）。"""
    result = plan(REQUIREMENT, chain=_chain(_plan([_file("index.html")], difficulty="easy")))

    assert result.budget.max_steps == 6
    assert result.budget.max_output_tokens == 12_000
    assert result.budget.max_files == 1


# --------------------------------------------------------------------------
# 4. 兜底计划（第三道硬保证）
# --------------------------------------------------------------------------


def test_all_invalid_files_fall_back_to_heuristic_plan() -> None:
    """⚠️ 文件全非法时必须兜底：空清单会让阶段 6 的门禁无从判定。"""
    result = plan(
        REQUIREMENT,
        chain=_chain(_plan([_file("../a.html"), _file("样式.css", "style")], difficulty="hard")),
    )

    assert result.degraded is True
    assert result.plan.names() == [FALLBACK_ENTRY]
    assert result.plan.difficulty == "easy"
    assert result.budget.difficulty == "easy"
    assert result.difficulty_declared == "hard", "原始声明照记，便于发现模型老给非法名"
    assert any("全部不合法" in item for item in result.warnings)


def test_model_failure_falls_back() -> None:
    """调用失败 → 兜底单文件计划 + 降级标记，绝不把异常抛给调用方。"""
    result = plan(REQUIREMENT, chain=_boom_chain("模型服务超时"))

    assert isinstance(result, PlanResult)
    assert result.degraded is True
    assert result.plan.names() == [FALLBACK_ENTRY]
    assert any("超时" in item for item in result.warnings)


def test_structured_parse_failure_records_usage() -> None:
    """⚠️ 结构化解析失败也要记账（失败同样烧了 token）。"""
    result = plan(REQUIREMENT, chain=_chain(None, _raw(input_tokens=88)))

    assert result.degraded is True
    assert result.usage.input_tokens == 88
    assert result.plan.names() == [FALLBACK_ENTRY]


def test_heuristic_plan_is_single_file_easy() -> None:
    """兜底计划本身即契约：单文件 index.html + easy，且样式内联。"""
    fallback = heuristic_plan(REQUIREMENT)

    assert fallback.entry_file == FALLBACK_ENTRY
    assert fallback.names() == [FALLBACK_ENTRY]
    assert fallback.difficulty == "easy"
    assert fallback.files[0].role == "markup"
    assert any("内联" in item for item in fallback.tech_constraints)


# --------------------------------------------------------------------------
# 5. 纯函数与契约
# --------------------------------------------------------------------------


def test_validate_plan_is_pure() -> None:
    """清洗不修改入参（便于在编排里复用同一份原始声明做对照）。"""
    declared = _plan([_file("index.html")], difficulty="easy")

    validate_plan(declared)

    assert declared.difficulty == "easy"
    assert declared.names() == ["index.html"]


def test_plan_is_json_serializable() -> None:
    """计划要写进 PG 的 ``generation_plan.file_plan``（JSONB）。"""
    result = plan(REQUIREMENT, chain=_chain(_plan([_file("index.html")])))

    payload = json.dumps(result.plan.model_dump(), ensure_ascii=False)

    assert json.loads(payload)["entry_file"] == "index.html"


def test_plan_prompt_text_lists_deliverables() -> None:
    """给 web-agent 的渲染文本必须列全清单（门禁与交付都按它做）。"""
    rendered = _plan(
        [
            _file("index.html", summary="页面结构"),
            _file("app.js", "script", summary="交互逻辑", depends_on=["index.html"]),
        ]
    ).as_prompt_text()

    assert "【交付清单（缺一不可）】" in rendered
    assert "index.html（markup）：页面结构" in rendered
    assert "app.js（script）：交互逻辑｜依赖：index.html" in rendered


def test_default_difficulty_constant_is_medium() -> None:
    """默认档位是 medium（`PlanResult` 与未知难度的兜底都用它）。"""
    assert DEFAULT_DIFFICULTY == "medium"


def test_build_chain_uses_structured_output() -> None:
    assert build_plan_chain() is not None


def test_prompt_loads_and_forbids_code() -> None:
    """提示词必须能加载，并写明"只规划、不写代码"与文件名规则。"""
    content = load_prompt("plan_agent_system")

    assert "规划，不是写代码" in content
    assert "单层文件名" in content
    assert "优先选择文件数更少" in content, "要防止模型为显得周全而堆砌文件"
