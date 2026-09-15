"""阶段 3 的离线测试（一）：文档解析层（无 LLM）。

这一层不连模型、不连数据库、不连 Redis —— 输入是磁盘上的文件，输出是 ``ParsedDocument``。
用例逐条对应一个真实会出事、且**后果严重**的场景：

- **GBK 的 .txt**：中文 Windows 记事本的默认编码。按 UTF-8 硬读会 500；
  用 ``errors="ignore"`` 会"成功"产出一份乱码需求 —— 后者更危险，所以这里断言**必须报错**而不是产出垃圾；
- **扫描版 PDF**：``extract_text()`` 会返回空串且不报错，若不拦就会让模型凭空编需求；
- **重复的内联样式**：构建工具会把整份 CSS 内联到每个元素上，不去重就把 prompt 撑爆；
- **只有样式没有正文的 HTML**：这是合法的"风格源"，不能当空文档丢掉。
"""

import io

import pytest
from pypdf import PdfWriter

from app.utils.doc import (
    MAX_PARSE_BYTES,
    can_be_style_source,
    detect_source_type,
    parse_document,
    parse_html_text,
)
from app.utils.doc.base import DocParseError, ParsedDocument

# --------------------------------------------------------------------------
# 工具：造测试文件（含一个手写的最小 PDF —— 只有真实字节才能验证 pypdf 链路）
# --------------------------------------------------------------------------


def _write(path, content, encoding: str = "utf-8"):
    """按指定编码写文件，返回路径。"""
    path.write_bytes(content.encode(encoding) if isinstance(content, str) else content)
    return path


def _build_text_pdf(text: str = "Hello WGP") -> bytes:
    """手工拼一个**带文本层**的最小 PDF。

    为什么不用库生成：pypdf 只擅长读，造"含文字的 PDF"需要 reportlab 这类重依赖。
    这里按 PDF 语法手写 5 个对象并算好 xref 偏移 —— 几十行换来"真字节验证解析链路"，
    比 mock 掉 PdfReader 有意义得多。
    """
    content = f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1")
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_pos = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _build_blank_pdf() -> bytes:
    """造一个**没有文本层**的 PDF（模拟扫描版/纯图片）。"""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


SAMPLE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>设计规范</title>
  <style>
    :root { --brand: #4f46e5; }
    body { background: #0f172a; color: #e2e8f0; font-family: "Inter", "Microsoft YaHei", sans-serif; font-size: 16px; }
    h1 { font-size: 32px; border-radius: 8px; padding: 24px; }
    .grid { display: grid; margin: 16px; }
    .row { display: flex; padding: 12px; }
    @media (max-width: 640px) { .grid { display: block; } }
  </style>
  <script>console.log("这段脚本不该出现在正文里");</script>
</head>
<body>
  <header id="top" class="site-header dark"><h1>企业官网设计规范</h1></header>
  <main>
    <section id="hero" class="hero-grid">
      <p style="color: #4f46e5; font-size: 18px;">主色用于行动按钮。</p>
      <p style="color: #4f46e5; font-size: 18px;">主色用于行动按钮。</p>
      <p style="color: #4f46e5; font-size: 18px;">主色用于行动按钮。</p>
    </section>
    <section id="tokens"><table><tr><td>圆角</td><td>8px</td></tr></table></section>
  </main>
  <footer class="site-footer">版权所有</footer>
</body>
</html>
"""


# --------------------------------------------------------------------------
# 1. 纯文本 / Markdown：编码回退是重点
# --------------------------------------------------------------------------


def test_utf8_text_parsed(tmp_path) -> None:
    """UTF-8 的 .txt 正常解析，并记录实际编码。"""
    path = _write(tmp_path / "需求.txt", "做一个待办清单页面", "utf-8")

    doc = parse_document(path)

    assert doc.source_type == "text"
    assert "做一个待办清单页面" in doc.text
    assert doc.encoding == "utf-8"
    assert doc.skeleton == []


def test_utf8_bom_is_stripped(tmp_path) -> None:
    """带 BOM 的 UTF-8 文件：BOM 必须被吃掉，否则正文首字符是 \\ufeff。"""
    path = _write(tmp_path / "bom.txt", "带 BOM 的需求", "utf-8-sig")

    doc = parse_document(path)

    assert doc.text.startswith("带 BOM 的需求")
    assert "\ufeff" not in doc.text


def test_gbk_text_is_decoded_not_mangled(tmp_path) -> None:
    """⚠️ 核心用例：GBK/GB18030 的中文 .txt 必须正确解码（这是中文 Windows 记事本的默认编码）。"""
    original = "需求说明：做一个带筛选的待办清单，风格极简白底。"
    path = _write(tmp_path / "gbk.txt", original, "gb18030")

    doc = parse_document(path)

    assert doc.encoding == "gb18030"
    assert original in doc.text, "中文必须完整还原，不能出现乱码"
    assert "\ufffd" not in doc.text, "绝不能出现替换字符（那是 errors='ignore' 的典型症状）"


def test_undecodable_bytes_raise_instead_of_returning_garbage(tmp_path) -> None:
    """⚠️ 既不是 UTF-8 也不是 GB18030 的字节必须**明确报错**。

    0xFF 在 GB18030 里不是合法首字节，因此这条数据两种编码都读不了 ——
    正确行为是报错并说明试过哪些编码，而不是静默返回空串或乱码。
    """
    path = _write(tmp_path / "broken.txt", b"\xff\xff\xff\xff")

    with pytest.raises(DocParseError) as excinfo:
        parse_document(path)

    message = str(excinfo.value)
    assert "utf-8" in message and "gb18030" in message
    assert "另存为 UTF-8" in message, "要给出用户可执行的下一步，而不只是报告失败"


def test_whitespace_only_text_is_rejected(tmp_path) -> None:
    """只有空白的文件等同空文档：不能产出一份"空需求"往下走。"""
    path = _write(tmp_path / "blank.txt", "   \n\n  \t ")

    with pytest.raises(DocParseError, match="空文件"):
        parse_document(path)


def test_crlf_is_normalized(tmp_path) -> None:
    """Windows 换行统一成 \\n：下游按行处理时不受平台差异影响。"""
    path = _write(tmp_path / "crlf.txt", "第一行\r\n第二行\r第三行")

    doc = parse_document(path)

    assert doc.text == "第一行\n第二行\n第三行"


def test_markdown_structure_is_preserved(tmp_path) -> None:
    """⚠️ .md 必须保留原始结构（标题/表格/代码块），并抽出行首标题当骨架。"""
    markdown = (
        "# 需求说明\n\n"
        "## 功能列表\n\n"
        "| 功能 | 说明 |\n| --- | --- |\n| 添加 | 支持回车提交 |\n\n"
        "```python\n# 这行是代码注释，不是标题\nprint(1)\n```\n\n"
        "### 约束\n- 必须响应式\n"
    )
    path = _write(tmp_path / "需求.md", markdown)

    doc = parse_document(path)

    assert doc.source_type == "markdown"
    assert doc.text == markdown, "正文必须原样保留，不做压平"
    assert doc.skeleton == ["# 需求说明", "## 功能列表", "### 约束"]
    assert "| 功能 | 说明 |" in doc.text, "表格结构不能丢"
    assert "print(1)" in doc.text, "代码块不能丢"


def test_markdown_heading_inside_fence_is_ignored(tmp_path) -> None:
    """代码块里以 # 开头的行不能被误当成标题。"""
    path = _write(tmp_path / "code.md", "```\n# 不是标题\n```\n\n## 真标题\n")

    doc = parse_document(path)

    assert doc.skeleton == ["## 真标题"]


# --------------------------------------------------------------------------
# 2. HTML：正文 / 骨架 / 设计令牌
# --------------------------------------------------------------------------


def test_html_body_excludes_script_and_style(tmp_path) -> None:
    """正文里不能混入 <script> 内容 —— 否则模型会把 JS 源码当成页面文案。"""
    path = _write(tmp_path / "spec.html", SAMPLE_HTML, "utf-8")

    doc = parse_document(path)

    assert doc.source_type == "html"
    assert "企业官网设计规范" in doc.text
    assert "这段脚本不该出现在正文里" not in doc.text
    assert "font-family" not in doc.text, "<style> 里的声明不是给人读的正文"


def test_html_skeleton_keeps_id_and_class() -> None:
    """骨架要带上 id/class，模型才能知道"哪一块是 hero、哪一块是页脚"。"""
    doc = parse_html_text(SAMPLE_HTML)

    assert "header#top.site-header.dark" in doc.skeleton
    assert "section#hero.hero-grid" in doc.skeleton
    assert "section#tokens" in doc.skeleton
    assert any(item.startswith("footer") for item in doc.skeleton)


def test_html_design_tokens_are_extracted() -> None:
    """⚠️ 设计令牌是"HTML 能当风格源"的事实依据，六类都要抽到。"""
    tokens = parse_html_text(SAMPLE_HTML).design_tokens

    assert "#0f172a" in tokens["colors"], "配色（含十六进制）"
    assert "#4f46e5" in tokens["colors"]
    assert "Inter" in tokens["font_families"], "字体族取首个并去掉引号"
    assert "32px" in tokens["font_sizes"], "字号阶梯"
    assert "8px" in tokens["border_radius"], "圆角"
    assert any("24px" in item for item in tokens["spacing"]), "间距"
    assert tokens["layout"]["display:grid"] >= 1, "布局方式"
    assert tokens["layout"]["display:flex"] >= 1
    assert tokens["layout"]["@media"] == 1, "媒体查询数量反映响应式设计"


def test_html_can_be_style_source_only_when_tokens_exist() -> None:
    """有设计令牌的 HTML 才能当风格源；没有令牌的 HTML 退回"只是内容"。"""
    assert parse_html_text(SAMPLE_HTML).can_be_style_source() is True
    assert parse_html_text("<html><body><p>只有文字</p></body></html>").can_be_style_source() is False


def test_html_without_any_style_warns() -> None:
    """没有样式声明的 HTML 要显式提示"当不了风格源"，不能静默给个空字典。"""
    doc = parse_html_text("<html><body><p>只有文字</p></body></html>")

    assert doc.design_tokens == {}
    assert any("不能作为风格源" in item for item in doc.warnings)


def test_style_only_html_is_kept_as_style_source() -> None:
    """⚠️ 只有样式、没有正文的 HTML 是**合法的风格源**，不能当成空文档丢掉。"""
    doc = parse_html_text('<html><head><style>body{color:#123456}</style></head><body></body></html>')

    assert doc.text.strip() == ""
    assert doc.design_tokens["colors"] == ["#123456"]
    assert any("只能作为风格源" in item for item in doc.warnings)
    assert doc.can_be_style_source() is True
    assert doc.is_empty is False, "有设计令牌就不算空文档"


def test_duplicate_inline_styles_are_dropped_once() -> None:
    """⚠️ 重复的行内样式只计一次，并明确告知忽略了多少段。

    SAMPLE_HTML 里那段 ``color: #4f46e5; font-size: 18px;`` 出现了 3 次：
    去重后应报"忽略 2 段"，且该颜色在语料里仍只来自第一次出现（不因重复而被放大为最高频）。
    """
    doc = parse_html_text(SAMPLE_HTML)

    assert any("忽略 2 段重复的行内样式" in item for item in doc.warnings)


def test_oversized_inline_style_is_dropped() -> None:
    """超长行内样式（构建工具内联的整份样式表）必须丢弃，否则会把 prompt 撑爆。"""
    huge = "color: #abcdef; " + "padding: 1px; " * 100  # 明显超过 400 字符
    doc = parse_html_text(f'<html><body><div style="{huge}">正文</div></body></html>')

    assert any("段超长行内样式" in item for item in doc.warnings)
    assert "colors" not in doc.design_tokens, "被丢弃的样式不参与令牌提取"


def test_html_with_gbk_encoding_is_parsed(tmp_path) -> None:
    """HTML 也走同一套编码回退：GBK 存的中文页面照样能读。"""
    html = '<html><body><p>中文页面正文</p></body></html>'
    path = _write(tmp_path / "page.html", html, "gb18030")

    doc = parse_document(path)

    assert "中文页面正文" in doc.text


def test_empty_html_is_rejected() -> None:
    """空 HTML 不是"空文档"，而是错误。"""
    with pytest.raises(DocParseError, match="空文件"):
        parse_html_text("   ")


# --------------------------------------------------------------------------
# 3. PDF：扫描版必须报错
# --------------------------------------------------------------------------


def test_pdf_with_text_layer_is_parsed(tmp_path) -> None:
    """带文本层的 PDF：抽到文字、页数正确、按页给出 native_blocks。"""
    body = "Hello WGP PDF parser test page"
    path = _write(tmp_path / "spec.pdf", _build_text_pdf(body))

    doc = parse_document(path)

    assert doc.source_type == "pdf"
    assert body in doc.text
    assert doc.page_count == 1
    assert doc.native_blocks == [body], "PDF 的天然分块就是页"
    assert doc.encoding is None
    assert doc.can_be_style_source() is False, "PDF 抽不出设计令牌，永远当不了风格源"


def test_pdf_with_too_little_text_is_treated_as_scanned(tmp_path) -> None:
    """⚠️ 阈值是刻意设的：只有几个字符的 PDF 与扫描件等价（内容等于没有），

    真实场景里带文本层的 PDF 不可能只有两三个字符；而扫描件常会残留一两个乱码字符，
    阈值太低就会把这种噪声当正文送进模型。
    """
    path = _write(tmp_path / "tiny.pdf", _build_text_pdf("Hi"))

    with pytest.raises(DocParseError, match="没有可提取的文字"):
        parse_document(path)


def test_scanned_pdf_raises_instead_of_empty_digest(tmp_path) -> None:
    """⚠️ 核心用例：扫描版（无文本层）必须明确报错，不能"成功"返回空需求。"""
    path = _write(tmp_path / "scan.pdf", _build_blank_pdf())

    with pytest.raises(DocParseError) as excinfo:
        parse_document(path)

    message = str(excinfo.value)
    assert "没有可提取的文字" in message
    assert "扫描版" in message
    assert ".txt / .md" in message, "要给出可行的替代方案"


def test_fake_pdf_raises_clear_error(tmp_path) -> None:
    """把文本文件改名成 .pdf：报"无法作为 PDF 读取"，而不是抛 pypdf 的原始异常。"""
    path = _write(tmp_path / "fake.pdf", "这其实是一段纯文本")

    with pytest.raises(DocParseError, match="无法作为 PDF 读取"):
        parse_document(path)


# --------------------------------------------------------------------------
# 4. 类型识别与统一入口的边界
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("a.pdf", "pdf"),
        ("a.PDF", "pdf"),
        ("a.html", "html"),
        ("a.htm", "html"),
        ("a.md", "markdown"),
        ("a.txt", "text"),
        ("C:/tmp/报告.md", "markdown"),
    ],
)
def test_detect_source_type_by_extension(filename: str, expected: str) -> None:
    """扩展名是首选判据，且大小写不敏感、忽略路径。"""
    assert detect_source_type(filename) == expected


def test_extension_wins_over_mime() -> None:
    """扩展名优先于 MIME：浏览器常给出 application/octet-stream，不能因此拒绝。"""
    assert detect_source_type("需求.md", "application/octet-stream") == "markdown"


def test_mime_is_used_when_extension_missing() -> None:
    """没有扩展名时用 MIME 兜底。"""
    assert detect_source_type("upload", "application/pdf") == "pdf"
    assert detect_source_type("upload", "text/plain; charset=utf-8") == "text"


def test_unsupported_type_lists_supported_formats() -> None:
    """不支持的类型要报出支持列表（用户才知道该怎么办）。"""
    with pytest.raises(DocParseError) as excinfo:
        detect_source_type("需求.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    assert ".pdf / .html / .htm / .md / .txt" in str(excinfo.value)


def test_parse_document_rejects_missing_file(tmp_path) -> None:
    """文件不存在时给中文原因，而不是让 FileNotFoundError 冒到接口层。"""
    with pytest.raises(DocParseError, match="文件不存在"):
        parse_document(tmp_path / "nope.txt")


def test_parse_document_rejects_empty_file(tmp_path) -> None:
    """0 字节文件在上传阶段就该被拦住。"""
    path = _write(tmp_path / "empty.txt", b"")

    with pytest.raises(DocParseError, match="空文件"):
        parse_document(path)


def test_parse_document_rejects_oversized_file(tmp_path, monkeypatch) -> None:
    """超过上限的文件必须拒绝（避免把畸形大文件读进内存）。"""
    monkeypatch.setattr("app.utils.doc.MAX_PARSE_BYTES", 10)
    path = _write(tmp_path / "big.txt", "x" * 100)

    with pytest.raises(DocParseError, match="超过上限"):
        parse_document(path)


def test_parse_document_uses_mime_for_extensionless_file(tmp_path) -> None:
    """真实场景：前端上传时文件名可能没有扩展名，此时靠 MIME 判断。"""
    path = _write(tmp_path / "upload", "一句话需求", "utf-8")

    doc = parse_document(path, filename="upload", mime="text/plain")

    assert doc.source_type == "text"
    assert "一句话需求" in doc.text


def test_max_parse_bytes_is_10mb() -> None:
    """上限值本身也是约定（上传接口复用同一个常量，避免两处不一致）。"""
    assert MAX_PARSE_BYTES == 10 * 1024 * 1024


# --------------------------------------------------------------------------
# 5. 风格源能力判据与渲染
# --------------------------------------------------------------------------


def test_can_be_style_source_only_html() -> None:
    """⚠️ 只有 HTML 能当风格源 —— 这是"解析器能力"判据，Python 侧据此否决模型判定。"""
    assert can_be_style_source("html") is True
    assert can_be_style_source("pdf") is False
    assert can_be_style_source("markdown") is False
    assert can_be_style_source("text") is False


def test_render_for_model_includes_all_sections() -> None:
    """渲染给模型看的文本里，正文/骨架/设计令牌都要在（HTML 的配色不在正文里）。"""
    rendered = parse_html_text(SAMPLE_HTML).render_for_model()

    assert "【文档类型】html" in rendered
    assert "【正文】" in rendered
    assert "【区块骨架】" in rendered
    assert "【设计令牌】" in rendered
    assert "#0f172a" in rendered


def test_render_for_model_marks_truncation() -> None:
    """⚠️ 截断必须显式标注并给出原文长度，绝不静默丢内容。"""
    doc = ParsedDocument(source_type="text", text="甲" * 100)

    rendered = doc.render_for_model(max_chars=10)

    assert "已截断" in rendered
    assert "原文共 100 字符" in rendered


def test_char_count_and_is_empty() -> None:
    """两个小属性是下游判断"要不要分块 / 文档是否为空"的依据。"""
    doc = ParsedDocument(source_type="text", text="abc")

    assert doc.char_count == 3
    assert doc.is_empty is False
    assert ParsedDocument(source_type="text").is_empty is True
