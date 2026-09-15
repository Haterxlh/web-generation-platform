# app/utils/doc/text_parser.py —— 纯文本 / Markdown 解析（无 LLM）
#
# ⚠️ 本模块存在的最大理由：**中文 Windows 记事本默认存 GBK/GB18030，不是 UTF-8**。
#
# 天真写法的两种后果（都真实存在）：
#   1. `path.read_text(encoding="utf-8")` → 用户上传的中文 .txt 直接 UnicodeDecodeError，
#      接口 500，用户只看到"服务器错误"；
#   2. `errors="ignore"`（看似"更健壮"）→ 不报错，但整篇中文变乱码，
#      再喂给模型就得到一份**语法通顺、内容全错的**需求摘要 —— 这比报错危险得多。
#
# 正确做法：按 UTF-8 → GB18030 依次尝试，**都失败才明确报错**。
# GB18030 是 GBK 的超集，覆盖绝大多数简体中文遗留文件；顺带也覆盖了 UTF-8 的"合法字节子集"问题：
# 顺序不能反过来（GB18030 能"成功"解码很多 UTF-8 字节序列，只是内容变成乱码）。

import re
from pathlib import Path

from app.utils.doc.base import DocParseError, ParsedDocument, SourceType

# 尝试顺序即优先级：UTF-8 优先（现代默认），失败再退 GB18030（中文旧文件）。
# "utf-8-sig" 同时兼容"带 BOM"与"不带 BOM"的 UTF-8，且会自动吃掉 BOM ——
# 否则正文第一个字符会是 \ufeff，白白浪费一个 token 还可能干扰模型。
ENCODING_CANDIDATES: tuple[str, ...] = ("utf-8-sig", "gb18030")

# 对外汇报时把 utf-8-sig 归一成 utf-8：BOM 已被剥掉，用户不必知道这个细节
_ENCODING_LABEL = {"utf-8-sig": "utf-8"}

# Markdown 标题：`## 标题`（1~6 级）。只用来抽骨架，不做任何结构改写
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

# 骨架最多保留多少条：骨架是"目录"，不是正文，长了反而挤占 prompt
MAX_SKELETON_ITEMS = 40


def decode_bytes(data: bytes, label: str = "文件") -> tuple[str, str]:
    """按 UTF-8 → GB18030 的顺序把字节解码成文本。

    Args:
        data: 文件原始字节。
        label: 出错信息里显示的来源名（如文件名）。

    Returns:
        (文本, 实际使用的编码名)。

    Raises:
        DocParseError: 所有候选编码都解码失败。
    """
    errors: list[str] = []
    for encoding in ENCODING_CANDIDATES:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError as error:
            errors.append(f"{encoding}（{error.reason}，位置 {error.start}）")
            continue
        return text.replace("\r\n", "\n").replace("\r", "\n"), _ENCODING_LABEL.get(
            encoding, encoding
        )

    # ⚠️ 绝不返回空串蒙混过关：明确告诉用户"这个文件读不了"，让他另存为 UTF-8 再传
    raise DocParseError(
        f"{label}不是可识别的文本编码：已依次尝试 {'、'.join(errors)}。"
        "请用记事本或编辑器另存为 UTF-8 后重新上传。"
    )


def parse_text(path: Path, source_type: SourceType = "text", label: str | None = None) -> ParsedDocument:
    """解析 ``.txt`` / ``.md`` 文件。

    ``.md`` 与 ``.txt`` 的区别只有两点（都刻意保持最小）：

    1. ``.md`` 会抽**标题大纲**进 ``skeleton``（让模型知道文档讲了哪几块）；
    2. ``.md`` 的正文**原样保留** Markdown 语法 —— 表格与代码块本身就是信息，
       压平成纯文本等于替模型做决定，还会丢掉"这是示例代码"这类语义。

    Args:
        path: 文件路径。
        source_type: ``text`` 或 ``markdown``。
        label: 出错信息里显示的来源名；默认用文件名。

    Returns:
        解析结果（``text`` / ``skeleton`` / ``encoding``）。

    Raises:
        DocParseError: 编码无法识别，或文件内容为空。
    """
    name = label or path.name
    text, encoding = decode_bytes(path.read_bytes(), label=name)

    if not text.strip():
        raise DocParseError(f"{name} 是空文件，没有可用的内容")

    skeleton = extract_markdown_skeleton(text) if source_type == "markdown" else []

    warnings: list[str] = []
    if source_type == "markdown":
        warnings.append("已保留 Markdown 原始结构（标题/表格/代码块），未做压平处理")

    return ParsedDocument(
        source_type=source_type,
        text=text,
        skeleton=skeleton,
        encoding=encoding,
        warnings=warnings,
    )


def extract_markdown_skeleton(text: str) -> list[str]:
    """抽出 Markdown 的标题大纲（保持原文顺序）。

    只认"行首的 ``#``"，代码块里以 ``#`` 开头的注释不会被误当成标题 ——
    因为代码块（``` 围栏）内的行会先被跳过。

    Args:
        text: Markdown 正文。

    Returns:
        形如 ``["# 需求说明", "## 功能列表"]`` 的列表（最多 ``MAX_SKELETON_ITEMS`` 条）。
    """
    skeleton: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _MD_HEADING_RE.match(stripped)
        if match:
            skeleton.append(f"{match.group(1)} {match.group(2)}")
            if len(skeleton) >= MAX_SKELETON_ITEMS:
                break
    return skeleton
