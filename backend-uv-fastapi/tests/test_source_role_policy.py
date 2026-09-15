"""阶段 3 的离线测试（二）：角色裁决与 digest 契约（无模型、无数据库）。

这里守的是两条"看起来只是约定、实际一动就出事"的线：

1. **只有 HTML 能当风格源** —— PDF / 纯文本 / Markdown 的解析器给不出设计令牌。
   模型看到文档里写着"主色是深蓝"，很可能把它判成风格源；若不否决，
   下游会拿到一个**空的风格规范**，还会以为"风格已经定过了"而不再追问。
2. **JSONB 落库必须可序列化** —— ``RequirementDigest`` 要原样写进
   ``generation_source.digest``，一旦塞进不可序列化的对象，就会在**运行期**炸。
"""

import json

import pytest

from app.agents.source.role_policy import (
    ROLE_BOTH,
    ROLE_CONTENT,
    ROLE_STYLE,
    VALID_ROLES,
    resolve_role,
)
from app.agents.state import RequirementDigest, StyleSpec
from app.utils.doc.base import ParsedDocument

TOKENS = {
    "colors": ["#0f172a", "#4f46e5"],
    "font_families": ["Inter"],
    "font_sizes": ["16px", "32px"],
    "border_radius": ["8px"],
    "spacing": ["24px"],
    "layout": {"display:grid": 2, "@media": 1},
}


def _html_doc() -> ParsedDocument:
    """带设计令牌的 HTML（唯一有资格当风格源的形态）。"""
    return ParsedDocument(source_type="html", text="企业官网设计规范", design_tokens=dict(TOKENS))


def _style_only_html() -> ParsedDocument:
    """只有样式、没有正文的 HTML：合法的风格源。"""
    return ParsedDocument(source_type="html", text="", design_tokens={"colors": ["#123456"]})


def _plain_doc(source_type: str) -> ParsedDocument:
    """PDF / Markdown / 纯文本：只能当内容源。"""
    return ParsedDocument(source_type=source_type, text="一些正文内容")


# --------------------------------------------------------------------------
# 1. 角色裁决：只有 HTML 能当风格源
# --------------------------------------------------------------------------


def test_html_with_tokens_keeps_style_role() -> None:
    """模型判 style，且 HTML 确实有设计令牌 → 尊重模型判定。"""
    decision = resolve_role(ROLE_STYLE, _html_doc())

    assert decision.role == ROLE_STYLE
    assert decision.overridden is False
    assert decision.reason is None


def test_html_with_tokens_keeps_both_role() -> None:
    """both 同样成立（同一份 HTML 常常既是内容来源也是风格来源）。"""
    decision = resolve_role(ROLE_BOTH, _html_doc())

    assert decision.role == ROLE_BOTH
    assert decision.overridden is False


def test_style_only_html_is_accepted() -> None:
    """只有样式没有正文的 HTML 是**合法风格源**，不能被误否决。"""
    decision = resolve_role(ROLE_STYLE, _style_only_html())

    assert decision.role == ROLE_STYLE
    assert decision.overridden is False


@pytest.mark.parametrize("source_type", ["pdf", "markdown", "text"])
@pytest.mark.parametrize("requested", [ROLE_STYLE, ROLE_BOTH])
def test_non_html_style_request_is_vetoed(source_type: str, requested: str) -> None:
    """⚠️ 核心用例：非 HTML 要 style/both 一律否决为 content，并留下可读原因。"""
    decision = resolve_role(requested, _plain_doc(source_type))

    assert decision.role == ROLE_CONTENT
    assert decision.overridden is True
    assert decision.requested == requested
    assert "设计令牌" in (decision.reason or "")
    assert ".html" in (decision.reason or ""), "要告诉用户可行的做法（换 HTML 上传）"


def test_html_without_tokens_style_request_is_vetoed() -> None:
    """HTML 但没有样式声明 → 同样否决，但原因要区分开（不是"类型不对"，而是"这文件没样式"）。"""
    doc = ParsedDocument(source_type="html", text="只有文字的页面", design_tokens={})

    decision = resolve_role(ROLE_STYLE, doc)

    assert decision.role == ROLE_CONTENT
    assert decision.overridden is True
    assert "没有可识别的样式声明" in (decision.reason or "")


@pytest.mark.parametrize("requested", [None, "", "   ", ROLE_CONTENT, "CONTENT"])
def test_content_role_needs_no_override(requested: str | None) -> None:
    """content 是安全侧：模型没说或说了 content，都原样放行、不记否决。"""
    decision = resolve_role(requested, _plain_doc("pdf"))

    assert decision.role == ROLE_CONTENT
    assert decision.overridden is False
    assert decision.reason is None


def test_missing_role_defaults_to_content() -> None:
    """模型没给角色时按 content 处理，且 ``requested`` 保留 None 以示"没给过"。"""
    decision = resolve_role(None, _html_doc())

    assert decision.role == ROLE_CONTENT
    assert decision.requested is None
    assert decision.overridden is False


def test_invalid_role_is_reported_as_override() -> None:
    """非法取值（模型编出来的第四个角色）必须显式记录，而不是静默当 content。"""
    decision = resolve_role("stylish", _html_doc())

    assert decision.role == ROLE_CONTENT
    assert decision.overridden is True
    assert "不是 content/style/both" in (decision.reason or "")


def test_valid_roles_match_api_contract() -> None:
    """角色枚举必须与接口契约（AttachmentRef.role / generation_source.role）一致。"""
    assert VALID_ROLES == {"content", "style", "both"}


# --------------------------------------------------------------------------
# 2. StyleSpec：结构化取值只从解析器搬，不让模型猜
# --------------------------------------------------------------------------


def test_style_spec_from_design_tokens_copies_all_fields() -> None:
    """设计令牌要逐字段搬进契约（六类一个都不能漏）。"""
    spec = StyleSpec.from_design_tokens(TOKENS, notes="深色底 + 大圆角")

    assert spec.colors == ["#0f172a", "#4f46e5"]
    assert spec.font_families == ["Inter"]
    assert spec.font_sizes == ["16px", "32px"]
    assert spec.border_radius == ["8px"]
    assert spec.spacing == ["24px"]
    assert spec.layout == {"display:grid": 2, "@media": 1}
    assert spec.notes == "深色底 + 大圆角"
    assert spec.is_empty() is False


def test_style_spec_from_design_tokens_ignores_unknown_keys() -> None:
    """解析器将来新增令牌字段，不能把契约打挂。"""
    spec = StyleSpec.from_design_tokens({"colors": ["#fff"], "shadows": ["0 1px 2px"]})

    assert spec.colors == ["#fff"]
    assert not hasattr(spec, "shadows")


def test_style_spec_from_empty_tokens() -> None:
    """没有令牌（PDF / 纯文本）时得到一份空规范。"""
    assert StyleSpec.from_design_tokens(None).is_empty() is True
    assert StyleSpec.from_design_tokens({}).is_empty() is True


def test_style_spec_tolerates_broken_layout_value() -> None:
    """layout 不是映射时不能抛异常 —— 解析层是外部输入的边界。"""
    spec = StyleSpec.from_design_tokens({"colors": ["#fff"], "layout": "grid"})

    assert spec.layout == {}
    assert spec.colors == ["#fff"]


# --------------------------------------------------------------------------
# 3. RequirementDigest：默认值、渲染、落库可序列化
# --------------------------------------------------------------------------


def test_digest_defaults_to_content_role() -> None:
    """默认角色是 content（安全侧）。"""
    digest = RequirementDigest()

    assert digest.role == ROLE_CONTENT
    assert digest.content_points == []
    assert digest.style_spec.is_empty() is True


def test_digest_is_json_serializable_for_jsonb() -> None:
    """⚠️ digest 要原样写进 PG 的 JSONB 列，必须可序列化。"""
    digest = RequirementDigest(
        summary="一份设计规范",
        role=ROLE_BOTH,
        content_points=["首页文案：企业官网"],
        style_spec=StyleSpec.from_design_tokens(TOKENS, notes="深色底"),
        constraints=["必须响应式"],
        open_questions=["是否需要多语言？"],
    )

    payload = json.dumps(digest.model_dump(), ensure_ascii=False)

    assert "深色底" in payload
    assert json.loads(payload)["role"] == ROLE_BOTH


def test_digest_roundtrip_from_dict() -> None:
    """从库里的 dict 还原（跨轮复用 digest 时会走这条路径）。"""
    original = RequirementDigest(
        summary="需求说明书", role=ROLE_CONTENT, constraints=["必须响应式"]
    )

    restored = RequirementDigest.model_validate(original.model_dump())

    assert restored == original


def test_digest_prompt_text_separates_constraints_from_content() -> None:
    """⚠️ .md/.txt 常是"需求说明书"：要求进 constraints，素材进 content_points，两者不能混。"""
    digest = RequirementDigest(
        summary="待办清单需求说明书",
        role=ROLE_CONTENT,
        content_points=['页面标题可写"待办清单"'],
        constraints=["必须支持回车添加", "必须响应式"],
    )

    rendered = digest.as_prompt_text()

    assert "【内容素材】" in rendered
    assert "【硬要求】" in rendered
    assert rendered.index("【内容素材】") < rendered.index("【硬要求】")
    assert "必须支持回车添加" in rendered


def test_digest_prompt_text_shows_none_marker_for_empty_parts() -> None:
    """空的部分要写"（无）"，不能让下游以为漏读了内容。"""
    rendered = RequirementDigest().as_prompt_text()

    assert rendered.count("（无）") >= 3, "概述/内容素材/硬要求都应为空"


def test_digest_prompt_text_includes_style_section_when_present() -> None:
    """有风格规范时渲染出六类信息（这是 web-agent 复刻视觉的依据）。"""
    digest = RequirementDigest(
        summary="设计规范", role=ROLE_STYLE, style_spec=StyleSpec.from_design_tokens(TOKENS)
    )

    rendered = digest.as_prompt_text()

    assert "【风格规范】" in rendered
    assert "#0f172a" in rendered
    assert "Inter" in rendered
    assert "display:grid×2" in rendered


def test_digest_prompt_text_omits_style_section_when_empty() -> None:
    """没有风格信息时不要输出一个空的"【风格规范】"段（那会让模型以为风格已定）。"""
    rendered = RequirementDigest(summary="一篇要搬运的文章").as_prompt_text()

    assert "【风格规范】" not in rendered


def test_digest_prompt_text_includes_open_questions() -> None:
    """待确认项要显式传给下游（RAG 未命中 / 文档没说清时不能沉默）。"""
    rendered = RequirementDigest(open_questions=["是否需要多语言？"]).as_prompt_text()

    assert "【待确认】" in rendered
    assert "是否需要多语言？" in rendered
