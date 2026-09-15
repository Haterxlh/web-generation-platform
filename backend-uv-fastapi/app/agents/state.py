# app/agents/state.py —— Agent 编排层的结构化契约（各节点的输入 / 输出）
#
# 为什么单独一个模块：
#   这些模型被 router / chat / merge / plan 多个节点共用，也可能被 service 引用。
#   集中在一处，就能一眼看清"Agent 这条流水线上流动的数据长什么样"。
#
# 约定：本模块是**纯声明**，不含任何 LLM 调用与 IO —— 因此它可以被任何层安全 import。

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

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
