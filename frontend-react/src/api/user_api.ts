/**
 * 用户接口模块：对应 backend-uv-fastapi/app/api/user.py（一个后端路由模块 ↔ 一个前端 api 模块）。
 * 注册 / 登录 / 获取当前登录用户（Bearer token）。
 */
import { request } from './http'
import type { LoginRequest, LoginResponse, RegisterRequest, User } from '@/types/user_types'

/** 用户模块统一前缀 */
const USER_PREFIX = '/api/user'

/** POST /api/user/register：注册新用户（成功返回用户信息，不返回 token） */
export function registerUser(req: RegisterRequest): Promise<User> {
  return request(`${USER_PREFIX}/register`, { method: 'POST', body: req })
}

/** POST /api/user/login：登录，返回 JWT + 用户信息 */
export function loginUser(req: LoginRequest): Promise<LoginResponse> {
  return request(`${USER_PREFIX}/login`, { method: 'POST', body: req })
}

/** GET /api/user/current：获取当前登录用户（token 在步骤 2 接入 http.ts 后自动带上） */
export function getCurrentUser(): Promise<User> {
  return request(`${USER_PREFIX}/current`)
}