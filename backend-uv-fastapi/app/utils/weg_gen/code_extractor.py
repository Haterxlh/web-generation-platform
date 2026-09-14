# app/utils/code_extractor.py —— 从模型回复中提取代码块，并映射成固定文件名
# 职责边界：只做"文本 → {文件名: 内容}"；不写磁盘、不碰数据库、不调用模型
# 与提示词的契约：语言标记 html / css / js 各一个代码块（见 app/prompts/*.md 的输出格式段）

import re
from dataclasses import dataclass

# 期望的产物；元组的顺序 = "按出现顺序补位"时的补位顺序
EXPECTED_SINGLE = ("index.html",)
EXPECTED_MULTI = ("index.html", "style.css", "script.js")

# 语言标记 → 固定文件名。含常见别名：模型不一定守规矩，别名必须兜住
LANG_TO_FILENAME = {
    "html": "index.html",
    "htm": "index.html",
    "css": "style.css",
    "js": "script.js",
    "javascript": "script.js",
    "ecmascript": "script.js",
}

# 标题行里找文件名，如 "### style.css" / "**script.js**"
_FILENAME_RE = re.compile(r"([A-Za-z0-9_-]+\.(?:html|htm|css|js))\b", re.IGNORECASE)


class CodeExtractError(ValueError):
    """模型输出里缺少必需的代码块，或代码块格式不符。

    Attributes:
        missing: 缺失的期望文件名（供上层生成"面向模型"的修正指令）。
        found: 实际识别到的文件名（供诊断）。
    """

    def __init__(
        self,
        message: str,
        missing: list[str] | None = None,
        found: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.missing = missing or []
        self.found = found or []


@dataclass
class CodeBlock:
    """一个被切出来的代码块。

    Attributes:
        lang: 语言标记（已转小写，可能为空串）。
        heading: 本代码块之前最近的一行非空文本（常是 "### style.css" 这类文件名标注）。
        content: 代码正文。
    """

    lang: str
    heading: str
    content: str


def extract_code_blocks(text: str) -> list[CodeBlock]:
    """把 markdown 文本切成代码块列表（按出现顺序）。

    为什么逐行扫描而不是一个正则搞定：围栏起止、语言标记、标题行的关系用状态机表达最清楚，
    也方便在"围栏忘了收尾"这类脏输出上做容错。

    Args:
        text: 模型返回的原始文本。

    Returns:
        代码块列表。
    """
    blocks: list[CodeBlock] = []
    lang: str | None = None  # None 表示"当前不在代码块里"
    heading = ""
    buf: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if line.startswith("```"):
            info = line[3:].strip().lower()  # 围栏行上的语言标记（收尾围栏这里是空串）

            if lang is None:
                # 不在块里 → 这是开始围栏
                lang = info
                buf = []
            elif info:
                # 已在块里，但这条围栏带语言标记 → 按 CommonMark，收尾围栏**不带** info string，
                # 所以它其实是下一条开始围栏：先收掉当前块，再开新块。
                # 这条规则能救"模型漏写收尾围栏"的脏输出 ——
                # 否则 ```css 会被当成 html 块的收尾，导致整个 css 块消失。
                blocks.append(CodeBlock(lang=lang, heading=heading, content="\n".join(buf).strip()))
                lang = info
                buf = []
            else:
                # 正常的收尾围栏（不带语言标记）
                blocks.append(CodeBlock(lang=lang, heading=heading, content="\n".join(buf).strip()))
                lang = None
            continue

        if lang is None:
            if line:  # 代码块之外：记住最近一行非空文本，作为"标题行"
                heading = line
        else:
            buf.append(raw_line)

    # 容错：模型忘了写收尾的 ```，把剩下的内容也算一块（总比整段丢掉强）
    if lang is not None:
        blocks.append(CodeBlock(lang=lang, heading=heading, content="\n".join(buf).strip()))

    return blocks


def _filename_for(block: CodeBlock, fallback: str) -> str:
    """判断一个代码块该存成什么文件名。

    优先级：语言标记 > 标题行里的文件名 > fallback（按出现顺序补位）。

    Args:
        block: 待判断的代码块。
        fallback: 前两种方式都认不出时的兜底文件名（可为空串表示"不兜底"）。

    Returns:
        文件名。
    """
    if block.lang in LANG_TO_FILENAME:
        return LANG_TO_FILENAME[block.lang]

    match = _FILENAME_RE.search(block.heading)
    if match:
        name = match.group(1).lower()
        if name.endswith((".htm", ".html")):
            return "index.html"
        if name.endswith(".css"):
            return "style.css"
        if name.endswith(".js"):
            return "script.js"

    return fallback


def _collect(text: str, expected: tuple[str, ...]) -> dict[str, str]:
    """按期望的文件名收集代码块内容。

    Args:
        text: 模型返回的原始文本。
        expected: 期望得到的文件名，顺序用于按位补位。

    Returns:
        {文件名: 内容}。

    Raises:
        CodeExtractError: 有期望的文件名没被收集到。
    """
    result: dict[str, str] = {}

    for index, block in enumerate(extract_code_blocks(text)):
        fallback = expected[index] if index < len(expected) else ""
        name = _filename_for(block, fallback)
        # 认出来的名字不在期望清单里（比如单文件模式里混进一个 css 块）→ 直接忽略
        if name not in expected or not block.content:
            continue
        # 同类型已有内容时保留更长的那个（完整版通常更长）
        if name not in result or len(block.content) > len(result[name]):
            result[name] = block.content

    missing = [name for name in expected if name not in result]
    if missing:
        raise CodeExtractError(
            f"模型输出缺少必需的代码块：{missing}；"
            f"实际识别到：{sorted(result) or '无'}（请检查提示词的输出格式约定）",
            missing=missing,
            found=sorted(result),
        )
    return result


def extract_single_html(text: str) -> dict[str, str]:
    """单文件模式：取出完整 HTML。

    Args:
        text: 模型返回的原始文本。

    Returns:
        {"index.html": "<!DOCTYPE html>..."}

    Raises:
        CodeExtractError: 没找到可用的 HTML 代码块。
    """
    return _collect(text, EXPECTED_SINGLE)


def extract_multi_files(text: str) -> dict[str, str]:
    """多文件模式：取出 html / css / js 三块并映射为固定文件名。

    Args:
        text: 模型返回的原始文本。

    Returns:
        {"index.html": ..., "style.css": ..., "script.js": ...}

    Raises:
        CodeExtractError: 三个文件有缺失。
    """
    return _collect(text, EXPECTED_MULTI)