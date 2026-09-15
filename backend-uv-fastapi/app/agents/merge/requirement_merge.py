# app/agents/merge/requirement_merge.py —— 需求归并节点（对话摘要 + 四来源冲突消解）
#
# 职责（docs/agent_refactor_plan.md §3.5 / §3.8.3）：产出**唯一一份** ``FinalRequirement``。
#
# 本阶段（阶段 3）只实现与单测，**到阶段 6 才接进编排图** —— 因为它的最后一个输入
# （RAG 检索结果）要到阶段 4 才出现，而它的消费者（plan-agent / web-agent）在阶段 5/6。
#
# 三件必须由 Python 完成、不能交给模型的事：
#
# 1. **sources（来源证据）由 Python 生成**：它记录的是"这句话从哪来"这类**事实**，
#    让模型复述只会得到一份看起来合理但可能漏项的清单 —— 而漏项意味着"某条需求没有出处"，
#    事后根本无法追责。
# 2. **风格的结构化取值由 Python 汇总**：它们是文档解析的实测值（``#0f172a``），
#    模型转述会失真（"看起来像 #1e293b"）。模型只写 ``style_spec.notes`` 这类意图。
# 3. **槽位只增不减**：模型可以在文档里发现新信息（补 ``need_persistence``），
#    但**不能把用户已经说过的槽位清空** —— 复用 ``RequirementSlots.merged_with`` 的语义。
#
# 而"是否采用文档风格"这件事**交回给模型**（``use_document_style``）：
# 用户可能明确说"不要文档里的配色"。判定权归模型，Python 只在它说"要"时提供事实、
# 说"不要"时**真的不注入**设计令牌（否则用户的话会被静默忽略）。

import logging
from collections.abc import Sequence

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from app.agents.common import ModelUsage
from app.agents.state import (
    FinalRequirement,
    RagResult,
    RequirementDigest,
    RequirementSlots,
    RequirementSource,
    StyleSpec,
)
from app.core.llm_client import llm_structured_client
from app.utils.weg_gen.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

PROMPT_NAME = "requirement_merge_system"

# 来源标识在证据里最多保留多少字符（用户原话可能很长）
SOURCE_REF_CHARS = 60


class MergeDecision(BaseModel):
    """模型在 merge 阶段**能决定**的字段。

    刻意不含 ``sources``：那是 Python 侧的事实（见模块头注释）。
    """

    summary: str = Field(default="", description="一段人可读的需求陈述")
    slots: RequirementSlots = Field(default_factory=RequirementSlots, description="合并后的槽位")
    content_points: list[str] = Field(default_factory=list, description="最终内容素材")
    style_spec: StyleSpec = Field(default_factory=StyleSpec, description="风格（只写 notes）")
    constraints: list[str] = Field(default_factory=list, description="最终硬约束")
    uncertainty: list[str] = Field(default_factory=list, description="不确定项")
    use_document_style: bool = Field(
        default=True, description="是否采用上传文档的风格（用户明确说不要时为 false）"
    )


class MergeResult(BaseModel):
    """merge 节点的返回值。

    Attributes:
        requirement: 收敛后的最终需求。
        degraded: 是否走了降级路径（模型失败 → Python 直接合并）。
        usage: 本次调用消耗的 token。
        warnings: 非致命问题（降级原因、被忽略的设计令牌等）。
    """

    model_config = {"arbitrary_types_allowed": True}

    requirement: FinalRequirement
    degraded: bool = False
    usage: ModelUsage = Field(default_factory=ModelUsage)
    warnings: list[str] = Field(default_factory=list)


def build_merge_chain(model: Runnable | None = None) -> Runnable:
    """构造 merge 链（结构化输出 + 保留原始 AIMessage 以便记账）。

    Args:
        model: 可注入的模型（测试用；默认用全局 `llm_structured_client`）。
            结构化输出依赖强制 tool_choice，与思考模式互斥（设计约定 §7.3）。

    Returns:
        输入 `list[BaseMessage]` → 输出 `{"raw": AIMessage, "parsed": MergeDecision|None}`。
    """
    return (model or llm_structured_client).with_structured_output(
        MergeDecision, include_raw=True
    )


# 模块级只建一次（链本身无状态）
_MERGE = build_merge_chain()


def merge(
    *,
    user_message: str,
    slots: RequirementSlots | None = None,
    draft_summary: str = "",
    digests: Sequence[tuple[str, RequirementDigest]] = (),
    rag: RagResult | None = None,
    chain: Runnable | None = None,
) -> MergeResult:
    """把四个来源归并成一份最终需求。

    Args:
        user_message: 用户本轮原话（优先级最高，永不裁）。
        slots: 会话已累积的槽位（对话澄清摘要的结构化形态）。
        draft_summary: 会话草稿的一句话摘要（对话澄清摘要的文字形态）。
        digests: ``(来源标识, digest)`` 列表；来源标识一般是 ``@doc1`` 或文件名。
        rag: 个人知识库检索结果（阶段 4 起可能非空，一期通常是 ``skipped`` 或 ``miss``）。
            ⚠️ 刻意收**三态对象**而不是一个文本片段：``miss``（需要但没查到）
            必须变成不确定项，``skipped``（本来就不需要）必须什么都不加 ——
            一个字符串无法区分这两件事。
        chain: 可注入的链（测试用）。

    Returns:
        `MergeResult`（最终需求 + 是否降级 + 用量 + 警告）。
    """
    current_slots = slots or RequirementSlots()
    sources = _build_sources(user_message, draft_summary, digests, rag)
    digest_uncertainty = [
        f"{ref}：{item}" for ref, digest in digests for item in digest.open_questions
    ]

    payload = [
        SystemMessage(content=load_prompt(PROMPT_NAME)),
        HumanMessage(
            content=_build_merge_message(
                user_message, current_slots, draft_summary, digests, rag
            )
        ),
    ]

    warnings: list[str] = []
    if rag is not None:
        warnings.extend(rag.warnings)
    try:
        result = (chain or _MERGE).invoke(payload)
    except Exception as error:  # noqa: BLE001 —— 归并失败不能拖垮整条链路
        detail = f"{type(error).__name__}: {error}"[:200]
        logger.warning("requirement_merge 调用失败，走 Python 合并：%s", detail)
        return _degraded_result(
            user_message=user_message,
            slots=current_slots,
            digests=digests,
            rag=rag,
            sources=sources,
            digest_uncertainty=digest_uncertainty,
            reason=f"merge 调用失败：{detail}",
            usage=ModelUsage(),
        )

    raw = (result or {}).get("raw")
    usage = ModelUsage.from_message(raw) if raw is not None else ModelUsage()
    decision = (result or {}).get("parsed")

    if decision is None:
        # 结构化解析失败：用量照记（失败同样烧了 token）
        return _degraded_result(
            user_message=user_message,
            slots=current_slots,
            digests=digests,
            rag=rag,
            sources=sources,
            digest_uncertainty=digest_uncertainty,
            reason="merge 结构化解析失败",
            usage=usage,
        )

    style_spec = _resolve_style(decision, digests, warnings)
    requirement = FinalRequirement(
        summary=decision.summary.strip(),
        # 只增不减：模型可以在文档里发现新信息，但不能清空用户说过的槽位
        slots=current_slots.merged_with(decision.slots),
        content_points=_dedupe(decision.content_points),
        style_spec=style_spec,
        constraints=_dedupe(decision.constraints),
        sources=sources,
        # 文档里"没说清的点"与"知识库没查到的资料"都必须一路传下去 ——
        # 模型可能没把它们写进 uncertainty，但这正是最不能让下游凭空补全的两类信息
        uncertainty=_dedupe(
            [*decision.uncertainty, *digest_uncertainty, *_rag_uncertainty(rag)]
        ),
    )
    return MergeResult(requirement=requirement, degraded=False, usage=usage, warnings=warnings)


# ==================== Python 侧的事实与兜底 ====================


def _resolve_style(
    decision: MergeDecision, digests: Sequence[tuple[str, RequirementDigest]], warnings: list[str]
) -> StyleSpec:
    """决定最终风格规范：事实（令牌）由 Python 填，意图（notes）由模型写。

    Args:
        decision: 模型判定。
        digests: 各文档的理解结果。
        warnings: 警告列表（会就地追加）。

    Returns:
        最终风格规范。
    """
    notes = decision.style_spec.notes.strip()
    if not decision.use_document_style:
        warnings.append("已按用户/模型的判定忽略文档风格，文档的设计令牌未注入")
        return StyleSpec(notes=notes)
    return _aggregate_style(digests, notes=notes)


def _aggregate_style(
    digests: Sequence[tuple[str, RequirementDigest]], notes: str = ""
) -> StyleSpec:
    """汇总所有文档的设计令牌（去重、保序、布局计数相加）。

    Args:
        digests: 各文档的理解结果。
        notes: 模型给出的风格意图描述。

    Returns:
        汇总后的风格规范；没有任何令牌时返回只带 notes 的空规范。
    """
    colors: list[str] = []
    fonts: list[str] = []
    sizes: list[str] = []
    radii: list[str] = []
    spacing: list[str] = []
    layout: dict[str, int] = {}

    for _ref, digest in digests:
        spec = digest.style_spec
        colors.extend(spec.colors)
        fonts.extend(spec.font_families)
        sizes.extend(spec.font_sizes)
        radii.extend(spec.border_radius)
        spacing.extend(spec.spacing)
        for key, value in spec.layout.items():
            layout[key] = layout.get(key, 0) + value

    return StyleSpec(
        colors=_dedupe(colors),
        font_families=_dedupe(fonts),
        font_sizes=_dedupe(sizes),
        border_radius=_dedupe(radii),
        spacing=_dedupe(spacing),
        layout=layout,
        notes=notes,
    )


def _build_sources(
    user_message: str,
    draft_summary: str,
    digests: Sequence[tuple[str, RequirementDigest]],
    rag: RagResult | None,
) -> list[RequirementSource]:
    """生成来源证据清单（**事实，不由模型产出**）。

    ⚠️ 只有 ``hit`` 才记 rag 证据：``miss`` 意味着**什么都没拿到**，
    把它写成"来自个人知识库"的证据，等于凭空捏造一条出处。

    Args:
        user_message: 用户本轮原话。
        draft_summary: 会话草稿摘要。
        digests: 各文档理解结果。
        rag: 知识库检索结果（可为 None）。

    Returns:
        证据清单（按优先级排序）。
    """
    sources: list[RequirementSource] = []
    if user_message.strip():
        sources.append(
            RequirementSource(kind="user", ref=_clip(user_message), note="用户本轮原话（最高优先级）")
        )
    if draft_summary.strip():
        sources.append(RequirementSource(kind="chat", ref=_clip(draft_summary), note="对话澄清摘要"))
    for ref, digest in digests:
        sources.append(
            RequirementSource(kind="doc", ref=ref, note=f"上传文档（角色：{digest.role}）")
        )
    if rag is not None and rag.status == "hit" and rag.chunks:
        sources.append(
            RequirementSource(
                kind="rag",
                ref="个人知识库",
                note=f"检索命中 {len(rag.chunks)} 段（检索词：{rag.query or '未记录'}）",
            )
        )
    return sources


def _rag_uncertainty(rag: RagResult | None) -> list[str]:
    """把 RAG 的"没查到"翻译成**面向用户**的不确定项。

    三条规则（阶段 4 的核心语义）：

    - ``hit`` → 不需要额外提示（资料已经拿到并进了提示词）；
    - ``skipped`` → **什么都不加**：判定本来就不需要私人资料，静默是正确的；
    - ``miss`` → 必须显式写出来，并给用户一个可执行的动作（补一句话或直接传文件）。

    ⚠️ 这里的"沉默"与"不沉默"是刻意分开的：把 ``skipped`` 也写成提示，
    会让每个通用页面都挂着一条无意义的"知识库未命中"，用户很快就学会忽略所有提示。

    Args:
        rag: 检索结果。

    Returns:
        需要追加到 `uncertainty` 的条目（可能为空）。
    """
    if rag is None or rag.status != "miss":
        return []
    return [
        f"个人知识库未命中：{rag.reason or '没有找到相关资料'}；"
        "如果该需求依赖你的私有资料，请补充说明或直接上传对应文件"
    ]


def _degraded_result(
    *,
    user_message: str,
    slots: RequirementSlots,
    digests: Sequence[tuple[str, RequirementDigest]],
    rag: RagResult | None,
    sources: list[RequirementSource],
    digest_uncertainty: list[str],
    reason: str,
    usage: ModelUsage,
) -> MergeResult:
    """归并失败时的兜底：用 Python 把各来源直接拼一份需求（**不假装成功**）。

    兜底也要保住三样东西：**用户说过的槽位**、**文档里的硬要求**、
    以及**知识库没查到这件事**，并把"这次归并没有经过模型"写进 uncertainty。

    Args:
        user_message: 用户本轮原话。
        slots: 已累积槽位。
        digests: 各文档理解结果。
        rag: 知识库检索结果。
        sources: 来源证据。
        digest_uncertainty: 文档里的待确认项。
        reason: 降级原因。
        usage: 已消耗的用量。

    Returns:
        降级后的结果。
    """
    content_points = _dedupe([item for _ref, digest in digests for item in digest.content_points])
    constraints = _dedupe([item for _ref, digest in digests for item in digest.constraints])
    uncertainty = _dedupe(
        [
            *digest_uncertainty,
            *_rag_uncertainty(rag),
            f"需求归并未经过模型整合（{reason}），可能存在遗漏或未消解的冲突",
        ]
    )
    requirement = FinalRequirement(
        summary=f"（降级）由对话槽位与 {len(digests)} 份文档要点直接合并",
        slots=slots,
        content_points=content_points,
        style_spec=_aggregate_style(digests),
        constraints=constraints,
        sources=sources,
        uncertainty=uncertainty,
    )
    return MergeResult(
        requirement=requirement,
        degraded=True,
        usage=usage,
        warnings=[f"需求归并降级：{reason}"],
    )


def _dedupe(items: Sequence[str]) -> list[str]:
    """按顺序去重并清理空白（保序：靠前的通常更重要）。"""
    seen: dict[str, None] = {}
    for item in items:
        text = " ".join(str(item).split())
        if text:
            seen.setdefault(text, None)
    return list(seen)


def _clip(text: str) -> str:
    """截断过长的证据文本（用户原话可能几百字）。"""
    flat = " ".join(text.split())
    return flat[:SOURCE_REF_CHARS] + ("…" if len(flat) > SOURCE_REF_CHARS else "")


# ==================== 提示词装配 ====================


def _build_merge_message(
    user_message: str,
    slots: RequirementSlots,
    draft_summary: str,
    digests: Sequence[tuple[str, RequirementDigest]],
    rag: RagResult | None,
) -> str:
    """拼出归并阶段的用户消息（四个来源按优先级排列）。"""
    parts = [
        "## 任务\n把下面四个来源归并成**一份**最终需求；冲突时按来源优先级裁决。",
        "## 来源 1（最高优先级｜永不裁）：用户本轮原话\n" + (user_message.strip() or "（无）"),
        "## 来源 2：对话澄清摘要\n"
        + (draft_summary.strip() or "（无摘要）")
        + "\n槽位：" + slots.model_dump_json(),
    ]

    if digests:
        blocks = [
            f"### {ref}（角色：{digest.role}）\n{digest.as_prompt_text()}"
            for ref, digest in digests
        ]
        parts.append("## 来源 3：上传文档的理解结果\n" + "\n\n".join(blocks))
    else:
        parts.append("## 来源 3：上传文档的理解结果\n（本轮没有文档）")

    parts.append(
        "## 来源 4（最低优先级）：个人知识库检索结果\n"
        + (rag.as_prompt_text() if rag is not None else "（本次未做检索判定）")
    )
    return "\n\n".join(parts)
