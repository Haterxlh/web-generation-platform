# app/utils/doc/ —— 文档解析层（**无 LLM**）
#
# 分层（docs/agent_refactor_plan.md 阶段 3）：
#   解析层（本包）：文件 → ParsedDocument（正文 / 骨架 / 设计令牌），纯 Python，可离线单测；
#   理解层（app/agents/source/doc_digest_agent.py）：ParsedDocument → RequirementDigest，用模型。
#
# 本模块是**统一入口**，也是"支持哪些类型"这件事的唯一真源：
# 上传接口的类型白名单、digest 的输入、谁能当风格源，都从这里取判据，避免三处各写一份而漂移。

from pathlib import Path

from app.utils.doc.base import DocParseError, ParsedDocument, SourceType
from app.utils.doc.html_parser import parse_html, parse_html_text
from app.utils.doc.pdf_parser import parse_pdf
from app.utils.doc.text_parser import decode_bytes, parse_text

# 扩展名 → 源类型。扩展名是**首选判据**（上传时的 MIME 由浏览器给，常常是 application/octet-stream）
SUPPORTED_EXTENSIONS: dict[str, SourceType] = {
    ".pdf": "pdf",
    ".html": "html",
    ".htm": "html",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
}

# MIME → 源类型（仅当扩展名缺失或无法识别时的兜底）
SUPPORTED_MIMES: dict[str, SourceType] = {
    "application/pdf": "pdf",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/plain": "text",
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
}

# 能给用户看的支持列表（错误信息与前端提示共用一处措辞）
SUPPORTED_HINT = "支持的格式：.pdf / .html / .htm / .md / .txt"

# 单文件解析上限：10 MB。超过它的文本类文档没有实际价值（几百万字的需求说明书不存在），
# 而 PDF 超过这个体积基本都是扫描版，解析出来也只有图片。
MAX_PARSE_BYTES = 10 * 1024 * 1024

# 能当风格源的源类型（当前只有 HTML —— PDF/纯文本抽不出设计令牌，是能力问题不是偏好问题）
STYLE_CAPABLE_TYPES: frozenset[str] = frozenset({"html"})


def detect_source_type(filename: str, mime: str | None = None) -> SourceType:
    """判断一个上传文件的源类型。

    判据优先级：**扩展名 > MIME**。原因：浏览器给的 MIME 经常是
    ``application/octet-stream``，而用户把文件命名成 ``.md`` 是有意为之。

    Args:
        filename: 原始文件名（可能含路径，会被忽略）。
        mime: 浏览器声明的 MIME 类型，可为 None。

    Returns:
        源类型。

    Raises:
        DocParseError: 扩展名与 MIME 都不认识（错误信息里给出支持列表）。
    """
    suffix = Path(filename or "").suffix.lower()
    if suffix in SUPPORTED_EXTENSIONS:
        return SUPPORTED_EXTENSIONS[suffix]

    normalized_mime = (mime or "").split(";")[0].strip().lower()
    if normalized_mime in SUPPORTED_MIMES:
        return SUPPORTED_MIMES[normalized_mime]

    raise DocParseError(
        f"不支持的文件类型：{filename or '（未命名）'}"
        f"（扩展名 {suffix or '无'}、MIME {normalized_mime or '未提供'}）。{SUPPORTED_HINT}"
    )


def can_be_style_source(source_type: str) -> bool:
    """该源类型**有没有能力**当风格源。

    ⚠️ 这是"解析器能力"判据，用来否决模型的判定（判定权归模型、否决权归 Python）。
    模型完全可能把一份 ``.md`` 判成"风格源"（比如文档里写了"主色用深蓝"），
    但那种描述只能作为**文字约束**进 constraints，不能当结构化设计令牌用 ——
    因为下游拿不到任何可核对的取值。

    Args:
        source_type: 源类型（pdf / html / markdown / text）。

    Returns:
        True 表示可以当风格源。
    """
    return source_type in STYLE_CAPABLE_TYPES


def parse_document(
    path: Path | str, filename: str | None = None, mime: str | None = None
) -> ParsedDocument:
    """解析一个文档文件（统一入口）。

    Args:
        path: 磁盘路径。
        filename: 原始文件名（用于判断类型）；默认用 ``path`` 的文件名。
        mime: 浏览器声明的 MIME（可选兜底）。

    Returns:
        解析结果。

    Raises:
        DocParseError: 类型不支持、文件不存在 / 超限，或解析失败（编码、扫描版、加密等）。
    """
    file_path = Path(path)
    name = filename or file_path.name

    if not file_path.is_file():
        raise DocParseError(f"文件不存在或不是普通文件：{name}")

    size = file_path.stat().st_size
    if size == 0:
        raise DocParseError(f"{name} 是空文件，没有可用的内容")
    if size > MAX_PARSE_BYTES:
        raise DocParseError(
            f"{name} 大小 {size / 1024 / 1024:.1f} MB，超过上限 "
            f"{MAX_PARSE_BYTES // 1024 // 1024} MB"
        )

    source_type = detect_source_type(name, mime)

    if source_type == "pdf":
        return parse_pdf(file_path, label=name)
    if source_type == "html":
        return parse_html(file_path, label=name)
    # text / markdown 共用一套解析（区别只在骨架抽取）
    return parse_text(file_path, source_type=source_type, label=name)


__all__ = [
    "MAX_PARSE_BYTES",
    "STYLE_CAPABLE_TYPES",
    "SUPPORTED_EXTENSIONS",
    "SUPPORTED_HINT",
    "SUPPORTED_MIMES",
    "DocParseError",
    "ParsedDocument",
    "SourceType",
    "can_be_style_source",
    "decode_bytes",
    "detect_source_type",
    "parse_document",
    "parse_html",
    "parse_html_text",
    "parse_pdf",
    "parse_text",
]
