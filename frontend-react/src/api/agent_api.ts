/**
 * Agent 对话接口模块：对应 backend-uv-fastapi/app/api/agent.py
 * （一个后端路由模块 ↔ 一个前端 api 模块）。
 *
 * 会话式生成的前半段在这里：聊天澄清需求 → 上传附件拿别名 → 需求齐了再去建生成任务
 * （建任务与轮询在 generation_api.ts，那里是通用的生成链路）。
 *
 * ⚠️ 本模块的接口走**后端 PG**（会话 / 消息 / 附件都在 Agent 域），
 * 与 MySQL 侧的生成任务不是同一个库 —— 但它们通过 session_uuid / source_uuid 对接。
 */
import { request } from './http'
import type {
  AgentChatRequest,
  AgentChatResponse,
  AgentSessionDetail,
  Source,
  SourceUploadResponse,
} from '@/types/agent_types'

/** Agent 模块统一前缀 */
const AGENT_PREFIX = '/api/agent'

/**
 * POST /api/agent/chat：与 Agent 对话（澄清需求）。
 *
 * ⚠️ 这是**同步**接口，一轮通常 3~8 秒（含 1~2 次模型调用）——
 * 用户本来就在等回复，所以不走队列。真正的耗时操作是"生成"。
 * 不传 `session_uuid` 表示新建会话，响应里会带回新的 `session_uuid`。
 */
export function chatWithAgent(req: AgentChatRequest): Promise<AgentChatResponse> {
  return request(`${AGENT_PREFIX}/chat`, { method: 'POST', body: req })
}

/** GET /api/agent/session/{session_uuid}：查会话详情与历史消息（只能查自己的，刷新后回放用） */
export function getAgentSession(sessionUuid: string): Promise<AgentSessionDetail> {
  return request(`${AGENT_PREFIX}/session/${encodeURIComponent(sessionUuid)}`)
}

/**
 * POST /api/agent/source/upload：上传附件（multipart）。
 *
 * ⚠️ 两个容易踩的点：
 * 1. 附件别名的作用域是**会话**，所以必须先有会话（先发一条消息）才能上传；
 * 2. 这是**同步**接口，长文档要串行多次模型调用，耗时随篇幅增长 ——
 *    界面上要给"解析中"的反馈，不要让它看起来像卡死。
 *
 * `parse_status='failed'` 时接口仍是 200：文件与别名都就绪，只是读不懂。
 */
export function uploadSource(sessionUuid: string, file: File): Promise<SourceUploadResponse> {
  const formData = new FormData()
  // 字段名必须与后端 upload_source 的形参一致：session_uuid / file
  formData.append('session_uuid', sessionUuid)
  formData.append('file', file)
  return request(`${AGENT_PREFIX}/source/upload`, { method: 'POST', formData })
}

/** GET /api/agent/source/list：查询会话下的附件（把 @docN 渲染成文件名 chip 的数据来源） */
export function listSources(sessionUuid: string): Promise<Source[]> {
  const query = new URLSearchParams({ session_uuid: sessionUuid })
  return request(`${AGENT_PREFIX}/source/list?${query.toString()}`)
}
