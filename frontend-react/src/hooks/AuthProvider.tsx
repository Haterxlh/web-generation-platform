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
import { clearAgentSessionUuid } from '@/utils/agent_session'
import { clearToken, getToken, setToken } from '@/utils/token'

/** 登录状态提供者：包在 <App /> 外层使用 */
export default function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  // 初始值直接由"本地有没有 token"推导，而不是一律先置 true：
  // 没有 token 时根本不需要恢复登录态，也就没必要先 true 再在 effect 里同步改回 false
  // —— 那次同步 setState 既多余、又会触发一次级联渲染（react-hooks/set-state-in-effect），
  //    而且会让未登录用户在第一帧看到"正在恢复登录态"的闪烁。
  const [initializing, setInitializing] = useState(() => getToken() !== null)

  // ① 刷新页面后，凭 localStorage 里的 token 把用户信息换回来
  useEffect(() => {
    // 没有 token 说明从未登录或已登出：initializing 的 useState 初值已经是 false，
    // 这里直接结束即可 —— 既不必打扰后端，也不需要在 effect 体内 setState
    if (getToken() === null) return

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
    // 会话标识必须一起清：它是**上一个账号**的会话，留着会让新登录的人
    // 打开生成页时去回放别人的会话（后端会 404，虽然安全但看起来像"数据丢了"）
    clearAgentSessionUuid()
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
