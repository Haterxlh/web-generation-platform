/**
 * HTTP 请求封装：统一 baseURL、JSON 序列化、鉴权头与错误处理。
 * 后端为 FastAPI（backend-uv-fastapi），本文件对应后端的请求入口约定。
 */
import { getToken } from '@/utils/token'

/** 后端基地址；留空时同源请求走 Vite 开发代理 /api */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

/** 接口请求错误：带 HTTP 状态码 */
export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  /** 请求体（对象，自动 JSON 序列化） */
  body?: unknown
  /**
   * multipart 上传用的表单体（与 body 互斥）。
   * ⚠️ 走 formData 时**绝不能手写 Content-Type**：boundary 由浏览器生成，
   * 手写成 `multipart/form-data` 会丢掉 boundary，后端直接 422。
   */
  formData?: FormData
  /** 附加请求头 */
  headers?: HeadersInit
  signal?: AbortSignal
}

/** 401 处理函数：由 AuthProvider 注册（清除登录态并跳转登录页） */
type UnauthorizedHandler = () => void

let unauthorizedHandler: UnauthorizedHandler | null = null

/** 注册 401 处理函数；传 null 表示注销（组件卸载时用） */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  unauthorizedHandler = handler
}

/** 从 FastAPI 错误响应体中提取可读的错误信息 */
function extractErrorMessage(payload: unknown, status: number): string {
  const detail = (payload as { detail?: unknown } | null)?.detail

  // 业务异常：detail 是字符串，如"该账号已被注册"
  if (typeof detail === 'string') return detail

  // 参数校验失败 422：detail 是 [{ loc, msg, type }, ...]
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item as { msg?: unknown }).msg)
      .filter((msg): msg is string => typeof msg === 'string')
    if (messages.length > 0) return messages.join('；')
  }

  return `请求失败（HTTP ${status}）`
}

/** 发起 JSON 请求并解析响应；非 2xx 时抛出 ApiError */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, formData, headers, signal } = options

  // 已登录则自动附加 Bearer token，页面组件无须关心鉴权头
  const token = getToken()

  // 上传走 multipart：不设 Content-Type（浏览器补 boundary），body 直接给 FormData
  const isUpload = formData !== undefined

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      ...(isUpload ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: isUpload ? formData : body === undefined ? undefined : JSON.stringify(body),
    signal,
  })

  if (!response.ok) {
    let payload: unknown = null
    try {
      payload = await response.json()
    } catch {
      // 响应体非 JSON 时保留默认错误信息
    }

    // 401：登录态失效（token 过期/无效）→ 通知外部清理
    if (response.status === 401) unauthorizedHandler?.()

    throw new ApiError(extractErrorMessage(payload, response.status), response.status)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
