# app/agents/source/doc_digest_agent.py —— 文档理解节点（"digest-agent"）
#
# 职责（docs/agent_refactor_plan.md 阶段 3）：**文件** → ``RequirementDigest``。
#
# 三条设计要点，每条都对应一个真实风险：
#
# 1. **只吃文件，不吃对话**。会话是会变的、文件是不变的 —— 所以 digest 算一次就能
#    缓存进 ``generation_source.digest`` 跨轮复用，而"对话摘要 + 冲突消解"是另一个节点
#    （阶段 6 入图的 merge）。合成一个节点，就会把"本可不重算"的活每轮重做一遍。
#
# 2. **分块策略按类型区分**：``.txt`` / ``.md`` 一般很小，一次调用就够；
#    只有 PDF 与超长 HTML 才走 map-reduce（分块摘要 → 归并）。少一次归并调用 = 少一份钱与延迟。
#
# 3. **设计令牌由 Python 填，不由模型填**。配色/字体/圆角是**事实**（页面里确实写了 ``#0f172a``），
#    模型转述只会引入幻觉（"看起来像 #1e293b"）。模型只写 ``style_spec.notes`` 这类**意图**描述。
#    同理，角色判定照旧"模型提议、Python 否决"（见 role_policy.py）。

import logging
import re
from collections.abc import Iterable, Sequence

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from app.agents.common import ModelUsage
from app.agents.source.role_policy import ROLE_CONTENT, resolve_role
from app.agents.state import RequirementDigest, StyleSpec
from app.core.llm_client import llm_structured_client
from app.utils.doc.base import ParsedDocument
from app.utils.weg_gen.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

PROMPT_NAME = "doc_digest_system"
CHUNK_PROMPT_NAME = "doc_chunk_digest_system"

# 每块的正文字符预算。取 4000 的理由：中文大致 1 字符 ≈ 1 token，
# 4000 字符的块 + 提示词 + 输出，单次调用可控在万级 token 以内，失败重来的代价也小。
CHUNK_CHAR_BUDGET = 4000

# 分块数上限：一份 100 万字的文档按预算能切出几百块，逐块调用既慢又贵。
# 超限时的处理见 `split_blocks`：保留**开头若干段 + 最后一段**，并显式记警告（不静默丢内容）。
MAX_CHUNKS = 12

# 降级时正文片段保留多少字符
FALLBACK_TEXT_CHARS = 300

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")


class ChunkDigest(BaseModel):
    """单个分块的摘要结果（map 阶段的输出契约）。

    刻意**不含** ``role`` 与 ``summary``：角色是整份文档的属性（分块看一段判不出来），
    ``summary`` 是归并的产物。让它俩出现在 map 契约里，只会诱使模型用一段的结论代表全文。
    """

    content_points: list[str] = Field(default_factory=list, description="本段可直接用作页面内容的素材")
    constraints: list[str] = Field(default_factory=list, description="本段对网页提出的要求")
    open_questions: list[str] = Field(default_factory=list, description="本段没说清、影响生成的点")
    style_notes: str = Field(default="", description="本段对视觉风格的文字描述；没有则留空")


class DigestResult(BaseModel):
    """digest 节点的返回值（产物 + 用量 + 降级与警告）。

    Attributes:
        digest: 结构化理解结果（``role`` 已由 Python 否决过）。
        degraded: 是否走了降级路径（调用失败 / 结构化解析失败 / 所有分块失败）。
        role_overridden: 模型给的角色是否被 Python 否决（原因在 ``warnings`` 里）。
        chunk_count: 实际参与理解的分块数（1 表示没有走 map-reduce）。
        usage: 本次理解累计消耗的 token（**失败的分块也记账**）。
        warnings: 非致命问题（分块跳过、文档过长、角色被否决……）。
    """

    model_config = {"arbitrary_types_allowed": True}

    digest: RequirementDigest
    degraded: bool = False
    role_overridden: bool = False
    chunk_count: int = 1
    usage: ModelUsage = Field(default_factory=ModelUsage)
    warnings: list[str] = Field(default_factory=list)


def build_digest_chain(model: Runnable | None = None) -> Runnable:
    """构造"整篇 → RequirementDigest"的链（结构化输出 + 保留原始 AIMessage）。

    Args:
        model: 可注入的模型（测试用；默认用全局 `llm_structured_client`）。
            结构化输出依赖强制 tool_choice，与思考模式互斥（设计约定 §7.3）。

    Returns:
        输入 `list[BaseMessage]` → 输出 `{"raw": AIMessage, "parsed": RequirementDigest|None}`。
    """
    return (model or llm_structured_client).with_structured_output(
        RequirementDigest, include_raw=True
    )


def build_chunk_chain(model: Runnable | None = None) -> Runnable:
    """构造"单个分块 → ChunkDigest"的链（map 阶段）。"""
    return (model or llm_structured_client).with_structured_output(
        ChunkDigest, include_raw=True
    )


# 模块级只建一次（链本身无状态，可反复 invoke）
_DIGEST = build_digest_chain()
_CHUNK = build_chunk_chain()


# ==================== 分块（纯函数，可离线单测） ====================


def split_blocks(
    parsed: ParsedDocument,
    budget: int = CHUNK_CHAR_BUDGET,
    max_chunks: int = MAX_CHUNKS,
) -> tuple[list[str], list[str]]:
    """把解析结果切成若干块。

    优先用**文档天然的分块**（PDF 的每页），因为它们不会把一句话劈成两半；
    没有天然分块时才按空行分段，再按需合并到预算。

    Args:
        parsed: 解析结果。
        budget: 每块的字符预算。
        max_chunks: 分块数上限（超出时的处理见 Returns 对应的警告）。

    Returns:
        (分块列表, 警告列表)。正文为空时返回空列表。
    """
    warnings: list[str] = []
    units = _units(parsed, budget)

    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 2 > budget:
            chunks.append(current)
            current = unit
        else:
            current = f"{current}\n\n{unit}" if current else unit
    if current:
        chunks.append(current)

    if not chunks:
        return [], warnings

    if len(chunks) > max_chunks:
        # 保留开头（通常是背景与总述）与结尾（结论、约束常写在最后），跳过中间
        head = chunks[: max_chunks - 1]
        tail = chunks[-1:]
        skipped = len(chunks) - len(head) - len(tail)
        warnings.append(
            f"文档过长（共 {len(chunks)} 段），已只理解前 {len(head)} 段与最后 1 段，"
            f"中间跳过 {skipped} 段；如需完整理解请拆分文件后分别上传"
        )
        chunks = head + tail

    return chunks, warnings


def _units(parsed: ParsedDocument, budget: int = CHUNK_CHAR_BUDGET) -> list[str]:
    """把文档拆成"不可再分的语义单位"（页 / 段落 / 超长行切片）。

    Args:
        parsed: 解析结果。
        budget: 单个单位的字符上限；超过它就硬切。

    Returns:
        单位列表（已去空、已按预算硬切超长单位）。
    """
    raw_units = [item.strip() for item in parsed.native_blocks if item.strip()]
    if not raw_units:
        text = parsed.text
        paragraphs = [item.strip() for item in _PARAGRAPH_SPLIT_RE.split(text) if item.strip()]
        # 有些文档整篇没有空行（PDF 抽出来就是一长串），退化成按行分段
        raw_units = paragraphs if len(paragraphs) > 1 else [
            line.strip() for line in text.splitlines() if line.strip()
        ]

    units: list[str] = []
    for unit in raw_units:
        if len(unit) <= budget:
            units.append(unit)
            continue
        # 单个单位就超预算（例如 PDF 整页只有一行、或没有换行的机器生成文档）：
        # 只能硬切。这是"最后手段"，正常情况下走不到这里。
        units.extend(unit[index : index + budget] for index in range(0, len(unit), budget))
    return units


# ==================== 节点主入口 ====================


def digest(
    parsed: ParsedDocument,
    *,
    filename: str | None = None,
    chain: Runnable | None = None,
    chunk_chain: Runnable | None = None,
) -> DigestResult:
    """把一份解析结果理解成 ``RequirementDigest``。

    Args:
        parsed: 解析结果（来自 `app/utils/doc/`，**无 LLM** 的那一层）。
        filename: 原始文件名（只用于提示词里的自我介绍与警告文案）。
        chain: 可注入的"整篇"链（测试用）。
        chunk_chain: 可注入的"分块"链（测试用）。

    Returns:
        理解结果（含降级标记、角色否决标记、分块数、用量与警告）。
    """
    label = filename or "该文档"

    if parsed.is_empty:
        # 没有输入就不调模型：调了只能得到编造的内容，还白花钱
        return DigestResult(
            digest=fallback_digest(parsed),
            degraded=True,
            chunk_count=0,
            warnings=["文档没有任何可提取的内容，未调用模型"],
        )

    chunks, warnings = split_blocks(parsed)

    if len(chunks) <= 1:
        return _digest_whole(parsed, chunks[0] if chunks else parsed.text, label, chain, warnings)
    return _digest_chunked(parsed, chunks, label, chain, chunk_chain, warnings)


def _digest_whole(
    parsed: ParsedDocument,
    content: str,
    label: str,
    chain: Runnable | None,
    warnings: list[str],
) -> DigestResult:
    """短文档（一个块就装得下）：一次调用直接得到整份 digest。"""
    payload = [
        SystemMessage(content=load_prompt(PROMPT_NAME)),
        HumanMessage(content=_build_whole_message(parsed, content, label)),
    ]
    result, usage, failure = _invoke(chain or _DIGEST, payload)

    if failure is not None or (result or {}).get("parsed") is None:
        reason = failure or "模型未按结构化契约返回"
        logger.warning("doc_digest 整篇理解失败，降级为正文片段：%s", reason)
        warnings.append(f"文档理解失败，已降级为仅保留正文片段（{reason}）")
        return DigestResult(
            digest=fallback_digest(parsed),
            degraded=True,
            chunk_count=1,
            usage=usage,
            warnings=warnings,
        )

    digest_obj: RequirementDigest = result["parsed"]  # type: ignore[index]
    return _finalize(digest_obj, parsed, usage=usage, chunk_count=1, warnings=warnings)


def _digest_chunked(
    parsed: ParsedDocument,
    chunks: Sequence[str],
    label: str,
    chain: Runnable | None,
    chunk_chain: Runnable | None,
    warnings: list[str],
) -> DigestResult:
    """长文档：先逐块摘要（map），再归并成一份（reduce）。"""
    partials: list[ChunkDigest] = []
    usage = ModelUsage()
    failed = 0

    for index, chunk in enumerate(chunks, start=1):
        payload = [
            SystemMessage(content=load_prompt(CHUNK_PROMPT_NAME)),
            HumanMessage(content=_build_chunk_message(parsed, chunk, index, len(chunks), label)),
        ]
        result, chunk_usage, failure = _invoke(chunk_chain or _CHUNK, payload)
        usage = usage + chunk_usage

        partial = (result or {}).get("parsed")
        if failure is not None or partial is None:
            failed += 1
            warnings.append(
                f"第 {index}/{len(chunks)} 段理解失败，已跳过（{failure or '模型未按结构化契约返回'}）"
            )
            continue
        partials.append(partial)

    if not partials:
        warnings.append("所有分块都理解失败，已降级为仅保留正文片段")
        return DigestResult(
            digest=fallback_digest(parsed),
            degraded=True,
            chunk_count=len(chunks),
            usage=usage,
            warnings=warnings,
        )

    # reduce：把各段结果归并成一份（这一步才决定 role 与 summary）
    payload = [
        SystemMessage(content=load_prompt(PROMPT_NAME)),
        HumanMessage(content=_build_merge_message(parsed, partials, label, len(chunks))),
    ]
    result, reduce_usage, failure = _invoke(chain or _DIGEST, payload)
    usage = usage + reduce_usage

    if failure is not None or (result or {}).get("parsed") is None:
        reason = failure or "模型未按结构化契约返回"
        warnings.append(f"分块归并失败，已退化为按段结果直接合并（{reason}）")
        merged = _merge_partials(parsed, partials)
        return _finalize(
            merged,
            parsed,
            usage=usage,
            chunk_count=len(chunks),
            warnings=warnings,
            degraded=True,
        )

    # 只要有分块失败，整体就算降级：信息确实少了一块，不能让调用方以为是完整的
    return _finalize(
        result["parsed"],  # type: ignore[index]
        parsed,
        usage=usage,
        chunk_count=len(chunks),
        warnings=warnings,
        degraded=failed > 0,
    )


def _invoke(chain: Runnable, payload: list) -> tuple[dict | None, ModelUsage, str | None]:
    """调用一次链，并把异常翻译成 (结果, 用量, 失败原因)。

    ⚠️ 失败也要返回用量（如果异常前拿到过 AIMessage 就拿不到，这里按 0 计）：
    与生成模块同一个口径 —— **失败的任务同样烧了 token**。

    Args:
        chain: 结构化输出链（include_raw=True）。
        payload: 消息列表。

    Returns:
        ({"raw": …, "parsed": …} 或 None, 用量, 失败原因或 None)。
    """
    try:
        result = chain.invoke(payload)
    except Exception as error:  # noqa: BLE001 —— 单个阶段失败不能拖垮整份文档理解
        return None, ModelUsage(), f"{type(error).__name__}: {error}"[:200]

    raw = (result or {}).get("raw")
    usage = ModelUsage.from_message(raw) if raw is not None else ModelUsage()
    return result, usage, None


def _finalize(
    digest_obj: RequirementDigest,
    parsed: ParsedDocument,
    *,
    usage: ModelUsage,
    chunk_count: int,
    warnings: list[str],
    degraded: bool = False,
) -> DigestResult:
    """统一收尾：写入设计令牌事实 + 裁决角色。

    Args:
        digest_obj: 模型给出的 digest。
        parsed: 解析结果（令牌事实与角色能力判据的唯一来源）。
        usage: 累计用量。
        chunk_count: 参与理解的分块数。
        warnings: 已有警告（会被就地追加角色否决原因）。
        degraded: 是否降级。

    Returns:
        收尾后的结果。
    """
    decision = resolve_role(digest_obj.role, parsed)
    if decision.overridden and decision.reason:
        warnings.append(decision.reason)

    finalized = digest_obj.model_copy(
        update={
            "role": decision.role,
            # 结构化取值一律来自解析器：模型填的在这里被**覆盖**掉（不是合并），
            # 因为一个幻觉出来的色值一旦混进风格规范，就会一路传到 web-agent 的提示词里
            "style_spec": StyleSpec.from_design_tokens(
                parsed.design_tokens, notes=digest_obj.style_spec.notes
            ),
        }
    )
    return DigestResult(
        digest=finalized,
        degraded=degraded,
        role_overridden=decision.overridden,
        chunk_count=chunk_count,
        usage=usage,
        warnings=warnings,
    )


def fallback_digest(parsed: ParsedDocument, role: str = ROLE_CONTENT) -> RequirementDigest:
    """降级 digest：不依赖模型，只把正文片段与设计令牌搬进契约。

    设计意图（docs/agent_refactor_plan.md §3.8.6）：文档理解失败**不能阻塞**主流程，
    但也不能假装成功 —— 所以留下一段正文片段供下游使用，并把不确定性**显式写进
    ``open_questions``**（"这份文档没能被完整理解"这件事必须传到用户那里）。

    Args:
        parsed: 解析结果。
        role: 角色（默认为安全侧的内容源）。

    Returns:
        降级后的 digest。
    """
    snippet = " ".join(parsed.text.split())[:FALLBACK_TEXT_CHARS]
    return RequirementDigest(
        summary="（降级）未完成模型理解，仅保留正文片段与设计令牌",
        role=role,
        content_points=[snippet] if snippet else [],
        constraints=[],
        open_questions=["该文档未能被完整理解；如需严格按其内容生成，请确认其中的关键要点"],
        style_spec=StyleSpec.from_design_tokens(parsed.design_tokens),
    )


def _merge_partials(parsed: ParsedDocument, partials: Sequence[ChunkDigest]) -> RequirementDigest:
    """归并失败时的 Python 兜底：把各段结果直接合并（不再调模型）。

    Args:
        parsed: 解析结果。
        partials: 各段的理解结果。

    Returns:
        合并后的 digest。
    """
    # 各段常常重复同一句风格描述（"深色底"），去重后再拼 —— 重复三次的同一句话没有信息量
    notes = "；".join(
        _dedupe(item.style_notes.strip() for item in partials if item.style_notes.strip())
    )
    return RequirementDigest(
        summary=f"（降级）由 {len(partials)} 段理解结果直接合并",
        role=ROLE_CONTENT,
        content_points=_dedupe(item for part in partials for item in part.content_points)[:20],
        constraints=_dedupe(item for part in partials for item in part.constraints)[:20],
        open_questions=_dedupe(item for part in partials for item in part.open_questions)[:10],
        style_spec=StyleSpec.from_design_tokens(parsed.design_tokens, notes=notes),
    )


def _dedupe(items: Iterable[str]) -> list[str]:
    """按顺序去重（保序：靠前的通常更重要）。

    Args:
        items: 字符串序列（可能含重复与空白）。

    Returns:
        去重后的字符串列表。
    """
    seen: dict[str, None] = {}
    for item in items:
        text = " ".join(str(item).split())
        if text:
            seen.setdefault(text, None)
    return list(seen)


# ==================== 提示词装配（纯字符串拼接） ====================


def _document_header(parsed: ParsedDocument, label: str) -> str:
    """文档元信息段（让模型知道自己在读什么）。"""
    bits = [f"文件名：{label}", f"类型：{parsed.source_type}"]
    if parsed.page_count:
        bits.append(f"页数：{parsed.page_count}")
    bits.append("含设计令牌：" + ("是" if parsed.design_tokens else "否"))
    return "## 文档信息\n" + "\n".join(bits)


def _build_whole_message(parsed: ParsedDocument, content: str, label: str) -> str:
    """整篇理解的用户消息。"""
    return "\n\n".join(
        [
            "## 任务\n把下面这份文档理解成结构化需求摘要。",
            _document_header(parsed, label),
            "## 文档内容\n" + content,
        ]
    )


def _build_chunk_message(
    parsed: ParsedDocument, chunk: str, index: int, total: int, label: str
) -> str:
    """分块理解的用户消息。"""
    return "\n\n".join(
        [
            f"## 任务\n这是整份文档的第 {index}/{total} 段，请**只**总结这一段。",
            _document_header(parsed, label),
            f"## 本段内容\n{chunk}",
        ]
    )


def _build_merge_message(
    parsed: ParsedDocument, partials: Sequence[ChunkDigest], label: str, total: int
) -> str:
    """归并阶段的用户消息。"""
    blocks: list[str] = []
    for index, part in enumerate(partials, start=1):
        blocks.append(
            "\n".join(
                [
                    f"### 第 {index} 段",
                    "- 内容素材：" + ("；".join(part.content_points) or "（无）"),
                    "- 硬要求：" + ("；".join(part.constraints) or "（无）"),
                    "- 待确认：" + ("；".join(part.open_questions) or "（无）"),
                    "- 风格描述：" + (part.style_notes.strip() or "（无）"),
                ]
            )
        )
    skipped = total - len(partials)
    note = f"（注意：共有 {total} 段，其中 {skipped} 段理解失败、未包含在下面）" if skipped else ""
    return "\n\n".join(
        [
            "## 任务\n把下面各段的理解结果归并成**一份**需求摘要：去重、合并同类项，"
            "并给出整份文档的角色与一句话概述。" + note,
            _document_header(parsed, label),
            "## 分块结果\n" + "\n\n".join(blocks),
        ]
    )
