/**
 * HTTP 请求封装：统一 baseURL、JSON 序列化与错误处理。
 * 后端为 FastAPI（backend-uv-fastapi），本文件对应后端的请求入口约定。
 */

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
  /** 附加请求头 */
  headers?: HeadersInit
  signal?: AbortSignal
}

/** 发起 JSON 请求并解析响应；非 2xx 时抛出 ApiError */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, headers, signal } = options

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })

  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`
    try {
      const payload = (await response.json()) as { detail?: unknown }
      if (typeof payload.detail === 'string') message = payload.detail
    } catch {
      // 响应体非 JSON 时保留默认错误信息
    }
    throw new ApiError(message, response.status)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
