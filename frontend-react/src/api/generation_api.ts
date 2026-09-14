/**
 * 生成接口模块：对应 backend-uv-fastapi/app/api/generation.py（一个后端路由模块 ↔ 一个前端 api 模块）。
 * 创建生成 / 我的生成历史 / 任务详情 / 预览票据（写 HttpOnly Cookie）。
 */
import { request } from './http'
import type {
  GenerateRequest,
  GenerationListResponse,
  GenerationTask,
  PreviewTicketResponse,
} from '@/types/generation_types'

/** 生成模块统一前缀 */
const GENERATION_PREFIX = '/api/generation'

/**
 * POST /api/generation/create：创建并执行一次生成。
 * ⚠️ 后端是同步执行，通常 30~120 秒才返回，调用方必须做"生成中"的界面状态。
 */
export function createGeneration(req: GenerateRequest): Promise<GenerationTask> {
  return request(`${GENERATION_PREFIX}/create`, { method: 'POST', body: req })
}

/** GET /api/generation/list：我的生成历史（分页，新的在前） */
export function listGenerations(page = 1, pageSize = 20): Promise<GenerationListResponse> {
  // 用 URLSearchParams 拼查询串：自动处理编码，将来加关键词搜索也不用改写法
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  })
  return request(`${GENERATION_PREFIX}/list?${query.toString()}`)
}

/** GET /api/generation/{task_uuid}：查询单个任务详情（只能查自己的） */
export function getGeneration(taskUuid: string): Promise<GenerationTask> {
  return request(`${GENERATION_PREFIX}/${encodeURIComponent(taskUuid)}`)
}

/**
 * POST /api/generation/{task_uuid}/preview-ticket：签发预览票据。
 * 响应里的 Set-Cookie 由浏览器自动保存（HttpOnly，前端读不到也不需要读）；
 * 拿到 preview_url 后直接 window.open 即可，切勿把 token 拼进 URL。
 */
export function issuePreviewTicket(taskUuid: string): Promise<PreviewTicketResponse> {
  return request(`${GENERATION_PREFIX}/${encodeURIComponent(taskUuid)}/preview-ticket`, {
    method: 'POST',
  })
}
