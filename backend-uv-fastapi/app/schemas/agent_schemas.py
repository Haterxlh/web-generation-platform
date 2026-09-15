# app/schemas/agent_schemas.py —— Agent 对话模块的请求 / 响应模型
# 约定：请求体严格校验；响应体只暴露白名单字段

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AttachmentRef(BaseModel):
    """对话中引用的一个附件。

    阶段 2 只是把引用透传（上传接口在阶段 3）；字段先定下来，
    这样阶段 3 只需实现"按 source_uuid 查出来"，接口契约不用改。
    """

    source_uuid: str = Field(description="附件唯一标识（generation_source.source_uuid）")
    role: Literal["content", "style", "both"] = Field(
        default="content", description="附件角色：内容源 / 风格源 / 两者都是"
    )


class AgentChatRequest(BaseModel):
    """发起一轮对话的请求体。"""

    session_uuid: str | None = Field(
        default=None, description="会话标识；不传表示新建一个会话"
    )
    message: str = Field(min_length=1, max_length=4000, description="用户这一轮说的话")
    attachments: list[AttachmentRef] = Field(
        default_factory=list, description="本轮引用的附件"
    )


class RequirementSlotsOut(BaseModel):
    """需求槽位（响应白名单，与 agents/state.py 的 RequirementSlots 对齐）。"""

    site_kind: str | None = Field(default=None, description="站点类型")
    features: list[str] = Field(default_factory=list, description="核心功能列表")
    audience: str | None = Field(default=None, description="目标用户")
    style: str | None = Field(default=None, description="视觉风格与配色倾向")
    need_persistence: bool | None = Field(
        default=None, description="是否需要数据持久化；null 表示用户尚未提及"
    )


class AgentUsageOut(BaseModel):
    """本轮消耗的 token 用量。"""

    input_tokens: int = Field(default=0, description="输入 token 数")
    output_tokens: int = Field(default=0, description="输出 token 数（含思考）")
    reasoning_tokens: int = Field(default=0, description="其中思考 token 数")


class AgentChatResponse(BaseModel):
    """一轮对话的结果。"""

    session_uuid: str = Field(description="会话标识（新建时由后端生成）")
    message_uuid: str = Field(description="本轮助手消息的唯一标识")
    reply: str = Field(description="助手回复（直接展示给用户）")

    intent: Literal["chat", "generate"] = Field(description="识别出的意图")
    readiness: Literal["ready", "needs_clarification"] = Field(description="需求完备度")
    slots: RequirementSlotsOut = Field(description="当前累计的需求槽位")
    missing_slots: list[str] = Field(default_factory=list, description="还缺的槽位名")
    ready_to_generate: bool = Field(description="是否已可触发生成（前端据此显示按钮）")

    degraded: bool = Field(default=False, description="本轮是否走了降级路径（排查用）")
    usage: AgentUsageOut = Field(default_factory=AgentUsageOut, description="本轮 token 用量")


class AgentMessageOut(BaseModel):
    """会话里的一条消息（历史回放用）。"""

    model_config = ConfigDict(from_attributes=True)

    message_uuid: str = Field(description="消息唯一标识")
    role: str = Field(description="角色：user / assistant / system / tool")
    content: str = Field(description="消息正文（含 @docN 别名原文）")
    intent: str | None = Field(default=None, description="该轮的意图判定结果")
    create_time: datetime | None = Field(default=None, description="创建时间")


class AgentSessionDetailResponse(BaseModel):
    """会话详情（含消息列表，用于历史回放与前端渲染）。"""

    session_uuid: str = Field(description="会话标识")
    title: str | None = Field(default=None, description="会话标题")
    status: str = Field(description="会话状态：active / archived")
    slots: RequirementSlotsOut = Field(description="会话级需求草稿的槽位")
    summary: str = Field(default="", description="会话级需求草稿的一句话摘要")
    messages: list[AgentMessageOut] = Field(default_factory=list, description="消息列表（旧→新）")


class StyleSpecOut(BaseModel):
    """风格规范（响应白名单，与 agents/state.py 的 StyleSpec 对齐）。

    ⚠️ 结构化取值来自**解析器的实测统计**，不是模型转述 —— 它们可以直接当作
    "这个页面用什么配色/字体"的事实依据。
    """

    colors: list[str] = Field(default_factory=list, description="配色（按出现频次排序）")
    font_families: list[str] = Field(default_factory=list, description="字体族")
    font_sizes: list[str] = Field(default_factory=list, description="字号阶梯")
    border_radius: list[str] = Field(default_factory=list, description="圆角")
    spacing: list[str] = Field(default_factory=list, description="间距")
    layout: dict[str, int] = Field(default_factory=dict, description="布局统计")
    notes: str = Field(default="", description="模型对风格意图的文字描述")


class RequirementDigestOut(BaseModel):
    """文档理解结果（响应白名单，与 agents/state.py 的 RequirementDigest 对齐）。"""

    summary: str = Field(default="", description="一句话说明这份文档是什么")
    role: Literal["content", "style", "both"] = Field(description="角色：内容源/风格源/两者都是")
    content_points: list[str] = Field(default_factory=list, description="可用作页面内容的素材")
    style_spec: StyleSpecOut = Field(default_factory=StyleSpecOut, description="风格规范")
    constraints: list[str] = Field(default_factory=list, description="文档提出的硬要求")
    open_questions: list[str] = Field(default_factory=list, description="需要向用户确认的点")


class SourceUploadResponse(BaseModel):
    """附件上传结果。

    ``parse_status=failed`` **不是** HTTP 错误：上传本身成功了（文件已落盘、别名已分配），
    只是解析/理解失败。前端应展示 ``parse_error`` 并允许用户继续对话 ——
    核心链路不能被一个坏文件拖垮（见 docs/agent_refactor_plan.md §3.8.6）。
    """

    source_uuid: str = Field(description="附件唯一标识（对话里引用它）")
    alias: str = Field(description="会话内别名（形如 @doc1），前端渲染成 chip")
    display_name: str | None = Field(default=None, description="原始文件名（仅用于展示）")
    size_bytes: int = Field(description="文件大小（字节）")
    role: Literal["content", "style", "both"] = Field(description="最终生效的角色")
    parse_status: Literal["success", "failed"] = Field(description="解析状态")
    parse_error: str | None = Field(default=None, description="解析失败原因（面向用户）")
    digest: RequirementDigestOut | None = Field(default=None, description="文档理解结果")
    degraded: bool = Field(default=False, description="是否走了降级路径（解析或理解失败）")
    warnings: list[str] = Field(default_factory=list, description="非致命问题（不静默降级）")
    usage: AgentUsageOut = Field(default_factory=AgentUsageOut, description="理解消耗的 token")


class SourceOut(BaseModel):
    """会话里的一个附件（列表用，只给展示需要的字段）。"""

    source_uuid: str = Field(description="附件唯一标识")
    alias: str = Field(description="会话内别名（形如 @doc1）")
    display_name: str | None = Field(default=None, description="原始文件名")
    role: Literal["content", "style", "both"] = Field(description="角色")
    parse_status: str = Field(description="解析状态：pending/parsing/success/failed")
    parse_error: str | None = Field(default=None, description="解析失败原因")
    size_bytes: int | None = Field(default=None, description="文件大小（字节）")
    digest_summary: str | None = Field(default=None, description="一行理解摘要")
