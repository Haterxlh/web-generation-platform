# app/utils/doc/base.py —— 文档解析层的公共契约（异常 + 解析产物）
#
# 职责边界（docs/agent_refactor_plan.md 阶段 3）：
#   本子包只做**解析**（把文件变成结构化文本），**不含任何 LLM 调用**。
#   "理解"（摘要成 RequirementDigest）是 app/agents/source/doc_digest_agent.py 的事。
#
# 这样切分的好处：解析层可以整块离线单测（无需模型、无需数据库），
# 而模型只在"已经拿到干净文本"之后才被调用 —— 省 token，也让解析 bug 与提示词 bug 可分开定位。

from typing import Any, Literal

from pydantic import BaseModel, Field

# 解析器认得的源类型（与文件扩展名 / MIME 的映射见 app/utils/doc/__init__.py）
SourceType = Literal["pdf", "html", "markdown", "text"]


class DocParseError(ValueError):
    """文档解析失败。

    为什么单独定义一个异常类型，而不是直接抛 ValueError / UnicodeDecodeError：
    上传接口要把它翻译成**面向用户的明确原因**（写进 `generation_source.parse_error`），
    而这些原因有固定的几类（编码、扫描版、加密、不支持的类型、空文件……）。
    统一类型 + 统一措辞，才不会出现"有的地方报 500、有的地方静默跳过"。

    Attributes:
        reason: 面向用户的失败原因（会写进库、也会回给前端）。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ParsedDocument(BaseModel):
    """一份文档解析后的结构化结果（解析层的唯一产物）。

    字段都是"下游真的会用到"的，没有一个是装饰：

    - ``text``：正文文本。Markdown **保留原结构**（标题 / 表格 / 代码块），
      因为结构本身就是信息，预先压平等于替模型做决定；
    - ``native_blocks``：**文档天然的分块**（PDF 按页）。给 digest 的 map-reduce 用 ——
      按页切分比按字符硬切更不容易把一句话劈成两半，也省一次分块决策；
    - ``skeleton``：区块骨架（HTML 的结构标签 / Markdown 的标题大纲），
      让模型在只读摘要时也知道"这份文档讲了哪几块"；
    - ``design_tokens``：**只有 HTML 抽得出来**（配色 / 字体 / 字号阶梯 / 圆角 / 间距 / 布局）。
      它是"只有 HTML 能当风格源"这条硬约束的**事实依据** ——
      见 :meth:`can_be_style_source`；
    - ``warnings``：解析过程中的非致命问题（部分页无文本、样式被去重、文档超长……）。
      **刻意不做静默降级**：能拿到多少信息、丢了什么，都写在这里往上传。
    """

    source_type: SourceType = Field(description="源类型：pdf / html / markdown / text")
    text: str = Field(default="", description="正文文本（Markdown 保留原结构）")
    native_blocks: list[str] = Field(
        default_factory=list, description="文档天然分块（PDF 按页）；为空表示按正文自行分块"
    )
    skeleton: list[str] = Field(
        default_factory=list, description="区块骨架（HTML 结构标签 / Markdown 标题大纲）"
    )
    design_tokens: dict[str, Any] = Field(
        default_factory=dict, description="设计令牌（仅 HTML 有：配色/字体/字号/圆角/间距/布局）"
    )
    page_count: int | None = Field(default=None, description="PDF 页数；非 PDF 为 None")
    encoding: str | None = Field(default=None, description="实际使用的文本编码（仅文本类）")
    warnings: list[str] = Field(default_factory=list, description="非致命问题（不静默降级）")

    @property
    def char_count(self) -> int:
        """正文字符数（用于判断要不要走 map-reduce 分块）。"""
        return len(self.text)

    @property
    def is_empty(self) -> bool:
        """正文与设计令牌都为空 —— 这份文档什么信息都没给出来。"""
        return not self.text.strip() and not self.design_tokens

    def can_be_style_source(self) -> bool:
        """本类型的文档**有没有能力**当风格源。

        ⚠️ 这是"能力"判定，不是"模型判定"：``pypdf`` 只能抽出文字，
        抽不出配色 / 字体 / 圆角，所以 PDF 与纯文本**永远**当不了风格源。
        模型若把 ``.md`` 判成风格源，由 Python 侧用这个判据否决
        （判定权归模型、否决权归 Python —— 见 docs/agent_refactor_plan.md §3.8）。

        Returns:
            True 表示可以当风格源（当前只有 HTML）。
        """
        return self.source_type == "html" and bool(self.design_tokens)

    def render_for_model(self, max_chars: int | None = None) -> str:
        """把解析结果渲染成一段"给模型看"的文本（digest 阶段的输入）。

        渲染而不是直接丢 ``self.text``：骨架与设计令牌同样是有用信息，
        而它们不在正文里（HTML 的配色写在 ``<style>`` 里，早就被剥掉了）。

        Args:
            max_chars: 正文的字符上限；None 表示不截断。
                超限时**显式标注被截断**并给出原文长度 —— 绝不静默丢内容。

        Returns:
            形如 ``【文档类型】…\\n【正文】…`` 的多段文本。
        """
        parts: list[str] = [f"【文档类型】{self.source_type}"]

        if self.encoding:
            parts.append(f"【文本编码】{self.encoding}")
        if self.page_count is not None:
            parts.append(f"【页数】{self.page_count}")

        body = self.text
        if max_chars is not None and len(body) > max_chars:
            body = body[:max_chars]
            parts.append(
                f"【注意】正文过长已截断，此处只显示前 {max_chars} 字符，"
                f"原文共 {self.char_count} 字符"
            )
        parts.append("【正文】\n" + (body.strip() or "（无正文）"))

        if self.skeleton:
            parts.append("【区块骨架】\n" + "\n".join(f"- {item}" for item in self.skeleton))

        if self.design_tokens:
            parts.append("【设计令牌】\n" + self._render_tokens())

        if self.warnings:
            parts.append("【解析提示】\n" + "\n".join(f"- {item}" for item in self.warnings))

        return "\n\n".join(parts)

    def _render_tokens(self) -> str:
        """把设计令牌字典渲染成人可读的键值行。"""
        lines: list[str] = []
        for key, value in self.design_tokens.items():
            if isinstance(value, list):
                rendered = "、".join(str(item) for item in value)
            elif isinstance(value, dict):
                rendered = "、".join(f"{k}×{v}" for k, v in value.items())
            else:
                rendered = str(value)
            lines.append(f"- {key}：{rendered}")
        return "\n".join(lines)
