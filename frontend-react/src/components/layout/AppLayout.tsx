import { Link, NavLink, Outlet } from 'react-router-dom'

import { useAuth } from '@/hooks/auth_context'

const navItems = [
  { to: '/', label: '首页', end: true },
  { to: '/generate', label: '生成应用' },
  { to: '/projects', label: '我的项目' },
]

/** 全局布局：顶部导航 + 用户区 + 内容区（Outlet）+ 页脚 */
export default function AppLayout() {
  const { user, isAuthenticated, logout } = useAuth()

  function handleLogout(): void {
    // 只清状态，不手动跳转：若当前页需要登录，RequireAuth 会自动把用户送到登录页
    logout()
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <span className="app-logo">Web 生成平台</span>

        <nav className="app-nav">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => (isActive ? 'nav-link active' : 'nav-link')}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* 用户区：未登录显示入口，已登录显示账号 + 退出 */}
        <div className="app-user">
          {isAuthenticated && user !== null ? (
            <>
              {/* user_name 后端允许为 null，回退到账号显示 */}
              <span className="app-user-name">{user.user_name ?? user.user_account}</span>
              <button className="app-user-button" type="button" onClick={handleLogout}>
                退出
              </button>
            </>
          ) : (
            <>
              <Link className="app-user-link" to="/login">
                登录
              </Link>
              <Link className="app-user-link" to="/register">
                注册
              </Link>
            </>
          )}
        </div>
      </header>

      <main className="app-main">
        <Outlet />
      </main>

      <footer className="app-footer">Web 生成平台 · React + FastAPI</footer>
    </div>
  )
}
