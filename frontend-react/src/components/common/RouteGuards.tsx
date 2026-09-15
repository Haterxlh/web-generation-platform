/**
 * 路由守卫：控制"哪些页面必须登录后才能看""哪些页面只允许未登录时看"。
 * 用法：把目标页面作为 children 包进去。
 */
import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import { useAuth } from '@/hooks/auth_context'
import { intendedPath } from '@/utils/navigation'
import styles from './RouteGuards.module.css'

/** 恢复登录态时的占位界面（全屏居中，避免"未登录 → 登录页 → 又跳回"的闪烁） */
function AuthLoading() {
  return <div className={styles.loading}>正在恢复登录状态…</div>
}

/** 需要登录才能访问：未登录时跳转登录页，并记住原本要去的路径 */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated, initializing } = useAuth()
  const location = useLocation()

  // 还在用本地 token 向后端确认身份：先不下结论，否则会闪一下登录页
  if (initializing) return <AuthLoading />

  if (!isAuthenticated) {
    // state.from：把"用户原本想去的路径"悄悄传给登录页（不会出现在 URL 里）
    return <Navigate to="/login" state={{ from: location.pathname }} replace />
  }

  return <>{children}</>
}

/** 只允许未登录访问（登录页、注册页）：已登录则直接送回首页 */
export function GuestOnly({ children }: { children: ReactNode }) {
  const { isAuthenticated, initializing } = useAuth()
  const location = useLocation()

  if (initializing) return <AuthLoading />

  // 已登录就别停在登录/注册页；但要去"用户原本想去的地方"，而不是死板的首页
  // 不加 replace 的话，登录页会留在浏览器历史里 —— 用户点"后退"会回到 /login，又被守卫送走，来回弹。
  if (isAuthenticated) return <Navigate to={intendedPath(location.state)} replace />

  return <>{children}</>
}
