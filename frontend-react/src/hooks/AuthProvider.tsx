/**
 * AuthProvider：维护登录状态，并通过 AuthContext 把能力提供给整棵组件树。
 * 挂载在 main.tsx 的最外层（Router 之外），因此这里不做任何路由跳转。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { setUnauthorizedHandler } from '@/api/http'
import { getCurrentUser, loginUser, registerUser } from '@/api/user_api'
import { AuthContext } from '@/hooks/auth_context'
import type { AuthContextValue } from '@/hooks/auth_context'
import type { LoginRequest, RegisterRequest, User } from '@/types/user_types'
import { clearToken, getToken, setToken } from '@/utils/token'

/** 登录状态提供者：包在 <App /> 外层使用 */
export default function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  // 初始为 true：表示"正在尝试用本地 token 恢复登录态"
  const [initializing, setInitializing] = useState(true)

  // ① 刷新页面后，凭 localStorage 里的 token 把用户信息换回来
  useEffect(() => {
    // 没有 token 说明从未登录或已登出，直接结束初始化，不必打扰后端
    if (getToken() === null) {
      setInitializing(false)
      return
    }

    // cancelled：组件卸载（含 StrictMode 开发期二次挂载）后不再 setState
    // 用于标记当前组件是否已经被卸载了，如果当前组件已卸载，这个时候getCurrentUser()的异步信息返回了
    // 由于在卸载的时候，执行了cancelled = true，那么当前的页面就不会再更新状态
    let cancelled = false

    getCurrentUser()
      .then((current) => {
        if (!cancelled) setUser(current)
      })
      .catch(() => {
        // token 过期/无效：清掉本地残留，保持"未登录"状态
        clearToken()
      })
      .finally(() => {
        if (!cancelled) setInitializing(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  // ② 注册 401 处理：任何请求遇到"登录态失效"都会清空本地登录状态
  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearToken()
      setUser(null)
    })
    return () => setUnauthorizedHandler(null)
  }, [])

  const login = useCallback(async (req: LoginRequest) => {
    const res = await loginUser(req)
    setToken(res.access_token)
    setUser(res.user)
  }, [])

  const register = useCallback(async (req: RegisterRequest) => {
    // 后端注册接口只返回用户信息、不返回 token，所以注册成功后直接再登录一次
    await registerUser(req)
    const res = await loginUser({
      user_account: req.user_account,
      user_password: req.user_password,
    })
    setToken(res.access_token)
    setUser(res.user)
  }, [])

  const logout = useCallback(() => {
    clearToken()
    setUser(null)
  }, [])

  // 性能优化：
  // 避免：每次组件函数执行（每次渲染）新建一个对象。
  // React 一看：
  // → value 和上次不是同一个对象
  // → 认为"上下文的值变了"
  // → 把所有 useAuth() 的消费者全部重新渲染一遍，哪怕 user 一点没变。
  // useMemo 缓存值, 保持 value 引用稳定，避免每次渲染都让所有消费 useAuth 的组件重渲染
  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isAuthenticated: user !== null, // isAuthenticated 依赖于状态 user, 只要 user 一变，isAuthenticated 就会变
      initializing,
      login,
      register,
      logout,
    }),
    [user, initializing, login, register, logout], // useCallback 缓存函数
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
