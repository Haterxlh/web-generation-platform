# app/agents/source/role_policy.py —— 附件角色裁决（纯函数：判定权归模型，否决权归 Python）
#
# 背景（docs/agent_refactor_plan.md 阶段 3 增补）：
#   "这份文件是内容源、风格源，还是两者都是" —— 这个问题**适合交给模型**，
#   因为它需要理解文件的语义（同样是 HTML，可能是设计规范，也可能是一篇要搬运的文章）。
#
#   但有一条**模型无法判断、也不该由它判断**的事：**解析器到底抽出了什么**。
#   模型看到"主色是深蓝"这句话，很可能把一份 .md 判成风格源 —— 可我们的 .md 解析器
#   只输出文字，下游拿不到任何可核对的配色取值，于是"风格源"变成一个空壳：
#   web-agent 既拿不到令牌，又会以为风格已经被定义过，从而不再追问。
#
#   所以这里做**单向否决**：模型说 style/both → 若解析器给不出设计令牌，
#   强制改回 content，并留下明确原因。判定权仍归模型，Python 只在"能力不允许"时说不。

from dataclasses import dataclass

from app.utils.doc.base import ParsedDocument

# 三个合法角色（与 generation_source.role 列、AttachmentRef.role 完全一致）
ROLE_CONTENT = "content"
ROLE_STYLE = "style"
ROLE_BOTH = "both"
VALID_ROLES: frozenset[str] = frozenset({ROLE_CONTENT, ROLE_STYLE, ROLE_BOTH})

# "需要设计令牌"的角色：只有这两个角色会用到结构化风格取值
_STYLE_ROLES: frozenset[str] = frozenset({ROLE_STYLE, ROLE_BOTH})

_TYPE_LABELS: dict[str, str] = {
    "pdf": "PDF",
    "html": "HTML",
    "markdown": "Markdown",
    "text": "纯文本",
}


@dataclass(frozen=True)
class RoleDecision:
    """角色裁决结果。

    ``overridden`` 与 ``reason`` 是刻意保留的：被否决时不只是"悄悄改成 content"，
    而是留下一句能进日志 / 前端提示的中文原因 —— 否则用户会觉得"我明明说了用它的风格，
    系统当没听见"。这也是本项目一贯的"不静默降级"。

    Attributes:
        role: 最终生效的角色。
        requested: 模型原本给的角色（None 表示模型没给）。
        overridden: 是否被 Python 否决过。
        reason: 否决原因；未被否决时为 None。
    """

    role: str
    requested: str | None
    overridden: bool
    reason: str | None


def resolve_role(requested: str | None, parsed: ParsedDocument) -> RoleDecision:
    """裁决一份文档的角色。

    规则只有三条，顺序执行：

    1. 模型没给角色 → ``content``（内容源是**安全侧**：最坏情况是素材被当正文用；
       反过来把素材当风格源，会让"风格"这个维度凭空消失）；
    2. 模型给了非法取值（不在 content/style/both 里）→ 记 ``overridden`` 并按 ``content``；
    3. 模型要 ``style`` / ``both``，但解析结果给不出设计令牌 → 否决为 ``content`` 并说明原因。

    Args:
        requested: 模型判定的角色（可为 None）。
        parsed: 该文件的解析结果（能力判据来自它）。

    Returns:
        角色裁决结果。
    """
    normalized = (requested or "").strip().lower()

    if not normalized:
        return RoleDecision(ROLE_CONTENT, None, False, None)

    if normalized not in VALID_ROLES:
        return RoleDecision(
            ROLE_CONTENT,
            requested,
            True,
            f"角色取值 {requested!r} 不是 content/style/both 之一，已按内容源处理",
        )

    if normalized in _STYLE_ROLES and not parsed.can_be_style_source():
        return RoleDecision(ROLE_CONTENT, normalized, True, _no_style_reason(parsed))

    return RoleDecision(normalized, normalized, False, None)


def _no_style_reason(parsed: ParsedDocument) -> str:
    """生成"为什么不能当风格源"的中文原因。

    两种情形要分开说 —— 用户能采取的行动完全不同：
    类型不支持（换个 HTML 传上来就行）vs HTML 里没有样式（这个文件本身就没有风格信息）。

    Args:
        parsed: 解析结果。

    Returns:
        面向用户的原因说明。
    """
    label = _TYPE_LABELS.get(parsed.source_type, parsed.source_type)
    if parsed.source_type != "html":
        return (
            f"该文件是 {label}，解析器只能抽取文字，拿不到配色/字体/圆角等设计令牌，"
            "已按内容源处理；若要复刻它的视觉风格，请上传对应的 .html 文件"
        )
    return (
        "该 HTML 里没有可识别的样式声明（无 <style>、无行内样式），抽不出设计令牌，"
        "已按内容源处理；请上传包含完整样式（或设计规范）的页面"
    )
