"""阶段 4 的离线测试（二）：检索器接口与三态语义（retriever）。

一期不实现检索，但这一层要守住三件事：

1. **`skipped` 时根本不调 provider**：不是"调了返回空"。二期的调用会花钱、会连库，
   "没调"与"调了没查到"在排查与计费上完全不同。
2. **`miss` 要区分"没查到"与"能力未接入"**：前者用户该补资料，后者用户该等我们做完 ——
   合并成一句"没有相关资料"就是把我们的缺失说成用户的缺失。
3. **`user_id` 必填且必须为正**（硬约束 4）：pgvector 查询漏了 user_id 过滤，
   就是 A 能检索到 B 的私人文档 —— 这是整条链路里最严重的安全边界。
"""

import pytest

from app.agents.rag.retriever import (
    DEFAULT_TOP_K,
    NO_MATCH_REASON,
    StubRetrieverProvider,
    build_retriever_provider,
    retrieve,
)
from app.agents.state import RagChunk


class FakeProvider:
    """可控的假 provider：记录调用参数，返回预设片段。"""

    name = "fake"
    unavailable_reason: str | None = None

    def __init__(self, chunks: list[RagChunk] | None = None, reason: str | None = None) -> None:
        self.chunks = chunks or []
        self.unavailable_reason = reason
        self.calls: list[dict] = []

    def retrieve(self, user_id: int, query: str, top_k: int = DEFAULT_TOP_K) -> list[RagChunk]:
        self.calls.append({"user_id": user_id, "query": query, "top_k": top_k})
        return list(self.chunks)


def _chunk(text: str = "公司主色 #123456", source_type: str = "conversation") -> RagChunk:
    return RagChunk(
        chunk_id="c1",
        source_type=source_type,  # type: ignore[arg-type]
        source_ref="第 2 轮",
        text=text,
        score=0.9,
    )


# --------------------------------------------------------------------------
# 1. skipped：不检索，而且**根本不调用 provider**
# --------------------------------------------------------------------------


def test_skipped_does_not_call_provider() -> None:
    """⚠️ 核心用例：判定不需要时 provider 一次都不能被调用。"""
    provider = FakeProvider(chunks=[_chunk()])

    result = retrieve(7, needs_retrieval=False, query="公司 品牌色", provider=provider)

    assert result.status == "skipped"
    assert result.chunks == []
    assert provider.calls == [], "不需要检索时不应调用 provider（二期会花钱、会连库）"
    assert "不需要" in result.reason


def test_skipped_does_not_even_build_a_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """连 provider 工厂都不该被触发：避免"顺手构造"带来的隐式开销。"""

    def _explode() -> object:
        raise AssertionError("skipped 时不应构造 provider")

    monkeypatch.setattr("app.agents.rag.retriever.build_retriever_provider", _explode)

    result = retrieve(7, needs_retrieval=False)

    assert result.status == "skipped"


def test_skipped_keeps_query_for_traceability() -> None:
    """即使跳过，也保留检索词（便于二期评估"该查却没查"的判定质量）。"""
    result = retrieve(7, needs_retrieval=False, query="公司 品牌色")

    assert result.query == "公司 品牌色"


# --------------------------------------------------------------------------
# 2. miss：区分"没查到"与"能力未接入"
# --------------------------------------------------------------------------


def test_stub_provider_reports_not_available() -> None:
    """⚠️ 一期桩：返回 miss，并**自报家门**说检索尚未接入（而不是伪装成"没查到"）。"""
    result = retrieve(7, needs_retrieval=True, query="公司 品牌色")

    assert result.status == "miss"
    assert result.chunks == []
    assert "尚未接入" in result.reason
    assert any("检索能力不可用" in item for item in result.warnings), "要留下可排查的痕迹"


def test_miss_with_real_provider_uses_no_match_wording() -> None:
    """真 provider 查到空 → 措辞是"没有相关资料"（不是"未接入"）。"""
    provider = FakeProvider(chunks=[], reason=None)

    result = retrieve(7, needs_retrieval=True, query="公司 品牌色", provider=provider)

    assert result.status == "miss"
    assert result.reason == NO_MATCH_REASON
    assert result.warnings == []


# --------------------------------------------------------------------------
# 3. hit：片段透传
# --------------------------------------------------------------------------


def test_hit_returns_chunks_in_order() -> None:
    """命中时按 provider 给出的顺序透传（相关度排序由 provider 负责）。"""
    first = _chunk("第一条")
    second = RagChunk(chunk_id="c2", source_type="document", source_ref="规范.pdf", text="第二条")
    provider = FakeProvider(chunks=[first, second])

    result = retrieve(7, needs_retrieval=True, query="公司 品牌色", provider=provider)

    assert result.status == "hit"
    assert [item.text for item in result.chunks] == ["第一条", "第二条"]
    assert result.chunks[1].source_type == "document", "L1/L2 必须保留到消费端"


def test_hit_drops_empty_chunks_with_warning() -> None:
    """provider 返回的空片段要丢弃并记警告（空文本进提示词只会浪费 token）。"""
    provider = FakeProvider(chunks=[_chunk("有用"), _chunk("   ")])

    result = retrieve(7, needs_retrieval=True, query="q", provider=provider)

    assert [item.text for item in result.chunks] == ["有用"]
    assert any("空片段" in item for item in result.warnings)


def test_all_empty_chunks_become_miss() -> None:
    """全是空片段等于没查到（不能算 hit，否则下游会以为拿到了资料）。"""
    provider = FakeProvider(chunks=[_chunk("  ")])

    result = retrieve(7, needs_retrieval=True, query="q", provider=provider)

    assert result.status == "miss"


# --------------------------------------------------------------------------
# 4. 安全边界：user_id 必填且为正（硬约束 4）
# --------------------------------------------------------------------------


def test_user_id_has_no_default() -> None:
    """⚠️ user_id 是必填位置参数：没有默认值，二期实现者就没有"忘了传"的机会。"""
    with pytest.raises(TypeError):
        retrieve(needs_retrieval=True, query="q")  # type: ignore[call-arg]


@pytest.mark.parametrize("bad_user_id", [0, -1, True, "1", None])
def test_invalid_user_id_is_rejected(bad_user_id: object) -> None:
    """非正整数一律当场报错 —— 宁可失败，也不要放行一次"无主"检索。"""
    with pytest.raises(ValueError):
        retrieve(bad_user_id, needs_retrieval=True, query="q")  # type: ignore[arg-type]


def test_user_id_is_forwarded_to_provider() -> None:
    """⚠️ 越权防线回归：传进来的 user_id 必须**原样**到 provider（二期靠它做过滤）。"""
    provider = FakeProvider(chunks=[_chunk()])

    retrieve(42, needs_retrieval=True, query="公司 品牌色", provider=provider, top_k=3)

    assert provider.calls == [{"user_id": 42, "query": "公司 品牌色", "top_k": 3}]


def test_stub_provider_also_validates_user_id() -> None:
    """桩也校验：否则二期的实现者可能照着桩的写法漏掉过滤。"""
    with pytest.raises(ValueError):
        StubRetrieverProvider().retrieve(0, "q")


# --------------------------------------------------------------------------
# 5. 工厂与渲染
# --------------------------------------------------------------------------


def test_build_provider_returns_stub_in_phase_one() -> None:
    """一期工厂返回桩，且桩必须声明"我查不了"（不能是 None）。"""
    provider = build_retriever_provider()

    assert isinstance(provider, StubRetrieverProvider)
    assert provider.unavailable_reason is not None


def test_default_top_k_is_five() -> None:
    """默认取回条数是契约（喂给模型的上下文不是越多越好）。"""
    assert DEFAULT_TOP_K == 5


def test_prompt_text_for_three_states() -> None:
    """⚠️ 三态渲染必须**都说清楚**：空串会被模型读成"用户资料里没有"，然后开编。"""
    hit = retrieve(7, needs_retrieval=True, query="q", provider=FakeProvider(chunks=[_chunk()]))
    miss = retrieve(7, needs_retrieval=True, query="q", provider=FakeProvider())
    skipped = retrieve(7, needs_retrieval=False)

    hit_text = hit.as_prompt_text()
    assert "命中片段" in hit_text and "公司主色 #123456" in hit_text
    assert "conversation" in hit_text, "来源层级要传给模型（L1 与 L2 证据强度不同）"

    miss_text = miss.as_prompt_text()
    assert "未命中" in miss_text
    assert "不要据此编造" in miss_text, "未命中必须明确禁止编造用户的事实"

    assert "不需要" in skipped.as_prompt_text()


def test_needs_personal_knowledge_property() -> None:
    """三态到"要不要私人资料"的映射：只有 skipped 是"不要"。"""
    assert retrieve(7, needs_retrieval=False).needs_personal_knowledge is False
    assert retrieve(7, needs_retrieval=True, provider=FakeProvider()).needs_personal_knowledge is True
    assert (
        retrieve(7, needs_retrieval=True, provider=FakeProvider(chunks=[_chunk()])).needs_personal_knowledge
        is True
    )
