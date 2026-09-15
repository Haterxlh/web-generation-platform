# app/agents/rag/retriever.py —— 个人知识库检索：**接口 + 一期桩 provider**
#
# 一期（本阶段）**不实现检索**：没有向量表、没有 embedding、没有 pgvector 查询。
# 但接口、三态语义与安全边界从第一天就是真的 ——
# 二期的全部工作就是把 `build_retriever_provider()` 换成真实现，
# **不动图结构、不动提示词骨架**（阶段 4 的验收要求）。
#
# ⚠️ 本文件最要紧的一行是 `retrieve(user_id=...)` 的签名：
# **user_id 是必填位置参数，且这里会拒绝非正数**。
# 硬约束 4 说得很直白：pgvector 查询一旦漏了 user_id 过滤，就是 A 能检索到 B 的私人文档 ——
# 那是整条链路里最严重的安全边界。把 user_id 做成"必填、无默认、进函数先校验"，
# 二期实现的人就没有"忘了传"的机会。

import logging
from typing import Protocol

from app.agents.state import RagChunk, RagResult

logger = logging.getLogger(__name__)

# 默认取回片段数：喂给模型的上下文不是越多越好（无关片段会稀释真正有用的信息）
DEFAULT_TOP_K = 5

# "真的查了但没有"的措辞。与"检索能力未接入"必须区分开：
# 前者是用户资料里没有，后者是我们还没做 —— 用户能采取的行动完全不同。
NO_MATCH_REASON = "个人知识库中没有与该需求相关的资料"


class RetrieverProvider(Protocol):
    """检索器接口（二期由真实现满足这个协议）。

    Attributes:
        name: provider 名称（进日志与 trace，便于区分"谁查的"）。
        unavailable_reason: **检索能力是否可用**的显式声明。
            ``None`` 表示"我能真的查"；非 None 表示"我还查不了"，值是给用户看的原因。
            ⚠️ 刻意把它做成协议的一部分而不是靠约定：桩 provider 必须**自报家门**，
            否则"返回空结果"会被误读成"用户资料里没有" —— 那是静默失败。
    """

    name: str
    unavailable_reason: str | None

    def retrieve(self, user_id: int, query: str, top_k: int = DEFAULT_TOP_K) -> list[RagChunk]:
        """按检索词取回该用户的私人资料片段。

        Args:
            user_id: 用户 id（**必须**用于过滤，硬约束 4）。
            query: 检索关键词。
            top_k: 最多取回多少条。

        Returns:
            命中的片段列表（按相关度降序）。
        """
        ...


class StubRetrieverProvider:
    """一期桩：**不检索**，并明确声明自己还没接入。

    刻意不抛 `NotImplementedError`：那会让整条生成链路在"用户确实需要检索"时直接失败。
    桩的正确行为是"诚实地报告查不到"，让链路继续走，并把"未接入"这件事
    通过 ``unavailable_reason`` 一路传到用户的 uncertainty 里。
    """

    name = "stub"
    unavailable_reason = (
        "个人知识库检索尚未接入（二期）：当前只能判定「需要检索」，无法真的查询你的资料"
    )

    def retrieve(self, user_id: int, query: str, top_k: int = DEFAULT_TOP_K) -> list[RagChunk]:
        """永远返回空列表（但仍校验入参，保证二期的实现者不会绕过 user_id）。"""
        _require_user_id(user_id)
        logger.info("桩检索器被调用（user_id=%s, query=%r, top_k=%s），一期不检索", user_id, query, top_k)
        return []


def build_retriever_provider() -> RetrieverProvider:
    """构造检索器 provider —— **二期唯一的切换点**。

    二期在这里返回真实现（华为云 BGE-M3 + pgvector），其余代码一行都不用改。

    Returns:
        一期返回 `StubRetrieverProvider`。
    """
    return StubRetrieverProvider()


def retrieve(
    user_id: int,
    *,
    needs_retrieval: bool,
    query: str = "",
    provider: RetrieverProvider | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> RagResult:
    """按"是否需要检索"的判定执行检索，产出三态结果。

    Args:
        user_id: 用户 id（**必填、无默认值** —— 见模块头注释）。
        needs_retrieval: `need_rag` 的判定结果。
        query: 检索关键词。
        provider: 可注入的 provider（测试用；默认取 `build_retriever_provider()`）。
        top_k: 最多取回多少条。

    Returns:
        `RagResult`（hit / miss / skipped 三态之一）。

    Raises:
        ValueError: user_id 不是正整数（宁可当场报错，也不要放行一次"无主"检索）。
    """
    _require_user_id(user_id)

    if not needs_retrieval:
        # ⚠️ 不需要检索时**连 provider 都不构造、不调用**：
        # "调了但返回空"与"根本没调"在排查与计费上完全不同（二期调用会花钱）
        return RagResult(
            status="skipped",
            query=query,
            reason="本次需求不需要私人知识库（通用需求，未进行检索）",
        )

    active = provider or build_retriever_provider()
    chunks = active.retrieve(user_id, query, top_k=top_k) or []
    warnings: list[str] = []

    usable = []
    for chunk in chunks:
        if chunk.text.strip():
            usable.append(chunk)
    if len(usable) != len(chunks):
        warnings.append(f"检索结果中有 {len(chunks) - len(usable)} 条空片段，已丢弃")

    if not usable:
        # "未接入"与"查了没有"必须分开说：前者用户该等我们做完，后者用户该补资料
        reason = active.unavailable_reason or NO_MATCH_REASON
        if active.unavailable_reason:
            warnings.append(f"检索能力不可用（provider={active.name}）：{active.unavailable_reason}")
        return RagResult(
            status="miss",
            query=query,
            reason=reason,
            warnings=warnings,
        )

    return RagResult(status="hit", query=query, chunks=usable, warnings=warnings)


def _require_user_id(user_id: int) -> None:
    """校验 user_id 是正整数。

    Args:
        user_id: 待校验的用户 id。

    Raises:
        ValueError: 不是正整数 —— 这是硬约束 4（越权检索）的最后一道门。
    """
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise ValueError(f"检索必须带上有效的 user_id（越权风险），收到 {user_id!r}")
