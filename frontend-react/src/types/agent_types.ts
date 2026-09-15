/**
 * Agent 对话模块数据类型：与 backend-uv-fastapi/app/schemas/agent_schemas.py 的 Pydantic 模型对齐。
 * 后端出入参字段为蛇形命名，前端保持 snake_case，不做驼峰转换。
 *
 * 这一层是"会话式生成"的前半段：聊天澄清需求（本文件）→ 生成任务（generation_types.ts）。
 */

/** 附件角色：内容源 / 风格源 / 两者都是（只有 HTML 能当风格源，由后端 Python 侧否决） */
export type AttachmentRole = 'content' | 'style' | 'both'

/** 解析状态：pending/parsing 是接口文档里的中间态，前端主要见到 success / failed */
export type ParseStatus = 'pending' | 'parsing' | 'success' | 'failed'

/** 对话里引用的一个附件：对应后端 AttachmentRef（role 由后端以库中那一行为准，前端传什么都无效） */
export interface AttachmentRef {
  /** 附件唯一标识（上传接口返回的 source_uuid） */
  source_uuid: string
  /** 附件角色（后端会忽略请求里的值，以库里为准；这里传回上传时的结果即可） */
  role: AttachmentRole
}

/** 一轮对话的请求体：对应后端 AgentChatRequest */
export interface AgentChatRequest {
  /** 会话标识；不传表示新建一个会话 */
  session_uuid?: string | null
  /** 用户这一轮说的话（后端限制 1~4000 字，**不允许只传附件不说话**） */
  message: string
  /** 本轮引用的附件 */
  attachments?: AttachmentRef[]
}

/** 需求槽位：对应后端 RequirementSlotsOut */
export interface RequirementSlots {
  /** 站点类型 */
  site_kind: string | null
  /** 核心功能列表 */
  features: string[]
  /** 目标用户 */
  audience: string | null
  /** 视觉风格与配色倾向 */
  style: string | null
  /** 是否需要数据持久化；null 表示用户尚未提及（三态，不要当成 false） */
  need_persistence: boolean | null
}

/** token 用量：对应后端 AgentUsageOut */
export interface AgentUsage {
  input_tokens: number
  output_tokens: number
  reasoning_tokens: number
}

/** 一轮对话的结果：对应后端 AgentChatResponse */
export interface AgentChatResponse {
  /** 会话标识（新建时由后端生成，后续轮次必须带上） */
  session_uuid: string
  /** 本轮助手消息的唯一标识 */
  message_uuid: string
  /** 助手回复（直接展示给用户） */
  reply: string
  /** 识别出的意图 */
  intent: 'chat' | 'generate'
  /** 需求完备度 */
  readiness: 'ready' | 'needs_clarification'
  /** 当前累计的需求槽位 */
  slots: RequirementSlots
  /** 还缺的槽位名 */
  missing_slots: string[]
  /** 是否已可触发生成（前端据此显示"开始生成"） */
  ready_to_generate: boolean
  /** 本轮是否走了降级路径（排查用） */
  degraded: boolean
  /** 本轮 token 用量 */
  usage: AgentUsage
}

/** 会话里的一条消息：对应后端 AgentMessageOut */
export interface AgentMessage {
  message_uuid: string
  /** 角色：user / assistant / system / tool */
  role: string
  /** 消息正文（含 @docN 别名原文） */
  content: string
  /** 该轮的意图判定结果 */
  intent: string | null
  create_time: string | null
}

/** 会话详情（含消息列表，用于刷新后回放）：对应后端 AgentSessionDetailResponse */
export interface AgentSessionDetail {
  session_uuid: string
  title: string | null
  status: string
  slots: RequirementSlots
  summary: string
  /** 消息列表（旧 → 新） */
  messages: AgentMessage[]
}

/** 风格规范：对应后端 StyleSpecOut（结构化取值来自解析器实测，不是模型转述） */
export interface StyleSpec {
  colors: string[]
  font_families: string[]
  font_sizes: string[]
  border_radius: string[]
  spacing: string[]
  layout: Record<string, number>
  notes: string
}

/** 文档理解结果：对应后端 RequirementDigestOut */
export interface RequirementDigest {
  summary: string
  role: AttachmentRole
  content_points: string[]
  style_spec: StyleSpec
  constraints: string[]
  open_questions: string[]
}

/**
 * 附件上传结果：对应后端 SourceUploadResponse。
 * ⚠️ `parse_status='failed'` **不是** HTTP 错误：文件与别名都已就绪，
 * 只是读不懂（扫描版 PDF、编码无法识别等）。前端展示 `parse_error` 即可，不要阻断对话。
 */
export interface SourceUploadResponse {
  source_uuid: string
  /** 会话内别名（形如 @doc1），前端渲染成 chip */
  alias: string
  /** 原始文件名（仅用于展示） */
  display_name: string | null
  size_bytes: number
  role: AttachmentRole
  parse_status: ParseStatus
  parse_error: string | null
  digest: RequirementDigest | null
  degraded: boolean
  warnings: string[]
  usage: AgentUsage
}

/** 会话里的一个附件（列表用）：对应后端 SourceOut */
export interface Source {
  source_uuid: string
  alias: string
  display_name: string | null
  role: AttachmentRole
  parse_status: ParseStatus
  parse_error: string | null
  size_bytes: number | null
  digest_summary: string | null
}
