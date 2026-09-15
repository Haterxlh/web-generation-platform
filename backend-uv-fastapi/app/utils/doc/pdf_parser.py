# app/utils/doc/pdf_parser.py —— PDF 解析（无 LLM，按页抽文本）
#
# ⚠️ 本模块最重要的一条：**扫描版 PDF 必须明确报错**。
#
# 扫描版 PDF 里没有文本层（每页只是一张图），`extract_text()` 会"成功地"返回空串。
# 若把这个空串当成正常结果交给 digest 模型，它会一本正经地编出一份需求摘要 ——
# 用户看到的是"生成成功但完全不是我想要的"，比直接报错难排查得多。
# 所以这里用"文本总量低于阈值"作为硬判据，宁可报错也不静默产出空需求。
#
# 反过来，PDF **永远只能当内容源**：pypdf 只出文字，
# 抽不出配色 / 字体 / 圆角，因此它没有资格当风格源（见 ParsedDocument.can_be_style_source）。

from pathlib import Path

from pypdf import PdfReader

from app.utils.doc.base import DocParseError, ParsedDocument

# 少于这么多字符就认定"没有可用文本层"。
# 取 20 而不是 0：有些扫描件会带一层几乎无意义的隐藏 OCR 残留（一两个乱码字符），
# 阈值太低会把这种垃圾当正文送进模型。
MIN_TEXT_CHARS = 20


def parse_pdf(path: Path, label: str | None = None) -> ParsedDocument:
    """解析 PDF：逐页抽文本。

    Args:
        path: 文件路径。
        label: 出错信息里显示的来源名；默认用文件名。

    Returns:
        解析结果（``text`` / ``native_blocks`` 按页 / ``page_count``）。

    Raises:
        DocParseError: 文件不是合法 PDF、已加密、没有页面，或没有可用文本层（扫描版）。
    """
    name = label or path.name

    try:
        reader = PdfReader(str(path))
    except Exception as error:  # pypdf 的异常类型较杂，统一翻译成面向用户的原因
        raise DocParseError(f"{name} 无法作为 PDF 读取：{error}") from error

    # 加密文件：先试空口令（很多 PDF 只设了"权限口令"，空口令即可打开）
    if reader.is_encrypted:
        try:
            opened = reader.decrypt("")
        except Exception:  # noqa: BLE001 —— 解密失败一律按"需要密码"处理
            opened = 0
        if not opened:
            raise DocParseError(
                f"{name} 已加密，需要密码才能读取；请先解密或另存为无密码的 PDF 再上传"
            )

    try:
        pages = list(reader.pages)
    except Exception as error:
        raise DocParseError(f"{name} 的页面无法打开：{error}") from error

    if not pages:
        raise DocParseError(f"{name} 里没有任何页面")

    page_texts: list[str] = []
    blank_pages: list[int] = []
    failed_pages: list[int] = []

    for index, page in enumerate(pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:  # noqa: BLE001 —— 单页坏掉不该毁掉整份文档
            failed_pages.append(index)
            text = ""
        if not text:
            blank_pages.append(index)
        page_texts.append(text)

    total_chars = sum(len(item) for item in page_texts)
    if total_chars < MIN_TEXT_CHARS:
        raise DocParseError(
            f"{name} 里没有可提取的文字（共 {len(pages)} 页，疑似扫描版或纯图片 PDF）："
            "请改用带文本层的 PDF，或把需求直接写在 .txt / .md 文件里上传"
        )

    warnings: list[str] = []
    if failed_pages:
        warnings.append(
            "以下页面解析失败，已跳过：" + "、".join(f"第 {index} 页" for index in failed_pages)
        )
    # 有文本但有整页空白的，说明可能是"图文混排"或部分扫描，明确提示而不是假装完整
    if blank_pages and len(blank_pages) < len(pages):
        warnings.append(
            f"{len(blank_pages)} 页没有文字（可能是图片页）："
            + "、".join(f"第 {index} 页" for index in blank_pages[:10])
        )

    return ParsedDocument(
        source_type="pdf",
        text="\n\n".join(page_texts),
        # 天然的按页分块：给 map-reduce 用，比按字符硬切更不容易把一句话劈成两半
        native_blocks=[item for item in page_texts if item],
        page_count=len(pages),
        warnings=warnings,
    )
