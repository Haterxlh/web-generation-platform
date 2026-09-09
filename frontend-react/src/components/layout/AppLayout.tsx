import { NavLink, Outlet } from 'react-router-dom'

const navItems = [
  { to: '/', label: '首页', end: true },
  { to: '/generate', label: '生成应用' },
  { to: '/projects', label: '我的项目' },
]

/** 全局布局：顶部导航 + 内容区（Outlet）+ 页脚 */
export default function AppLayout() {
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
      </header>

      <main className="app-main">
        <Outlet />
      </main>

      <footer className="app-footer">复刻 Web 生成平台 · React + FastAPI</footer>
    </div>
  )
}
