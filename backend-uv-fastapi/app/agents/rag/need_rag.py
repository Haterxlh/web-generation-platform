# app/agents/rag/need_rag.py —— 检索必要性判定节点（**真实现**）
#
# 职责（docs/agent_refactor_plan.md 阶段 4）：判断"这次需求要不要用用户的私人知识库"，
# 并在需要时给出**检索词**。这是一次真实的结构化模型调用，不是占位。
#
# 两条关键设计：
#
# 1. **判定归模型、兜底归 Python**（沿用 router 的既有原则）：
#    模型负责"要不要查"这种需要理解意图的判断；Python 负责两件它做不好的事 ——
#    `need=true` 却没给检索词时**补一个可用的 query**（否则二期检索必然空转），
#    以及调用失败时的降级方向。
#
# 2. **失败时默认判"不需要"**（`on_failure_need=False`），并把原因记进 `reason`：
#    判定失败却报"需要检索"会误导用户（一期会立刻变成一条"知识库未命中"的告警），
#    而失败原因本身已经通过 `degraded` 与 `reason` 留痕。
#    二期若希望"宁可多查一次"，把这个参数改成 True 即可 —— 降级方向是一处可配置的决策，
#    不是散落在代码里的隐式行为。

import logging
from collections.abc import Sequence

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from app.agents.common import ModelUsage
from app.agents.state import RequirementDigest, RequirementSlots, digest_one_line
from app.core.llm_client import llm_structured_client
from app.utils.weg_gen.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

PROMPT_NAME = "need_rag_system"

# 兜底检索词的最大长度：检索词是关键词，不是句子
MAX_QUERY_CHARS = 100

# 判定失败时的默认方向（False = 按"不需要检索"处理，理由见模块头注释）
DEFAULT_ON_FAILURE_NEED = False


class NeedRagDecision(BaseModel):
    """need_rag 节点的结构化输出。"""

    need: bool = Field(description="是否需要检索用户的私人知识库")
    query: str = Field(default="", description="检索关键词（need=true 时必填）")
    reason: str = Field(default="", description="判断依据（一句话，便于抽查）")


class NeedRagResult(BaseModel):
    """need_rag 节点的返回值。

    Attributes:
        decision: 判定结果（`query` 已由 Python 兜底补全过）。
        degraded: 是否走了降级路径（调用失败或结构化解析失败）。
        usage: 本次调用消耗的 token。
        warnings: 非致命问题（降级原因、query 被兜底等）。
    """

    model_config = {"arbitrary_types_allowed": True}

    decision: NeedRagDecision
    degraded: bool = False
    usage: ModelUsage = Field(default_factory=ModelUsage)
    warnings: list[str] = Field(default_factory=list)


def build_need_rag_chain(model: Runnable | None = None) -> Runnable:
    """构造判定链（结构化输出 + 保留原始 AIMessage 以便记账）。

    Args:
        model: 可注入的模型（测试用；默认用全局 `llm_structured_client`）。
            结构化输出依赖强制 tool_choice，与思考模式互斥（设计约定 §7.3）。

    Returns:
        输入 `list[BaseMessage]` → 输出 `{"raw": AIMessage, "parsed": NeedRagDecision|None}`。
    """
    return (model or llm_structured_client).with_structured_output(
        NeedRagDecision, include_raw=True
    )


# 模块级只建一次（链本身无状态）
_NEED_RAG = build_need_rag_chain()


def decide(
    *,
    user_message: str,
    slots: RequirementSlots | None = None,
    draft_summary: str = "",
    digests: Sequence[tuple[str, RequirementDigest]] = (),
    on_failure_need: bool = DEFAULT_ON_FAILURE_NEED,
    chain: Runnable | None = None,
) -> NeedRagResult:
    """判断本次需求是否需要用户的私人知识库。

    Args:
        user_message: 用户本轮原话。
        slots: 会话已累积的槽位（判断"用户是否在指代自己的东西"很有用）。
        draft_summary: 会话草稿的一句话摘要。
        digests: ``(来源标识, digest)``；判定只需要知道"用户提供了什么资料"。
        on_failure_need: 调用失败时判成"需要"还是"不需要"（默认不需要，见模块头注释）。
        chain: 可注入的链（测试用）。

    Returns:
        `NeedRagResult`（判定 + 降级标记 + 用量 + 警告）。
    """
    payload = [
        SystemMessage(content=load_prompt(PROMPT_NAME)),
        HumanMessage(
            content=_build_message(
                user_message, slots or RequirementSlots(), draft_summary, digests
            )
        ),
    ]

    try:
        result = (chain or _NEED_RAG).invoke(payload)
    except Exception as error:  # noqa: BLE001 —— 判定失败不能拖垮生成链路
        detail = f"{type(error).__name__}: {error}"[:200]
        logger.warning("need_rag 调用失败，按 need=%s 处理：%s", on_failure_need, detail)
        return NeedRagResult(
            decision=NeedRagDecision(
                need=on_failure_need,
                query=_fallback_query(slots, user_message) if on_failure_need else "",
                reason=f"检索必要性判定失败（{detail}），已按"
                + ("需要检索" if on_failure_need else "不需要检索")
                + "处理",
            ),
            degraded=True,
            warnings=[f"检索必要性判定降级：{detail}"],
        )

    raw = (result or {}).get("raw")
    usage = ModelUsage.from_message(raw) if raw is not None else ModelUsage()
    decision = (result or {}).get("parsed")

    if decision is None:
        # 结构化解析失败：用量照记（失败同样烧了 token）
        return NeedRagResult(
            decision=NeedRagDecision(
                need=on_failure_need,
                query=_fallback_query(slots, user_message) if on_failure_need else "",
                reason="检索必要性判定未按结构化契约返回，已按"
                + ("需要检索" if on_failure_need else "不需要检索")
                + "处理",
            ),
            degraded=True,
            usage=usage,
            warnings=["检索必要性判定结构化解析失败"],
        )

    warnings: list[str] = []
    query = " ".join(decision.query.split())
    if decision.need and not query:
        # ⚠️ 模型说"要查"却没给检索词：直接放行会让二期拿着空串去检索（必然查不到，
        # 而用户看到的是"你的资料里没有"）—— 这属于静默失败，必须由 Python 兜住
        query = _fallback_query(slots, user_message)
        warnings.append("模型判定需要检索但未给出检索词，已按槽位与用户原话兜底生成")

    return NeedRagResult(
        decision=NeedRagDecision(need=decision.need, query=query, reason=decision.reason),
        degraded=False,
        usage=usage,
        warnings=warnings,
    )


def _fallback_query(slots: RequirementSlots | None, user_message: str, limit: int = MAX_QUERY_CHARS) -> str:
    """`need=true` 但没有检索词时的兜底：用槽位与用户原话拼一个可用的关键词串。

    ⚠️ 兜底出来的 query 质量一定不如模型给的（它是关键词，不是句子），
    所以它只在"模型漏填"或"调用失败"时兜底，并在 warnings 里写明 ——
    不声不响地兜底会让二期的检索质量无从归因。

    Args:
        slots: 会话槽位（可为 None）。
        user_message: 用户原话。
        limit: 最大长度。

    Returns:
        检索关键词串（去重、限长）。
    """
    bits: list[str] = []
    if slots is not None:
        if slots.site_kind:
            bits.append(slots.site_kind)
        bits.extend(slots.features[:3])
        if slots.style:
            bits.append(slots.style)
        if slots.audience:
            bits.append(slots.audience)
    if not bits:
        bits.append(user_message)

    flat = " ".join(" ".join(str(item).split()) for item in bits if str(item).strip())
    return flat[:limit]


def _build_message(
    user_message: str,
    slots: RequirementSlots,
    draft_summary: str,
    digests: Sequence[tuple[str, RequirementDigest]],
) -> str:
    """拼出判定用的用户消息。"""
    parts = [
        "## 任务\n判断这次需求是否需要检索用户的私人知识库；需要时给出检索关键词。",
        "## 用户本轮原话\n" + (user_message.strip() or "（无）"),
        "## 会话澄清摘要\n" + (draft_summary.strip() or "（无）") + "\n槽位：" + slots.model_dump_json(),
    ]
    if digests:
        lines = [
            f"- {ref}：{digest.summary or digest_one_line(digest) or '（无概述）'}"
            for ref, digest in digests
        ]
        parts.append("## 用户已提供的资料\n" + "\n".join(lines))
    else:
        parts.append("## 用户已提供的资料\n（本轮没有上传文档）")
    return "\n\n".join(parts)
