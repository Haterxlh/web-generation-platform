# app/agents/state.py —— Agent 编排层的结构化契约（各节点的输入 / 输出）
#
# 为什么单独一个模块：
#   这些模型被 router / chat / merge / plan 多个节点共用，也可能被 service 引用。
#   集中在一处，就能一眼看清"Agent 这条流水线上流动的数据长什么样"。
#
# 约定：本模块是**纯声明**，不含任何 LLM 调用与 IO —— 因此它可以被任何层安全 import。

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.agents.common import ModelUsage

# 需求槽位的字段名（就是 API 契约里的键名）
SLOT_NAMES: tuple[str, ...] = (
    "site_kind",
    "features",
    "audience",
    "style",
    "need_persistence",
)

# Python 侧硬兜底用的"必备槽位"：这两个都空，绝不放行生成
REQUIRED_SLOTS: tuple[str, ...] = ("site_kind", "features")


class RequirementSlots(BaseModel):
    """需求槽位（暂定 5 项，见 docs/agent_refactor_plan.md §0.3 决策 11）。

    三个刻意的类型选择：

    1. ``site_kind`` 用**自由文本**而不是枚举：固定枚举会逼模型把需求塞进错误的桶
       （"帮我做个抽奖转盘"该归哪类？）。提示词里给建议词表，但不锁死。
    2. ``need_persistence`` 用 ``bool | None`` **三态**：``False`` 不该同时表示
       "用户说不需要"和"用户根本没提" —— 否则"用户没提"会被静默当成"不需要"，
       需求凭空少一条且无人知晓。
    3. ``features`` 用**列表**：它要跨轮累积（第 1 轮"能添加待办"、第 3 轮"要设优先级"），
       列表天然可合并、去重、逐条对照。
    """

    site_kind: str | None = Field(
        default=None,
        description="站点类型，例如：单页展示 / 表单工具 / 数据看板 / 管理后台 / 内容站",
    )
    features: list[str] = Field(
        default_factory=list, description="核心功能，一条一项，可跨轮累积"
    )
    audience: str | None = Field(default=None, description="目标用户")
    style: str | None = Field(default=None, description="视觉风格与配色倾向")
    need_persistence: bool | None = Field(
        default=None, description="是否需要数据持久化；None 表示用户尚未提及"
    )

    def missing(self) -> list[str]:
        """列出仍为空的槽位名（保持 SLOT_NAMES 的顺序，便于稳定断言）。

        Returns:
            空槽位名列表。
        """
        miss: list[str] = []
        if not self.site_kind:
            miss.append("site_kind")
        if not self.features:
            miss.append("features")
        if not self.audience:
            miss.append("audience")
        if not self.style:
            miss.append("style")
        if self.need_persistence is None:
            miss.append("need_persistence")
        return miss

    def missing_required(self) -> list[str]:
        """只列出**必备**槽位里为空的那些（Python 侧硬兜底判据）。

        Returns:
            必备空槽位名列表。
        """
        return [name for name in self.missing() if name in REQUIRED_SLOTS]

    def merged_with(self, newer: "RequirementSlots") -> "RequirementSlots":
        """把新一轮抽取到的槽位合并进旧的（跨轮累积）。

        合并规则：
        - 标量字段：``newer`` 有值就覆盖，为 None 则保留旧值（**不要用新抽取的空值清掉旧信息**）；
        - ``features``：按顺序去重合并（新增的追加在后面）。

        Args:
            newer: 本轮新抽取的槽位。

        Returns:
            合并后的新对象（不修改原对象）。
        """
        merged_features: list[str] = list(self.features)
        for item in newer.features:
            if item and item not in merged_features:
                merged_features.append(item)

        return RequirementSlots(
            site_kind=newer.site_kind or self.site_kind,
            features=merged_features,
            audience=newer.audience or self.audience,
            style=newer.style or self.style,
            # 三态字段：只有 newer 明确给了 True/False 才覆盖，None 视为"本轮没提"
            need_persistence=(
                newer.need_persistence
                if newer.need_persistence is not None
                else self.need_persistence
            ),
        )


class RouterDecision(BaseModel):
    """intent_router 的结构化输出。

    ``reason`` 与 ``ask_hint`` 都是刻意保留的字段：

    - ``reason``：阶段 6 的实验记录要统计"路由正确率"。没有它，判错时只能看结果反推原因；
      有了它，一次调用同时给出"判定"和"判定的理由"，排查成本从"再烧一次 token 复现"
      降到"读一行日志"。
    - ``ask_hint``：router 与 chat-agent 之间的**交接棒** —— router 已经知道缺的是 ``style``，
      就不必让 chat-agent 再推一遍，它只负责把"缺 style"变成一句人话。
    """

    intent: Literal["chat", "generate"] = Field(
        description="用户想要对话（咨询/澄清），还是要产物"
    )
    readiness: Literal["ready", "needs_clarification"] = Field(
        description="需求是否已足够开始生成"
    )
    slots: RequirementSlots = Field(
        default_factory=RequirementSlots, description="从对话中抽取到的需求槽位"
    )
    missing_slots: list[str] = Field(
        default_factory=list, description="对本需求而言真正缺失的槽位名"
    )
    ask_hint: str = Field(default="", description="下一步该问什么（一句话，给 chat-agent）")
    reason: str = Field(default="", description="判定依据（一句话，用于排查与实验记录）")


class RequirementDraft(BaseModel):
    """会话级的需求草稿，存进 ``agent_session.draft_requirement``。

    为什么放会话行里而不是"读最后一条带 slots 的消息"：
    它是**会话状态**，每轮都要读写；放在会话行里，一次主键查询就能拿到，
    不必按 session 扫消息表再挑最新的一条。
    """

    slots: RequirementSlots = Field(default_factory=RequirementSlots, description="当前槽位")
    summary: str = Field(default="", description="一句话人可读的需求摘要")
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="最后更新时间（ISO 字符串：JSONB 里存字符串比存 datetime 更省心）",
    )


class RouterResult(BaseModel):
    """router 节点的返回值：判定 + 用量 + 降级标记。

    Attributes:
        decision: 结构化判定结果。
        degraded: 是否因为调用失败而走了降级路径（排查时一眼可见）。
        usage: 本次调用消耗的 token。
    """

    model_config = {"arbitrary_types_allowed": True}

    decision: RouterDecision
    degraded: bool = False
    usage: ModelUsage = Field(default_factory=ModelUsage)


class ChatResult(BaseModel):
    """chat-agent 节点的返回值。

    Attributes:
        reply: 给用户看的自然语言回复（**不是**结构化数据 —— 它唯一的读者是人）。
        degraded: 是否因为调用失败而返回了兜底话术。
        usage: 本次调用消耗的 token。
    """

    reply: str = ""
    degraded: bool = False
    usage: ModelUsage = Field(default_factory=ModelUsage)


def parse_draft(value: Mapping[str, Any] | None) -> tuple["RequirementDraft", str | None]:
    """把库里存的 ``agent_session.draft_requirement`` 还原成 :class:`RequirementDraft`。

    为什么放在这里而不是各 service 各写一份：这份草稿现在有**两个读者** ——
    对话路径（`AgentChatService`，每轮读写）与生成路径（`AgentGenerationService`，
    阶段 8 起把它交给 worker 的 ROUTING / need_rag / merge，让两处判定看到同一份需求）。
    解析口径一旦漂移，"用户确认过的需求"和"生成时看到的需求"就会悄悄不一致。

    Args:
        value: JSONB 列读回来的字典；None 或空表示还没有草稿。

    Returns:
        ``(草稿, 警告)``：没有草稿时返回空草稿且不告警；**数据不合法时返回空草稿 + 原因**
        （调用方决定是记日志还是进 warnings —— 但绝不抛异常：脏数据不该拖垮一轮对话或一次生成）。
    """
    if not value:
        return RequirementDraft(), None
    try:
        return RequirementDraft.model_validate(value), None
    except ValidationError as error:
        return RequirementDraft(), f"{type(error).__name__}: {error}"


def digest_one_line(value: "RequirementDigest | Mapping[str, Any] | None") -> str | None:
    """把结构化 digest 压成**一行**短摘要（附件在提示词里就显示这一行）。

    为什么放在这里而不是各 service 各写一份：附件摘要既要进提示词（对话里的 ``@doc1`` 展开），
    也要回给前端展示。两处渲染规则一旦漂移，用户看到的内容与模型看到的就不一致了 ——
    而"模型看到的"才是决定产出质量的那个。

    输入同时接受 ``RequirementDigest`` 与**数据库里取出的 dict**（JSONB 列读回来就是 dict）。

    Args:
        value: digest 对象 / 字典；None 或空表示尚未解析。

    Returns:
        一行摘要；没有任何内容时返回 None（渲染成"尚未解析"）。
    """
    if value is None:
        return None

    if isinstance(value, RequirementDigest):
        digest = value.model_dump()
    else:
        digest = dict(value)

    bits: list[str] = []
    content_points = digest.get("content_points") or []
    if content_points:
        bits.append("内容要点：" + "、".join(str(item) for item in content_points[:5]))
    style_spec = digest.get("style_spec") or {}
    if isinstance(style_spec, Mapping) and style_spec:
        flat = [
            f"{key}={value}" if not isinstance(value, list) else f"{key}={'/'.join(str(v) for v in value[:3])}"
            for key, value in list(style_spec.items())[:5]
            if value
        ]
        if flat:
            bits.append("风格：" + "，".join(flat))
    constraints = digest.get("constraints") or []
    if constraints:
        bits.append("约束：" + "、".join(str(item) for item in constraints[:5]))
    return "；".join(bits) or None


class StyleSpec(BaseModel):
    """从一份文档里提炼出的风格规范（阶段 3 起使用）。

    两类字段的来源**刻意不同**，这是本模型最重要的设计：

    - 结构化的取值（``colors`` / ``font_families`` / ``font_sizes`` / ``border_radius`` /
      ``spacing`` / ``layout``）来自**解析器的实测统计**（``ParsedDocument.design_tokens``），
      **不由模型填写**。理由：这些是事实（页面里确实写了 ``#0f172a``），
      让模型转述一遍只会引入幻觉 —— "看起来像 #1e293b" 这种偏差在风格复刻里是致命的。
    - ``notes`` 才是模型的地盘：把"深色底 + 大圆角 + 无衬线"这类**意图**说清楚。
    """

    colors: list[str] = Field(default_factory=list, description="配色（按出现频次排序）")
    font_families: list[str] = Field(default_factory=list, description="字体族")
    font_sizes: list[str] = Field(default_factory=list, description="字号阶梯")
    border_radius: list[str] = Field(default_factory=list, description="圆角")
    spacing: list[str] = Field(default_factory=list, description="间距")
    layout: dict[str, int] = Field(
        default_factory=dict, description="布局统计（display 取值次数 / 媒体查询数量）"
    )
    notes: str = Field(default="", description="模型对风格意图的文字描述")

    @classmethod
    def from_design_tokens(cls, tokens: Mapping[str, Any] | None, notes: str = "") -> "StyleSpec":
        """把解析器抽出的设计令牌映射成本模型（**只做搬运，不做猜测**）。

        Args:
            tokens: ``ParsedDocument.design_tokens``；可以为 None 或空。
            notes: 模型补充的风格描述。

        Returns:
            风格规范；未知的键被忽略（解析器将来加字段不会让这里报错）。
        """
        data = dict(tokens or {})
        layout = data.get("layout")
        return cls(
            colors=[str(item) for item in data.get("colors") or []],
            font_families=[str(item) for item in data.get("font_families") or []],
            font_sizes=[str(item) for item in data.get("font_sizes") or []],
            border_radius=[str(item) for item in data.get("border_radius") or []],
            spacing=[str(item) for item in data.get("spacing") or []],
            layout={str(k): int(v) for k, v in layout.items()} if isinstance(layout, Mapping) else {},
            notes=notes,
        )

    def is_empty(self) -> bool:
        """是否没有任何可用的风格信息（用于判"这份 digest 到底给没给风格"）。"""
        return not any(
            (self.colors, self.font_families, self.font_sizes,
             self.border_radius, self.spacing, self.layout, self.notes.strip())
        )


class RagChunk(BaseModel):
    """个人知识库里的一个检索片段（阶段 4 定义契约，二期才真有数据）。

    ⚠️ ``source_type`` 从第一天就必须区分 **L1 / L2**（硬约束 14）：

    - ``conversation``（L1）：用户**说过的话**（偏好、口径、"我们公司规定…"）；
    - ``document``（L2）：用户**上传的资料**（PDF / HTML / Markdown 的正文）。

    两者的证据强度不同：用户亲口说的可以直接当约束；从文档里检索出来的段落
    必须能被追溯到具体文件与位置。混在一个集合里，二期就无法按来源调整权重，
    也无法回答"这条要求的出处在哪"。
    """

    chunk_id: str = Field(default="", description="片段唯一标识（二期由向量表给出）")
    source_type: Literal["document", "conversation"] = Field(
        description="来源层级：document=L2 上传资料 / conversation=L1 用户说过的话"
    )
    source_ref: str = Field(default="", description="来源标识（文件名 / @doc1 / 会话轮次）")
    text: str = Field(description="片段正文（会进提示词，所以要短）")
    score: float | None = Field(default=None, description="相似度得分（二期才有意义）")


class RagResult(BaseModel):
    """一次"是否需要个人知识库、以及查到了什么"的结果（阶段 4 起使用）。

    **三态语义从第一天就是真的**（docs/agent_refactor_plan.md 阶段 4）：

    - ``skipped``：判定**不需要**私人资料（通用页面），**根本没有去查**；
    - ``miss``：需要，也去查了，但**没查到**（或检索能力尚未接入）；
    - ``hit``：需要且查到了片段。

    ``miss`` 与 ``skipped`` 绝不能合并成一个"空"：
    前者意味着"用户要的东西我们没找到"，必须作为**不确定项**传给下游与用户；
    后者意味着"这事本来就不需要"，静默是正确的。
    合并之后，下游就无法区分"不适用"与"失败"，而这正是需求跑偏的常见起点。
    """

    status: Literal["hit", "miss", "skipped"] = Field(description="检索三态")
    query: str = Field(default="", description="实际使用的检索词（排查用，也便于二期调优）")
    chunks: list[RagChunk] = Field(default_factory=list, description="命中的片段（hit 才有）")
    reason: str = Field(default="", description="为什么未命中 / 为什么不需要（面向用户）")
    degraded: bool = Field(default=False, description="判定或检索是否走了降级路径")
    warnings: list[str] = Field(default_factory=list, description="非致命问题（不静默降级）")
    usage: ModelUsage = Field(
        default_factory=ModelUsage, description="判定消耗的 token（检索本身不烧 token）"
    )

    @property
    def needs_personal_knowledge(self) -> bool:
        """本次需求是否需要用户的私人知识库（``skipped`` 之外都为真）。"""
        return self.status != "skipped"

    def as_prompt_text(self) -> str:
        """渲染成"给模型看"的检索结果。

        ``miss`` 与 ``skipped`` 都要**明说**，不能返回空串 ——
        空串会被模型理解为"用户资料里没有"，于是它开始编。
        """
        if self.status == "hit":
            lines = [
                f"- [{chunk.source_type}] {chunk.source_ref or '未标注来源'}："
                f"{' '.join(chunk.text.split())[:300]}"
                for chunk in self.chunks
            ]
            return "【个人知识库命中片段】\n" + "\n".join(lines)
        if self.status == "miss":
            return (
                "【个人知识库未命中】\n"
                f"检索词：{self.query or '（无）'}\n原因：{self.reason or '未找到相关资料'}\n"
                "⚠️ 用户资料里**没有**可用的依据：不要据此编造用户的事实（如公司名、产品名、数据）。"
            )
        return "【个人知识库】本次需求不需要私人资料，未进行检索。"


class RequirementSource(BaseModel):
    """一条需求的**来源证据**（用于追溯"这句话是从哪来的"）。

    阶段 3 的 merge 节点产出它，`FinalRequirement` 带着它一路传到 plan 与 web-agent。

    为什么必须显式记录：需求冲突时（用户说"极简"，文档给"深色大面积渐变"）
    最终采信了谁、依据是什么，只能靠这张清单回答。没有它，
    事后排查只能重新跑一遍模型 —— 那是最贵也最不可靠的方式。
    """

    kind: Literal["user", "chat", "doc", "rag"] = Field(
        description="来源类型：用户本轮原话 / 对话澄清摘要 / 上传文档 / 个人知识库"
    )
    ref: str = Field(description="来源标识（用户原话片段 / @doc1 / 文档名 / 知识库片段 id）")
    note: str = Field(default="", description="补充说明（如文档文件名、该来源的角色）")


class FinalRequirement(BaseModel):
    """需求装配的最终产物（阶段 3 的 merge 节点产出，阶段 5/6 消费）。

    它是"四来源冲突消解之后"的**唯一一份需求**：用户显式要求、对话澄清摘要、
    文档 digest（内容 + 风格）、个人知识库检索结果，都在这里收敛。

    为什么不把四个来源原样丢给 web-agent：那样它只能临场裁决，
    而那个裁决既不可观测、也无法回归测试（§3.5 的理由）。

    两个刻意保留的字段：

    - ``sources``：证据可追溯（谁说的、从哪来的）；
    - ``uncertainty``：**把不确定性显式传给下游**。RAG 未命中、文档没说清的地方
      绝不能沉默 —— 沉默会被下游当成"用户资料里没有"，然后编一个出来。
    """

    summary: str = Field(default="", description="一段人可读的需求陈述")
    slots: RequirementSlots = Field(default_factory=RequirementSlots, description="需求槽位")
    content_points: list[str] = Field(default_factory=list, description="来自文档的内容素材")
    style_spec: StyleSpec = Field(default_factory=StyleSpec, description="来自文档的风格规范")
    constraints: list[str] = Field(default_factory=list, description="硬约束")
    sources: list[RequirementSource] = Field(default_factory=list, description="来源证据")
    uncertainty: list[str] = Field(default_factory=list, description="不确定项")

    def as_prompt_text(self) -> str:
        """渲染成一段"给模型看"的最终需求（plan / web-agent 的输入）。

        Returns:
            多段文本；空的部分写"（无）"，避免下游误以为漏读。
        """

        def _lines(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "（无）"

        parts = [
            "【需求概述】\n" + (self.summary.strip() or "（无）"),
            "【槽位】\n" + self.slots.model_dump_json(),
        ]
        if self.content_points:
            parts.append("【内容素材】\n" + _lines(self.content_points))
        if self.constraints:
            parts.append("【硬约束】\n" + _lines(self.constraints))
        if not self.style_spec.is_empty():
            style = self.style_spec
            style_lines: list[str] = []
            if style.colors:
                style_lines.append("配色：" + "、".join(style.colors))
            if style.font_families:
                style_lines.append("字体：" + "、".join(style.font_families))
            if style.font_sizes:
                style_lines.append("字号：" + "、".join(style.font_sizes))
            if style.border_radius:
                style_lines.append("圆角：" + "、".join(style.border_radius))
            if style.spacing:
                style_lines.append("间距：" + "、".join(style.spacing))
            if style.layout:
                style_lines.append(
                    "布局：" + "、".join(f"{key}×{value}" for key, value in style.layout.items())
                )
            if style.notes.strip():
                style_lines.append("说明：" + style.notes.strip())
            parts.append("【风格规范】\n" + "\n".join(f"- {line}" for line in style_lines))
        if self.uncertainty:
            parts.append("【不确定项（需谨慎，不要凭空编造）】\n" + _lines(self.uncertainty))
        return "\n\n".join(parts)


# 交付文件的角色：用枚举而不是自由文本 —— 门禁靠它比对"该交的都交了没"
FILE_ROLES: tuple[str, ...] = ("markup", "style", "script", "data", "asset")


class PlannedFile(BaseModel):
    """计划交付的一个文件。

    ⚠️ ``name`` 会先过 `file_writer.safe_name()` 白名单再入库/进门禁：
    早先实测过模型把 ``style.css`` 写成 ``styles.css``，也见过它给出带路径的名字 ——
    文件名不能由模型说了算（阶段 5 起改为"plan 定集合 + Python 校验"）。

    ``role`` 用枚举（``markup`` / ``style`` / ``script`` / ``data`` / ``asset``）：
    自由文本会让门禁与后续统计无法比对 —— "html" / "HTML" / "网页" 到底是同一类吗？
    """

    name: str = Field(description="文件名（单层、无路径成分，如 index.html）")
    role: Literal["markup", "style", "script", "data", "asset"] = Field(
        default="markup", description="文件角色"
    )
    depends_on: list[str] = Field(
        default_factory=list, description="依赖的其他文件名（悬空依赖会被 Python 剔除）"
    )
    summary: str = Field(default="", description="这个文件负责什么（一句话，不给实现细节）")


class FilePlan(BaseModel):
    """规划产物：**事先声明的交付清单**（阶段 5 起由 plan-agent 产出）。

    为什么需要它（而不是让 web-agent 自己决定交什么）：
    完成门禁需要一份**事先声明**的清单才能判定"交付完整"。若没有它，
    完成判定就只能退回"模型说完了就算完"—— 那正是本次改造要根治的毛病
    （实测出现过"只写 1 个文件就宣布完成"）。

    ``difficulty`` 不是标签而是**开关**：它决定 web-agent 的步数上限与 token 预算
    （见 `app/agents/common.py` 的 `StageBudget`），因此 Python 会复核它（只能上调，不能低报）。
    """

    difficulty: Literal["easy", "medium", "hard"] = Field(
        default="medium", description="难度（决定步数上限与 token 预算）"
    )
    entry_file: str = Field(default="index.html", description="入口文件（预览打开的就是它）")
    files: list[PlannedFile] = Field(default_factory=list, description="要交付的文件清单")
    tech_constraints: list[str] = Field(
        default_factory=list, description="技术约束（如「不引入构建工具」「只用原生 JS」）"
    )
    assets: list[str] = Field(
        default_factory=list, description="外部资源（CDN 库、字体、图片占位等）"
    )

    def names(self) -> list[str]:
        """清单里的文件名（顺序即计划顺序，门禁按它比对）。"""
        return [item.name for item in self.files]

    def as_prompt_text(self) -> str:
        """渲染成"给模型看"的文件清单（阶段 6 的 web-agent 按它逐个交付）。"""

        def _lines(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "（无）"

        files = (
            "\n".join(
                f"- {item.name}（{item.role}）：{item.summary or '（未说明）'}"
                + (f"｜依赖：{'、'.join(item.depends_on)}" if item.depends_on else "")
                for item in self.files
            )
            or "（无）"
        )
        parts = [
            f"【难度】{self.difficulty}",
            f"【入口文件】{self.entry_file}",
            "【交付清单（缺一不可）】\n" + files,
        ]
        if self.tech_constraints:
            parts.append("【技术约束】\n" + _lines(self.tech_constraints))
        if self.assets:
            parts.append("【外部资源】\n" + _lines(self.assets))
        return "\n\n".join(parts)


class RequirementDigest(BaseModel):
    """一份文档的"理解结果"（digest-agent 的结构化输出，阶段 3 起使用）。

    它是**文件维度**的产物（一份文件一份 digest），与"对话维度"的需求草稿
    （``RequirementDraft``）分开存放：文件不变，digest 可以算一次缓存进
    ``generation_source.digest`` 跨轮复用；而对话每轮都在变，必须每轮重算。
    这就是把 digest 与 merge 拆成两个节点的原因（见 docs/agent_refactor_plan.md §3.2.1）。

    ⚠️ ``constraints`` 与 ``content_points`` 的区分是本模型的关键（阶段 3 增补的约定）：
    ``.md`` / ``.txt`` 常常本身就是**需求说明书**（"要有登录""必须响应式"）。
    若把这类"对网页的要求"塞进 ``content_points``，下游 web-agent 会把要求当成页面文案
    印在页面上。所以：**要求 → constraints，素材 → content_points**。

    Attributes:
        summary: 一句话说明这份文档是什么（给 merge 与排查用）。
        role: 该文档在本次生成中的角色（已由 Python 侧按解析器能力否决过）。
        content_points: 可直接用作页面内容 / 文案的素材要点。
        style_spec: 风格规范（只有 HTML 能给出结构化取值）。
        constraints: 文档里提出的硬要求（必须遵守）。
        open_questions: 文档没说清、需要向用户确认的点。
    """

    summary: str = Field(default="", description="一句话说明这份文档是什么")
    role: Literal["content", "style", "both"] = Field(
        default="content", description="角色：内容源 / 风格源 / 两者都是"
    )
    content_points: list[str] = Field(default_factory=list, description="可用作页面内容的素材要点")
    style_spec: StyleSpec = Field(default_factory=StyleSpec, description="风格规范")
    constraints: list[str] = Field(default_factory=list, description="文档提出的硬要求")
    open_questions: list[str] = Field(default_factory=list, description="需要向用户确认的点")

    def as_prompt_text(self) -> str:
        """渲染成一段"给模型看"的文本（merge 阶段把它拼进需求装配的上下文）。

        Returns:
            多段文本；空 digest 也会明确写出"（无）"，避免下游以为漏读了。
        """

        def _lines(items: list[str]) -> str:
            return "\n".join(f"- {item}" for item in items) if items else "（无）"

        parts = [
            f"【角色】{self.role}",
            f"【概述】{self.summary or '（无）'}",
            "【内容素材】\n" + _lines(self.content_points),
            "【硬要求】\n" + _lines(self.constraints),
        ]
        if not self.style_spec.is_empty():
            style = self.style_spec
            style_lines: list[str] = []
            if style.colors:
                style_lines.append("配色：" + "、".join(style.colors))
            if style.font_families:
                style_lines.append("字体：" + "、".join(style.font_families))
            if style.font_sizes:
                style_lines.append("字号：" + "、".join(style.font_sizes))
            if style.border_radius:
                style_lines.append("圆角：" + "、".join(style.border_radius))
            if style.spacing:
                style_lines.append("间距：" + "、".join(style.spacing))
            if style.layout:
                style_lines.append(
                    "布局：" + "、".join(f"{key}×{value}" for key, value in style.layout.items())
                )
            if style.notes.strip():
                style_lines.append("说明：" + style.notes.strip())
            parts.append("【风格规范】\n" + "\n".join(f"- {line}" for line in style_lines))
        if self.open_questions:
            parts.append("【待确认】\n" + _lines(self.open_questions))
        return "\n\n".join(parts)
