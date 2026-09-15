/**
 * 生成页的纯逻辑工具（**不含组件**）。
 *
 * 为什么单独一个文件：`react-refresh/only-export-components` 是 error 级规则，
 * 组件文件里混着导出常量 / 函数会让 HMR 退化 —— 所以类型、校验、文案映射一律放这里。
 */
import { ApiError } from '@/api/http'
import type { AgentSessionDetail, AttachmentRole, ParseStatus, RequirementSlots } from '@/types/agent_types'

/** 需求描述长度约束（与后端 GenerateRequest 的 2~2000 对齐） */
export const PROMPT_MIN = 2
export const PROMPT_MAX = 2000

/** 附件选择框的 accept（与后端 SUPPORTED_EXTENSIONS 一致） */
export const ACCEPT_UPLOAD = '.pdf,.html,.htm,.md,.txt'

/** 附件体积上限（与后端 MAX_PARSE_BYTES = 10MB 对齐，超限在前端就拦下，省一次无效上传） */
export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024

/** 轮询总时长上限。后端有 15 分钟任务超时 + 僵尸回收兜底，这里再放一个稍宽的上限 */
export const POLL_TIMEOUT_MS = 20 * 60 * 1000

/**
 * 待发送/已引用的附件（上传接口的返回，去掉 digest 等展示用不到的字段）。
 *
 * `parse_status='failed'` 仍然保留在列表里：文件与别名都已就绪，只是读不懂 ——
 * 让用户看得见"这个文件没读成功"，比他以为文件生效了要好。
 */
export interface ChatAttachment {
  source_uuid: string
  alias: string
  display_name: string | null
  role: AttachmentRole
  parse_status: ParseStatus
  parse_error: string | null
}

/** 聊天区的一条消息 */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** 用户消息里引用的附件（渲染成 chip） */
  attachments: ChatAttachment[]
  /** normal=正常对话；notice=系统提示（如附件解析失败），样式不同 */
  variant: 'normal' | 'notice'
}

/** 生成一个消息 id（仅用于 React key，不参与后端交互） */
export function newMessageId(): string {
  return `m${Math.random().toString(36).slice(2, 10)}`
}

/**
 * 校验需求描述。返回 null 表示通过，否则返回给用户看的提示。
 *
 * 注意：**先 trim 再校验**（防止一串空格绕过长度检查），
 * 而字符计数器要显示原值（用户所见即所得）。
 */
export function validatePrompt(text: string): string | null {
  const trimmed = text.trim()
  if (trimmed.length < PROMPT_MIN) return `需求描述至少 ${PROMPT_MIN} 个字`
  if (trimmed.length > PROMPT_MAX) return `需求描述最多 ${PROMPT_MAX} 个字`
  return null
}

/**
 * 校验待上传的附件（体积与类型）。返回 null 表示通过。
 *
 * 后端两个都会再校验一遍 —— 前端这层只是为了不让用户白等一次上传/解析。
 */
export function validateUpload(file: File): string | null {
  if (file.size > MAX_UPLOAD_BYTES) {
    return `文件超过 ${MAX_UPLOAD_BYTES / 1024 / 1024} MB，请压缩后再传`
  }
  const dot = file.name.lastIndexOf('.')
  const suffix = dot >= 0 ? file.name.slice(dot).toLowerCase() : ''
  const allowed = ACCEPT_UPLOAD.split(',')
  if (!allowed.includes(suffix)) {
    return `只支持 ${ACCEPT_UPLOAD} 这些格式`
  }
  return null
}

/**
 * 把异常转成给用户看的文案。
 *
 * 403 单独处理：FastAPI 的 HTTPBearer 在"请求头没带 token"时返回的是 403（不是 401），
 * 文案是英文的 Not authenticated，直接展示很突兀。
 */
export function toErrorMessage(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return '登录状态已失效，请重新登录后再试'
    // 后端失败时 detail 形如"任务队列不可用，请稍后重试"，本身就是给用户看的中文
    return err.message
  }
  return fallback
}

/**
 * 必备槽位（与后端 `app/agents/state.py` 的 `REQUIRED_SLOTS` 保持一致）。
 * 少一个就会卡在 `needs_clarification`，所以前端要能自己算出来。
 */
const REQUIRED_SLOTS = ['site_kind', 'features'] as const

/** 还缺哪些**必备**槽位（用于刷新回放后仍能告诉用户"差什么"） */
export function missingRequiredSlots(slots: RequirementSlots | null): string[] {
  if (slots === null) return [...REQUIRED_SLOTS]
  const missing: string[] = []
  if ((slots.site_kind ?? '').trim() === '') missing.push(REQUIRED_SLOTS[0])
  if (slots.features.length === 0) missing.push(REQUIRED_SLOTS[1])
  return missing
}

/**
 * 需求是否已足够开始生成。
 *
 * 与后端 `REQUIRED_SLOTS = ("site_kind", "features")` 保持一致：
 * 刷新页面后拿不到 `ready_to_generate`（会话详情接口没有这个字段），只能按同一口径重算 ——
 * 两处口径若漂移，就会出现"按钮可点但后端判定信息不足"的错位。
 */
export function isReadyToGenerate(slots: RequirementSlots | null): boolean {
  return missingRequiredSlots(slots).length === 0
}

/** 槽位 → 一句需求摘要（会话详情里有后端给的 summary，聊天响应里没有，故这里自己拼一份） */
export function describeSlots(slots: RequirementSlots | null): string {
  if (slots === null) return ''
  const bits: string[] = []
  if (slots.site_kind) bits.push(`类型是${slots.site_kind}`)
  if (slots.features.length > 0) bits.push(`功能包含${slots.features.join('、')}`)
  if (slots.audience) bits.push(`面向${slots.audience}`)
  if (slots.style) bits.push(`风格是${slots.style}`)
  if (slots.need_persistence !== null) {
    bits.push(slots.need_persistence ? '数据需要存下来' : '数据不需要存储')
  }
  return bits.join('；')
}

/**
 * 把槽位拼成提交给生成接口的 prompt。
 *
 * ⚠️ **只拼槽位，绝不带上原始对话**（2026-09-15 修掉的一个真实 bug）：
 * 生成接口的 prompt 会被 **worker 的 ROUTING 节点再判一次完备度**，而 worker 拿不到会话历史
 * （它只读 `task.prompt` 与附件 digest）。此前这里把"第一条用户消息"附在后面当上下文，
 * 于是当用户开场问过一句"你能做什么"时，ROUTING 会把整段读成**能力咨询**并停在 `clarifying` ——
 * 而用户明明已经对着需求确认卡片点过"开始生成"了。
 *
 * 槽位就是**用户刚刚确认过的那份需求**（与确认卡片显示的内容同源），所以它必须自洽、可独立读懂：
 * 按"做什么 → 有哪些功能 → 给谁用 → 什么风格 → 要不要存数据"的口气写成完整句子，
 * 而不是把一堆字段名丢给判定方。`fallbackText` 只在槽位拼不出东西时兜底。
 */
export function buildGenerationPrompt(slots: RequirementSlots | null, fallbackText: string): string {
  const lines: string[] = []

  if (slots !== null) {
    const siteKind = (slots.site_kind ?? '').trim()
    if (siteKind !== '') lines.push(`请生成一个${siteKind}页面。`)
    if (slots.features.length > 0) lines.push(`核心功能：${slots.features.join('、')}。`)
    if ((slots.audience ?? '').trim() !== '') lines.push(`目标用户：${slots.audience}。`)
    if ((slots.style ?? '').trim() !== '') lines.push(`视觉风格：${slots.style}。`)
    if (slots.need_persistence !== null) {
      lines.push(slots.need_persistence ? '数据需要保存，刷新后不能丢。' : '数据不需要保存。')
    }
  }

  // 槽位拼不出东西时才退回用户原话（例如整轮都只被判成 chat）
  if (lines.length === 0) {
    const fallback = fallbackText.trim()
    if (fallback !== '') lines.push(fallback)
  }

  // 两边都空时给一个兜底，避免提交空需求（后端要求 >= 2 字）
  if (lines.length === 0) return '请按我在对话里说过的需求生成一个网页。'
  return lines.join('\n').slice(0, PROMPT_MAX)
}

/** 会话详情 → 聊天区消息列表（刷新后回放；只保留 user / assistant，system / tool 不展示） */
export function sessionDetailToMessages(detail: AgentSessionDetail): ChatMessage[] {
  return detail.messages
    .filter((item) => item.role === 'user' || item.role === 'assistant')
    .map((item) => ({
      id: item.message_uuid,
      role: item.role === 'user' ? 'user' : 'assistant',
      content: item.content,
      // 历史消息里的附件引用不进 chip：详情接口没返回 attachments，
      // 而正文里的 @docN 原文仍在（这正是"消息只存别名"的设计意图）
      attachments: [],
      variant: 'normal',
    }))
}

/**
 * 从消息列表里取出**最后一条**用户发言（仅在槽位拼不出需求时作为兜底）。
 *
 * 取最后一条而不是第一条：第一条常常是"你能做什么"这类开场白，
 * 拿它当需求会把生成链路带偏（这正是 2026-09-15 修掉的那个 bug）。
 */
export function lastUserText(messages: ChatMessage[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const item = messages[index]
    if (item.role === 'user') return item.content
  }
  return ''
}

/** 已等待时长的可读文案 */
export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${minutes} 分 ${rest} 秒`
}
