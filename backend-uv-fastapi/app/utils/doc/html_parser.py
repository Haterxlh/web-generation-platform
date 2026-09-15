# app/utils/doc/html_parser.py —— HTML 解析（无 LLM）
#
# 产出三样东西（对应 ParsedDocument 的三个字段）：
#   1. 正文文本   —— 剥掉 script/style 等非内容标签后的可见文字；
#   2. 区块骨架   —— header/nav/main/section/footer… 的结构轮廓，让模型知道页面由哪几块组成；
#   3. 设计令牌   —— 配色 / 字体 / 字号阶梯 / 圆角 / 间距 / 布局。
#
# ⚠️ 设计令牌是**只有 HTML 能当风格源**的事实依据（见 ParsedDocument.can_be_style_source）：
# PDF 只能抽出文字，抽不出 "#0f172a 深色底 + 8px 圆角" 这类可复用的视觉规范。
#
# 两个刻意的取舍：
#   - **不做 CSS 级联计算**：不引入 tinycss2/浏览器引擎，只做"声明频次统计"。
#     风格源的价值在"有哪些主色、用什么字体、圆角多大"，不在精确复现层叠结果；
#     引入完整 CSS 解析器会让这一层变重，收益却很小。
#   - **重复的行内样式直接丢弃**（不是去重后计数）：构建工具会把整份样式表内联到每个元素上，
#     几万字符的重复声明里没有任何新信息，只会把 prompt 撑爆。

import re
from collections import Counter
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from app.utils.doc.base import DocParseError, ParsedDocument

# 复用文本层的编码回退（UTF-8 → GB18030）：HTML 声明里的 charset 不可信，
# 真正的判据只能是字节本身 —— 两处共用一套口径，避免"同一个文件两种结论"
from app.utils.doc.text_parser import decode_bytes

# 这些标签里的文字不是"给人读的正文"，抽取前先剥掉。
# ⚠️ style 单独处理：它的**内容**是设计令牌的来源，要先收集再剥。
DROP_TAGS: tuple[str, ...] = ("script", "noscript", "template", "svg", "iframe", "canvas")

# 进入骨架的结构性标签（正文标签 h1~h3 也进，方便模型了解信息层级）
STRUCTURE_TAGS: tuple[str, ...] = (
    "header", "nav", "main", "section", "article", "aside",
    "footer", "form", "table", "h1", "h2", "h3",
)

# 单段行内样式超过这个长度就丢弃：通常是构建工具内联的整份样式表
MAX_INLINE_STYLE_CHARS = 400
# CSS 语料总长度上限（防止畸形文件把内存与 prompt 拖垮）
MAX_CSS_CHARS = 200_000
# 骨架条数上限：骨架是"目录"，不是正文
MAX_SKELETON_ITEMS = 60
# 设计令牌每类保留多少个取值（按出现频次取前 N）
MAX_TOKEN_VALUES = 8

_COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)")
# 值的字符类刻意用 [^;}]+ 而不是排除引号：字体族常写成 "Inter", "微软雅黑"，
# 一旦把引号排除在外，整条 font-family 声明都匹配不到（正则不会"跳过引号再匹配"），
# 于是字体令牌静默丢失 —— 引号由 _first_font_family 单独剥掉。
_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;}]+)")
_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*([^;}]+)")
_RADIUS_RE = re.compile(r"border-radius\s*:\s*([^;}]+)")
_SPACING_RE = re.compile(r"(?:margin|padding)(?:-(?:top|right|bottom|left))?\s*:\s*([^;}]+)")
_DISPLAY_RE = re.compile(r"display\s*:\s*([a-z-]+)")
_MEDIA_RE = re.compile(r"@media\b")


def parse_html(path: Path, label: str | None = None) -> ParsedDocument:
    """解析 ``.html`` / ``.htm`` 文件。

    Args:
        path: 文件路径。
        label: 出错信息里显示的来源名；默认用文件名。

    Returns:
        解析结果（正文 + 骨架 + 设计令牌）。

    Raises:
        DocParseError: 文件无法按 HTML 读取。
    """
    name = label or path.name
    try:
        markup, _encoding = decode_bytes(path.read_bytes(), label=name)
    except OSError as error:
        raise DocParseError(f"{name} 读取失败：{error}") from error

    return parse_html_text(markup, label=name)


def parse_html_text(markup: str, label: str = "HTML") -> ParsedDocument:
    """解析一段 HTML 文本（与 :func:`parse_html` 共用逻辑，便于单测直接喂字符串）。

    Args:
        markup: HTML 源码。
        label: 出错信息里显示的来源名。

    Returns:
        解析结果。

    Raises:
        DocParseError: 源码为空。
    """
    if not markup.strip():
        raise DocParseError(f"{label} 是空文件，没有可用的内容")

    # "html.parser" 是标准库实现：不必额外装 lxml，行为对畸形 HTML 也足够宽容
    soup = BeautifulSoup(markup, "html.parser")

    css_corpus, warnings = _collect_css(soup)
    design_tokens = _extract_design_tokens(css_corpus)

    # ⚠️ 先收集完 <style> 再剥标签，顺序反了就拿不到样式了
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all("style"):
        tag.decompose()

    text = _extract_text(soup)
    skeleton = _extract_skeleton(soup)

    if not design_tokens:
        warnings.append(
            "文档里没有可识别的样式声明（无 <style>、无行内样式），抽不出设计令牌 —— 这份文档不能作为风格源"
        )
    if not text.strip() and design_tokens:
        warnings.append("该 HTML 没有可见正文，只能作为风格源使用")

    return ParsedDocument(
        source_type="html",
        text=text,
        skeleton=skeleton,
        design_tokens=design_tokens,
        warnings=warnings,
    )


def _collect_css(soup: BeautifulSoup) -> tuple[str, list[str]]:
    """收集用于提取设计令牌的样式声明语料。

    来源两处：``<style>`` 块 + 元素的行内 ``style`` 属性。

    Args:
        soup: 已解析的 DOM。

    Returns:
        (样式语料, 提示信息)。
    """
    chunks: list[str] = []
    warnings: list[str] = []

    for style in soup.find_all("style"):
        chunks.append(style.get_text(" ", strip=True))

    seen: set[str] = set()
    skipped_duplicate = 0
    skipped_too_long = 0
    for tag in soup.find_all(style=True):
        raw = str(tag.get("style") or "")
        normalized = " ".join(raw.split())
        if not normalized:
            continue
        if len(normalized) > MAX_INLINE_STYLE_CHARS:
            skipped_too_long += 1
            continue
        if normalized in seen:
            # 同一段样式在页面上重复出现：第一次已经记过，重复的没有新增信息
            skipped_duplicate += 1
            continue
        seen.add(normalized)
        chunks.append(normalized)

    if skipped_duplicate:
        warnings.append(f"已忽略 {skipped_duplicate} 段重复的行内样式（重复出现不带来新信息）")
    if skipped_too_long:
        warnings.append(
            f"已忽略 {skipped_too_long} 段超长行内样式（超过 {MAX_INLINE_STYLE_CHARS} 字符，"
            "通常是构建工具把整份样式表内联到了元素上）"
        )

    corpus = "\n".join(chunk for chunk in chunks if chunk)
    if len(corpus) > MAX_CSS_CHARS:
        corpus = corpus[:MAX_CSS_CHARS]
        warnings.append(f"样式文本过长，已截断到 {MAX_CSS_CHARS} 字符后提取设计令牌")

    return corpus, warnings


def _extract_design_tokens(corpus: str) -> dict[str, Any]:
    """从样式语料里统计出设计令牌（按出现频次取前 N）。

    Args:
        corpus: 样式声明文本。

    Returns:
        设计令牌字典；什么也没抽到时返回空字典。
    """
    if not corpus.strip():
        return {}

    tokens: dict[str, Any] = {}

    colors = _top_values(_COLOR_RE.findall(corpus), normalize=str.lower)
    if colors:
        tokens["colors"] = colors

    fonts = _top_values(
        [_first_font_family(item) for item in _FONT_FAMILY_RE.findall(corpus)], limit=5
    )
    if fonts:
        tokens["font_families"] = fonts

    sizes = _top_values(_FONT_SIZE_RE.findall(corpus))
    if sizes:
        tokens["font_sizes"] = sizes

    radii = _top_values(_RADIUS_RE.findall(corpus), limit=6)
    if radii:
        tokens["border_radius"] = radii

    spacing = _top_values(_SPACING_RE.findall(corpus))
    if spacing:
        tokens["spacing"] = spacing

    layout: dict[str, int] = {}
    for value, count in Counter(_DISPLAY_RE.findall(corpus)).most_common(6):
        layout[f"display:{value}"] = count
    media_count = len(_MEDIA_RE.findall(corpus))
    if media_count:
        layout["@media"] = media_count
    if layout:
        tokens["layout"] = layout

    return tokens


def _first_font_family(declaration: str) -> str:
    """取 ``font-family`` 声明里的首个字体族（去掉引号）。

    Args:
        declaration: 形如 ``"Inter", "Microsoft YaHei", sans-serif``。

    Returns:
        首个字体族名；为空时返回空串（调用方会过滤掉）。
    """
    first = declaration.split(",")[0]
    return first.strip().strip("\"'").strip()


def _top_values(
    values: list[str], limit: int = MAX_TOKEN_VALUES, normalize: Any = None
) -> list[str]:
    """按出现频次排序并取前 N 个去重取值。

    Args:
        values: 原始取值列表（可能含空串与重复）。
        limit: 最多返回多少个。
        normalize: 可选的归一化函数（如 ``str.lower``）。

    Returns:
        频次从高到低的取值列表。
    """
    cleaned: list[str] = []
    for value in values:
        item = " ".join(str(value).split())
        if not item:
            continue
        cleaned.append(normalize(item) if normalize else item)
    return [value for value, _count in Counter(cleaned).most_common(limit)]


def _extract_text(soup: BeautifulSoup) -> str:
    """抽出可见正文（丢掉空行，保留块级换行）。

    Args:
        soup: 已剥掉非内容标签的 DOM。

    Returns:
        正文文本。
    """
    raw = soup.get_text("\n")
    lines = [" ".join(line.split()) for line in raw.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_skeleton(soup: BeautifulSoup) -> list[str]:
    """抽出区块骨架（结构性标签 + id/class 选择器）。

    Args:
        soup: DOM。

    Returns:
        形如 ``["header#top.site-header", "section#hero"]`` 的列表。
    """
    skeleton: list[str] = []
    for tag in soup.find_all(STRUCTURE_TAGS):
        selector = _selector_of(tag)
        if selector and selector not in skeleton:
            skeleton.append(selector)
        if len(skeleton) >= MAX_SKELETON_ITEMS:
            break
    return skeleton


def _selector_of(tag: Any) -> str:
    """把一个标签渲染成简短的 CSS 选择器（最多带 3 个 class）。

    Args:
        tag: BeautifulSoup 标签。

    Returns:
        形如 ``section#hero.hero-grid.dark``；无 id/class 时只返回标签名。
    """
    name = tag.name or ""
    if not name:
        return ""
    tag_id = tag.get("id")
    if tag_id:
        name += f"#{tag_id}"
    classes = tag.get("class") or []
    if classes:
        name += "." + ".".join(list(classes)[:3])
    return name
