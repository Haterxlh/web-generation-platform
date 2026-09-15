# app/agents/plan/plan_agent.py —— 交付规划节点（FinalRequirement → FilePlan）
#
# 职责（docs/agent_refactor_plan.md 阶段 5）：产出一份**事先声明的交付清单**与难度。
#
# 三条 Python 侧的硬保证（"判定权归模型、否决权归 Python"在这里的落点）：
#
# 1. **文件名必须过白名单**：模型给过 `styles.css` 与带路径的名字（实测），
#    文件名不能由它说了算。非法名逐个丢弃并记警告，而不是让整次规划失败。
# 2. **难度只能上调**：模型把 hard 判成 easy，结果是循环跑一半被预算掐断、用户拿到失败；
#    反过来只是多花一点钱。所以取"模型判定"与"文件数推算"的较高者（见 common.resolve_difficulty）。
# 3. **必须有兜底计划**：规划失败或文件全非法时退化为单文件 `index.html` 并标降级 ——
#    生成链路不该因为"规划这一步失败"整体失败（§3.8.6 失败矩阵）。
#
# ⚠️ 规划**只出"做什么"，不出代码**：否则这一步变成一次昂贵的预生成，还会与 web-agent 打架。

import logging

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from app.agents.common import (
    DEFAULT_DIFFICULTY,
    ModelUsage,
    StageBudget,
    budget_for,
    resolve_difficulty,
)
from app.agents.state import FilePlan, FinalRequirement, PlannedFile
from app.core.llm_client import llm_structured_client
from app.utils.weg_gen.file_writer import UnsafeFileNameError, safe_name
from app.utils.weg_gen.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

PROMPT_NAME = "plan_agent_system"

# 兜底计划的入口文件名（与既有 preview 约定一致）
FALLBACK_ENTRY = "index.html"

# 清单条数上限：再多的文件也不是"规划得更细"，而是更容易缺件
MAX_PLANNED_FILES = 8


class PlanResult(BaseModel):
    """规划节点的返回值。

    Attributes:
        plan: 清洗并复核过的计划（文件名、难度、入口都已由 Python 校正）。
        difficulty_declared: **模型原始声明的难度**（用于与最终难度对照，优化提示词时是硬证据）。
        budget: 由最终难度决定的执行预算（阶段 6 的 web-agent 消费它）。
        degraded: 是否走了降级路径（调用失败 / 结构解析失败 / 文件全非法）。
        usage: 本次调用消耗的 token。
        warnings: 非致命问题（丢弃的文件名、难度上调、悬空依赖……）。
    """

    model_config = {"arbitrary_types_allowed": True}

    plan: FilePlan
    difficulty_declared: str = DEFAULT_DIFFICULTY
    budget: StageBudget = Field(default_factory=lambda: budget_for(DEFAULT_DIFFICULTY))
    degraded: bool = False
    usage: ModelUsage = Field(default_factory=ModelUsage)
    warnings: list[str] = Field(default_factory=list)


def build_plan_chain(model: Runnable | None = None) -> Runnable:
    """构造规划链（结构化输出 + 保留原始 AIMessage 以便记账）。

    Args:
        model: 可注入的模型（测试用；默认用全局 `llm_structured_client`）。
            结构化输出依赖强制 tool_choice，与思考模式互斥（设计约定 §7.3）。

    Returns:
        输入 `list[BaseMessage]` → 输出 `{"raw": AIMessage, "parsed": FilePlan|None}`。
    """
    return (model or llm_structured_client).with_structured_output(FilePlan, include_raw=True)


# 模块级只建一次（链本身无状态）
_PLAN = build_plan_chain()


def plan(requirement: FinalRequirement, *, chain: Runnable | None = None) -> PlanResult:
    """把最终需求规划成一份交付清单。

    Args:
        requirement: 已收敛的最终需求（来自 merge 节点）。
        chain: 可注入的链（测试用）。

    Returns:
        `PlanResult`（计划 + 模型原始难度 + 预算 + 降级标记 + 警告）。
    """
    payload = [
        SystemMessage(content=load_prompt(PROMPT_NAME)),
        HumanMessage(content=_build_message(requirement)),
    ]

    try:
        result = (chain or _PLAN).invoke(payload)
    except Exception as error:  # noqa: BLE001 —— 规划失败不能拖垮整条链路
        detail = f"{type(error).__name__}: {error}"[:200]
        logger.warning("plan_agent 调用失败，使用兜底计划：%s", detail)
        fallback = heuristic_plan(requirement)
        return PlanResult(
            plan=fallback,
            difficulty_declared=fallback.difficulty,
            budget=budget_for(fallback.difficulty),
            degraded=True,
            warnings=[f"规划失败，已使用兜底计划（单文件 {FALLBACK_ENTRY}）：{detail}"],
        )

    raw = (result or {}).get("raw")
    usage = ModelUsage.from_message(raw) if raw is not None else ModelUsage()
    declared_plan = (result or {}).get("parsed")

    if declared_plan is None:
        # 结构化解析失败：用量照记（失败同样烧了 token）
        fallback = heuristic_plan(requirement)
        return PlanResult(
            plan=fallback,
            difficulty_declared=fallback.difficulty,
            budget=budget_for(fallback.difficulty),
            degraded=True,
            usage=usage,
            warnings=["规划未按结构化契约返回，已使用兜底计划（单文件 index.html）"],
        )

    cleaned, warnings = validate_plan(declared_plan)
    if not cleaned.files:
        # 文件全被丢弃时必须兜底：一份"空清单"会让阶段 6 的门禁无从判定
        fallback = heuristic_plan(requirement)
        warnings.append("模型给出的文件全部不合法，已使用兜底计划（单文件 index.html）")
        return PlanResult(
            plan=fallback,
            difficulty_declared=declared_plan.difficulty,
            budget=budget_for(fallback.difficulty),
            degraded=True,
            usage=usage,
            warnings=warnings,
        )

    return PlanResult(
        plan=cleaned,
        difficulty_declared=declared_plan.difficulty,
        budget=budget_for(cleaned.difficulty),
        degraded=False,
        usage=usage,
        warnings=warnings,
    )


def validate_plan(declared: FilePlan) -> tuple[FilePlan, list[str]]:
    """清洗并复核模型给出的计划（纯函数，可离线单测）。

    四步，顺序不能乱：

    1. **文件名过白名单**（`safe_name`）：非法名丢弃并记警告；
    2. **去重**：同一个文件名出现两次会让门禁与落盘产生歧义；
    3. **剔除悬空依赖**：`depends_on` 指向不存在的文件时删掉该依赖（保留文件本身）；
    4. **复核难度与入口**：难度按 `resolve_difficulty` 只上调；入口不在清单里就换成
       第一个 markup 文件（再不行用兜底名）。

    Args:
        declared: 模型给出的计划。

    Returns:
        (清洗后的计划, 警告列表)。
    """
    warnings: list[str] = []
    kept: list[PlannedFile] = []
    dropped: list[str] = []

    for item in declared.files[:MAX_PLANNED_FILES]:
        try:
            name = safe_name(item.name)
        except UnsafeFileNameError:
            dropped.append(item.name)
            continue
        if any(existing.name == name for existing in kept):
            warnings.append(f"文件 {name} 在清单里重复出现，已合并为一条")
            continue
        kept.append(item.model_copy(update={"name": name}))

    if dropped:
        warnings.append(
            "以下文件名不合法（含路径/中文/空格等），已从交付清单中丢弃："
            + "、".join(repr(name) for name in dropped)
        )
    if len(declared.files) > MAX_PLANNED_FILES:
        warnings.append(
            f"模型规划了 {len(declared.files)} 个文件，超过上限 {MAX_PLANNED_FILES}，已截断"
        )

    names = {item.name for item in kept}

    # 悬空依赖：指向被丢弃或不存在的文件。删依赖而不是删文件 —— 文件本身仍要交付
    dangling: list[str] = []
    fixed: list[PlannedFile] = []
    for item in kept:
        valid_deps = [dep for dep in item.depends_on if dep in names]
        dangling.extend(dep for dep in item.depends_on if dep not in names)
        fixed.append(item.model_copy(update={"depends_on": valid_deps}))
    if dangling:
        warnings.append(
            "以下依赖指向清单里不存在的文件，已剔除该依赖："
            + "、".join(sorted(set(dangling)))
        )

    entry = _resolve_entry(declared.entry_file, fixed, warnings)
    difficulty, escalation_reason = resolve_difficulty(declared.difficulty, len(fixed))
    if escalation_reason:
        warnings.append(escalation_reason)

    return (
        declared.model_copy(
            update={
                "entry_file": entry,
                "difficulty": difficulty,  # type: ignore[arg-type]
                "files": fixed,
            }
        ),
        warnings,
    )


def _resolve_entry(entry: str, files: list[PlannedFile], warnings: list[str]) -> str:
    """确定入口文件：必须在清单里，否则退化到第一个 markup 文件。"""
    names = [item.name for item in files]
    if entry in names:
        return entry

    markup = next((item.name for item in files if item.role == "markup"), None)
    if markup:
        warnings.append(f"入口文件 {entry or '（未给）'} 不在交付清单里，已改用 {markup}")
        return markup
    if names:
        warnings.append(f"入口文件 {entry or '（未给）'} 不在交付清单里，已改用 {names[0]}")
        return names[0]
    return FALLBACK_ENTRY


def heuristic_plan(requirement: FinalRequirement | None = None) -> FilePlan:
    """兜底计划：单文件 `index.html`（难度 easy）。

    这是**有依据的**兜底，不是随便挑的：单文件模式在本项目里成功率最高
    （原 `single_html_flow` 两次实测均成功，多文件模式最初 0/3），
    而且"一个能打开的完整页面"永远比"三个缺一个的半成品"更有价值。

    Args:
        requirement: 最终需求（当前不参与生成，保留参数是为了将来按需求变量名）。

    Returns:
        easy 难度的单文件计划。
    """
    return FilePlan(
        difficulty="easy",
        entry_file=FALLBACK_ENTRY,
        files=[
            PlannedFile(
                name=FALLBACK_ENTRY,
                role="markup",
                summary="单文件页面：结构与样式内联，保证一次交付即可打开",
            )
        ],
        tech_constraints=["单文件交付：样式与脚本内联在 HTML 中"],
    )


def _build_message(requirement: FinalRequirement) -> str:
    """拼出规划用的用户消息（最终需求已经是收敛过的一份，直接给它）。"""
    parts = [
        "## 任务\n规划这次要交付哪些文件、每个文件负责什么；给出难度与入口文件。",
        "## 最终需求\n" + requirement.as_prompt_text(),
    ]
    if requirement.uncertainty:
        parts.append(
            "## 注意\n需求里有些点尚未确认（见上方【不确定项】）："
            "规划时按保守方案处理，不要为这些点额外增加文件。"
        )
    return "\n\n".join(parts)
