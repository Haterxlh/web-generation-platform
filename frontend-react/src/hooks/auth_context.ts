/**
 * 登录上下文：集中存放"当前登录用户"与登录/注册/登出动作。
 * 本文件是纯 TS（不含 JSX），只定义 Context、类型和读取用的 useAuth；
 * 真正持有状态的组件在 AuthProvider.tsx 里实现。
 */
import { createContext, useContext } from 'react'

import type { LoginRequest, RegisterRequest, User } from '@/types/user_types'

/** 登录上下文对外暴露的能力 */
export interface AuthContextValue {
  /** 当前登录用户；未登录为 null */
  user: User | null
  /** 是否已登录（user 非空即为已登录） */
  isAuthenticated: boolean
  /** 首次进入应用时是否仍在恢复登录态（避免刷新瞬间被误判为未登录） */
  initializing: boolean
  /** 登录：成功后写入 token 与用户信息 */
  login: (req: LoginRequest) => Promise<void>
  /** 注册：成功后自动登录 */
  register: (req: RegisterRequest) => Promise<void>
  /** 登出：清除本地 token 与用户信息 */
  logout: () => void
}

/** 默认值设为 null：这样"忘记包 AuthProvider"能被 useAuth 明确报错，而不是默默拿到空对象 */
export const AuthContext = createContext<AuthContextValue | null>(null)

/** 读取登录上下文；必须在 <AuthProvider> 内部使用 */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (ctx === null) throw new Error('useAuth 必须在 <AuthProvider> 内部使用')
  return ctx
}
