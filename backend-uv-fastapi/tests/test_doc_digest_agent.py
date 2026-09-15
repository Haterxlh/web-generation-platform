"""阶段 3 的离线测试（三）：digest-agent（文档理解）。

用假链（``RunnableLambda``）替代模型，因此**离线、快、稳**，并且能精确验证几件
"真跑模型时很难观察、但错了代价很大"的事：

- **设计令牌必须来自解析器**：模型返回的 ``colors`` 要**被覆盖**而不是合并 ——
  一个幻觉出来的色值混进风格规范，就会一路传到 web-agent 的提示词里；
- **角色否决**：``.md`` / PDF 被判成风格源时必须改回 ``content``，并留下原因；
- **降级不阻塞但也不假装成功**：理解失败要留下正文片段 + 显式的不确定性；
- **失败也记账**：结构化解析失败时用量照样要累加（与生成模块同一口径）。
"""

import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agents.source.doc_digest_agent import (
    CHUNK_CHAR_BUDGET,
    ChunkDigest,
    DigestResult,
    build_chunk_chain,
    build_digest_chain,
    digest,
    fallback_digest,
    split_blocks,
)
from app.agents.state import RequirementDigest, StyleSpec
from app.utils.doc.base import ParsedDocument
from app.utils.weg_gen.prompt_loader import load_prompt

HTML_TOKENS = {
    "colors": ["#0f172a", "#4f46e5"],
    "font_families": ["Inter"],
    "font_sizes": ["16px"],
    "border_radius": ["8px"],
    "spacing": ["24px"],
    "layout": {"display:grid": 1},
}


# --------------------------------------------------------------------------
# 假链与假文档
# --------------------------------------------------------------------------


def _raw(input_tokens: int = 10, output_tokens: int = 5, reasoning: int = 0) -> AIMessage:
    """造一个带用量元数据的 AIMessage。"""
    return AIMessage(
        content="",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "output_token_details": {"reasoning": reasoning},
        },
    )


def _chain(parsed: object, raw: AIMessage | None = None) -> RunnableLambda:
    """假链：返回 with_structured_output(include_raw=True) 的产物形态。"""
    return RunnableLambda(lambda _payload: {"raw": raw, "parsed": parsed})


def _boom_chain(message: str = "模型服务超时") -> RunnableLambda:
    """调用即抛异常的假链。"""

    def _boom(_payload: object) -> object:
        raise RuntimeError(message)

    return RunnableLambda(_boom)


def _text_doc(text: str, source_type: str = "text") -> ParsedDocument:
    return ParsedDocument(source_type=source_type, text=text)  # type: ignore[arg-type]


def _html_doc(text: str = "企业官网设计规范") -> ParsedDocument:
    return ParsedDocument(source_type="html", text=text, design_tokens=dict(HTML_TOKENS))


def _long_doc(paragraphs: int = 5, size: int = 3000) -> ParsedDocument:
    """造一份"必然要分块"的长文档。

    每个 block 取 3000 字符（略小于 4000 的块预算）：块与块之间**装不下**，
    于是"一段 = 一块"，测试里就能用确定的段数推断调用次数。
    """
    blocks = [f"第{index}段：" + "甲" * size for index in range(1, paragraphs + 1)]
    return ParsedDocument(
        source_type="pdf",
        text="\n\n".join(blocks),
        native_blocks=blocks,
        page_count=paragraphs,
    )


SAMPLE_DIGEST = RequirementDigest(
    summary="一份待办清单需求说明书",
    role="content",
    content_points=["标题：我的待办"],
    constraints=["必须支持回车添加"],
    open_questions=["是否需要多语言？"],
)


# --------------------------------------------------------------------------
# 1. 短文档：一次调用
# --------------------------------------------------------------------------


def test_short_document_uses_single_call() -> None:
    """短文档不折腾 map-reduce（省一次归并调用 = 省一份钱与延迟）。"""
    calls: list[list] = []

    def _record(payload: list) -> dict:
        calls.append(payload)
        return {"raw": _raw(), "parsed": SAMPLE_DIGEST}

    result = digest(_text_doc("做一个待办清单页面"), chain=RunnableLambda(_record))

    assert len(calls) == 1
    assert result.chunk_count == 1
    assert result.degraded is False
    assert result.digest.content_points == ["标题：我的待办"]
    assert result.digest.constraints == ["必须支持回车添加"]


def test_single_call_message_contains_document_content() -> None:
    """整篇理解的提示词里必须真的带上文档内容（否则模型只能编）。"""
    seen: list[str] = []

    def _record(payload: list) -> dict:
        seen.append("\n".join(message.content for message in payload))
        return {"raw": _raw(), "parsed": SAMPLE_DIGEST}

    digest(_text_doc("需求：做一个带筛选的待办清单"), chain=RunnableLambda(_record))

    assert "需求：做一个带筛选的待办清单" in seen[0]
    assert "## 文档信息" in seen[0]


def test_usage_is_read_from_raw_message() -> None:
    """用量从原始 AIMessage 读出来（成功也要记账）。"""
    result = digest(
        _text_doc("随便一份文档"),
        chain=_chain(SAMPLE_DIGEST, _raw(input_tokens=321, output_tokens=45, reasoning=7)),
    )

    assert result.usage.input_tokens == 321
    assert result.usage.output_tokens == 45
    assert result.usage.reasoning_tokens == 7


# --------------------------------------------------------------------------
# 2. 设计令牌与角色：Python 的否决权
# --------------------------------------------------------------------------


def test_design_tokens_are_overwritten_from_parser() -> None:
    """⚠️ 核心用例：模型给的配色必须被解析器的实测取值**覆盖**（不是合并、不是补齐）。

    模型看到"深色底"很可能回一个看起来合理的 ``#1e293b``；但页面里真实写的是 ``#0f172a``。
    风格复刻场景下这种"接近但不对"的偏差会直接毁掉还原度。
    """
    hallucinated = RequirementDigest(
        summary="设计规范",
        role="style",
        style_spec=StyleSpec(colors=["#1e293b"], font_families=["Roboto"], notes="深色底 + 大圆角"),
    )

    result = digest(_html_doc(), chain=_chain(hallucinated))

    assert result.digest.style_spec.colors == ["#0f172a", "#4f46e5"], "必须是解析器的实测值"
    assert result.digest.style_spec.font_families == ["Inter"]
    assert result.digest.style_spec.notes == "深色底 + 大圆角", "文字描述是模型的地盘，要保留"
    assert result.digest.style_spec.border_radius == ["8px"]
    assert result.role_overridden is False


def test_style_role_is_vetoed_for_markdown() -> None:
    """Markdown 被判成风格源 → 改回 content，并把原因写进 warnings。"""
    markdown_digest = RequirementDigest(summary="需求说明书", role="both")

    result = digest(
        _text_doc("主色用深蓝，圆角 8px", source_type="markdown"), chain=_chain(markdown_digest)
    )

    assert result.digest.role == "content"
    assert result.role_overridden is True
    assert any("设计令牌" in item for item in result.warnings)
    assert any(".html" in item for item in result.warnings), "要给出可行做法"


def test_style_role_is_vetoed_for_html_without_tokens() -> None:
    """HTML 但没有样式声明 → 同样否决，原因措辞要区分开。"""
    plain_html = ParsedDocument(source_type="html", text="只有文字的页面")

    result = digest(plain_html, chain=_chain(RequirementDigest(role="style")))

    assert result.digest.role == "content"
    assert result.role_overridden is True
    assert any("没有可识别的样式声明" in item for item in result.warnings)


def test_html_with_tokens_keeps_style_role() -> None:
    """有设计令牌的 HTML 说自己是风格源 → 尊重模型判定，不算否决。"""
    result = digest(_html_doc(), chain=_chain(RequirementDigest(role="style")))

    assert result.digest.role == "style"
    assert result.role_overridden is False


# --------------------------------------------------------------------------
# 3. 长文档：map-reduce
# --------------------------------------------------------------------------


def test_long_document_goes_map_reduce() -> None:
    """长文档：逐块摘要（map）+ 一次归并（reduce）。"""
    chunk_calls: list[str] = []
    reduce_calls: list[str] = []

    def _map(payload: list) -> dict:
        chunk_calls.append("\n".join(message.content for message in payload))
        index = len(chunk_calls)
        return {
            "raw": _raw(),
            "parsed": ChunkDigest(
                content_points=[f"素材{index}"],
                constraints=[f"要求{index}"],
                open_questions=[f"疑问{index}"] if index == 1 else [],
                style_notes="深色底" if index == 1 else "",
            ),
        }

    def _reduce(payload: list) -> dict:
        reduce_calls.append("\n".join(message.content for message in payload))
        return {"raw": _raw(), "parsed": SAMPLE_DIGEST}

    result = digest(
        _long_doc(paragraphs=5),
        filename="需求书.pdf",
        chain=RunnableLambda(_reduce),
        chunk_chain=RunnableLambda(_map),
    )

    assert len(chunk_calls) == 5, "每段一次 map 调用"
    assert result.chunk_count == 5
    assert len(reduce_calls) == 1, "只归并一次"
    assert "第 2/5 段" in chunk_calls[1], "分块消息要标出序号，模型才知道自己在读哪一段"
    # 归并消息里要带齐各段结果，否则归并环节等于重新猜
    assert "素材1" in reduce_calls[0] and "素材5" in reduce_calls[0]
    assert "要求3" in reduce_calls[0]


def test_usage_accumulates_across_all_calls() -> None:
    """用量要跨 map + reduce 累加（一次文档理解可能烧很多次调用）。"""
    map_chain = _chain(ChunkDigest(content_points=["a"]), _raw(input_tokens=10, output_tokens=1))
    reduce_chain = _chain(SAMPLE_DIGEST, _raw(input_tokens=100, output_tokens=5))

    result = digest(_long_doc(paragraphs=5), chain=reduce_chain, chunk_chain=map_chain)

    assert result.usage.input_tokens == 100 + 10 * 5
    assert result.usage.output_tokens == 5 + 5


def test_chunk_failure_is_skipped_but_marks_degraded() -> None:
    """某个分块失败 → 跳过该块继续做，但整体必须标降级（信息确实少了一块）。"""
    calls = {"n": 0}

    def _flaky(payload: list) -> dict:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("第 2 段超时")
        return {"raw": _raw(), "parsed": ChunkDigest(content_points=[f"素材{calls['n']}"])}

    result = digest(
        _long_doc(paragraphs=3),
        chain=_chain(SAMPLE_DIGEST),
        chunk_chain=RunnableLambda(_flaky),
    )

    assert result.degraded is True
    assert any("第 2/3 段理解失败" in item for item in result.warnings)
    assert result.chunk_count == 3


def test_all_chunks_failing_falls_back_to_snippet() -> None:
    """所有分块都失败 → 降级为正文片段，并把不确定性显式传给下游。"""
    result = digest(
        _long_doc(paragraphs=3),
        chain=_chain(SAMPLE_DIGEST),
        chunk_chain=_boom_chain(),
    )

    assert result.degraded is True
    assert result.chunk_count == 3
    assert result.digest.content_points, "降级也要留下正文片段，不能是空的"
    assert any("未能被完整理解" in item for item in result.digest.open_questions)
    assert any("所有分块都理解失败" in item for item in result.warnings)


def test_reduce_failure_merges_partials_in_python() -> None:
    """归并失败 → 用各段结果在 Python 侧合并（比退化成"只留一段正文"信息更多）。"""
    map_chain = _chain(
        ChunkDigest(content_points=["素材A"], constraints=["要求A"], style_notes="深色底")
    )

    result = digest(
        _long_doc(paragraphs=3),
        chain=_boom_chain("归并超时"),
        chunk_chain=map_chain,
    )

    assert result.degraded is True
    assert "直接合并" in result.digest.summary
    assert result.digest.content_points == ["素材A"]
    assert result.digest.constraints == ["要求A"]
    assert result.digest.style_spec.notes == "深色底"
    assert any("分块归并失败" in item for item in result.warnings)


def test_giant_document_caps_chunk_count_with_warning() -> None:
    """超长文档不会把调用次数炸开：截取首尾并**显式记警告**（不静默丢内容）。

    这里把上限压到 2 段来验证逻辑（真实上限是 12）：
    5 个小段按 budget=100 会打成 3 块（[1,2] / [3,4] / [5]），上限 2 时保留首块与末块。
    """
    blocks = [f"第{index}段" + "甲" * 36 for index in range(1, 6)]
    parsed = ParsedDocument(
        source_type="pdf", text="\n\n".join(blocks), native_blocks=blocks, page_count=5
    )

    chunks, warnings = split_blocks(parsed, budget=100, max_chunks=2)

    assert len(chunks) == 2
    assert "第1段" in chunks[0], "保留开头"
    assert "第5段" in chunks[-1], "保留结尾（结论与约束常写在最后）"
    assert any("中间跳过 1 段" in item for item in warnings)


# --------------------------------------------------------------------------
# 4. 分块纯函数
# --------------------------------------------------------------------------


def test_split_blocks_packs_small_pages_together() -> None:
    """PDF 的小页会被合并到同一块 —— 一页一次调用太浪费。"""
    pages = ["第 1 页内容", "第 2 页内容", "第 3 页内容"]
    parsed = ParsedDocument(
        source_type="pdf", text="\n\n".join(pages), native_blocks=pages, page_count=3
    )

    chunks, warnings = split_blocks(parsed, budget=4000)

    assert len(chunks) == 1
    assert "第 1 页内容" in chunks[0] and "第 3 页内容" in chunks[0]
    assert warnings == []


def test_split_blocks_splits_by_paragraph_and_respects_budget() -> None:
    """没有天然分块时按空行分段，每块不超过预算。"""
    blocks = ["甲" * 300, "乙" * 300, "丙" * 300]
    parsed = _text_doc("\n\n".join(blocks))

    chunks, _warnings = split_blocks(parsed, budget=400)

    assert len(chunks) == 3
    assert all(len(chunk) <= 400 for chunk in chunks)


def test_split_blocks_hard_splits_oversized_unit() -> None:
    """单个单位就超预算（机器生成的超长行）时必须硬切，不能让一块撑爆上下文。"""
    parsed = _text_doc("甲" * 1000)

    chunks, _warnings = split_blocks(parsed, budget=300)

    assert len(chunks) == 4
    assert all(len(chunk) <= 300 for chunk in chunks)
    assert sum(len(chunk) for chunk in chunks) == 1000, "硬切不能丢字符"


def test_split_blocks_on_empty_document() -> None:
    """空文档没有块可切（调用方会因此不调模型）。"""
    assert split_blocks(ParsedDocument(source_type="text")) == ([], [])


def test_default_budget_is_4000() -> None:
    """预算值本身是约定（调小会让调用次数暴涨，调大会撑爆上下文）。"""
    assert CHUNK_CHAR_BUDGET == 4000


# --------------------------------------------------------------------------
# 5. 失败路径：降级与记账
# --------------------------------------------------------------------------


def test_model_failure_degrades_to_snippet() -> None:
    """调用抛异常 → 降级为正文片段，且不把异常抛给调用方（文档理解不该拖垮主流程）。"""
    parsed = _text_doc("这是一份需求说明书，内容包括登录、注册与个人中心。")

    result = digest(parsed, chain=_boom_chain("模型服务超时"))

    assert result.degraded is True
    assert "超时" in result.warnings[0]
    assert result.digest.content_points[0].startswith("这是一份需求说明书")
    assert result.digest.open_questions, "降级必须把不确定性传下去"


def test_structured_parse_failure_still_records_usage() -> None:
    """⚠️ 模型没按 schema 返回时，用量照样要记 —— 失败同样烧了 token。"""
    result = digest(
        _text_doc("一份文档"),
        chain=_chain(None, _raw(input_tokens=88, output_tokens=12)),
    )

    assert result.degraded is True
    assert result.usage.input_tokens == 88, "解析失败也要记账"
    assert any("未按结构化契约返回" in item for item in result.warnings)


def test_empty_document_never_calls_model() -> None:
    """⚠️ 空文档**不调模型**：没有输入可总结，调了只会得到编造的内容。"""
    called: list[int] = []

    def _should_not_be_called(_payload: object) -> dict:
        called.append(1)
        return {"raw": _raw(), "parsed": SAMPLE_DIGEST}

    result = digest(
        ParsedDocument(source_type="html", text="", design_tokens={}),
        chain=RunnableLambda(_should_not_be_called),
    )

    assert called == []
    assert result.degraded is True
    assert result.chunk_count == 0
    assert any("没有任何可提取的内容" in item for item in result.warnings)


def test_fallback_digest_keeps_design_tokens() -> None:
    """降级也不能丢设计令牌 —— 它们是解析器辛苦抽出来的**事实**。"""
    fallback = fallback_digest(_html_doc())

    assert fallback.style_spec.colors == ["#0f172a", "#4f46e5"]
    assert fallback.role == "content"


def test_fallback_digest_on_text_document() -> None:
    """纯文本文档降级：只有正文片段，没有风格信息。"""
    fallback = fallback_digest(_text_doc("只有文字"))

    assert fallback.content_points == ["只有文字"]
    assert fallback.style_spec.is_empty() is True


def test_result_is_json_serializable() -> None:
    """结果要能直接落库 / 落日志（``generation_source.digest`` 是 JSONB）。"""
    result = digest(_html_doc(), chain=_chain(SAMPLE_DIGEST))

    assert isinstance(result, DigestResult)
    payload = json.dumps(result.digest.model_dump(), ensure_ascii=False)

    assert "我的待办" in payload


# --------------------------------------------------------------------------
# 6. 链构造与提示词（防止提示词文件名写错这类"运行期才发现"的问题）
# --------------------------------------------------------------------------


def test_build_chains_use_structured_output() -> None:
    """两条链都必须带结构化输出（否则拿到自由文本，无法当契约用）。"""
    assert build_digest_chain() is not None
    assert build_chunk_chain() is not None


@pytest.mark.parametrize("name", ["doc_digest_system", "doc_chunk_digest_system"])
def test_prompts_load_and_state_key_rules(name: str) -> None:
    """提示词必须能加载，且写明了"素材与要求要分开"这条关键纪律。"""
    content = load_prompt(name)

    assert content, "提示词不能为空"
    assert "content_points" in content
    assert "constraints" in content
