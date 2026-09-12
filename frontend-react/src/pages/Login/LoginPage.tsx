import { useState } from 'react'
import type { SubmitEvent } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'

import { ApiError } from '@/api/http'
import { useAuth } from '@/hooks/auth_context'
import { intendedPath } from '@/utils/navigation'

/** 登录页：登录成功后写入 token 跳转到目标页（默认首页） */
export default function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  // 被守卫拦下时带过来的目标路径；直接访问 /login 时为 undefined → 回首页
  const from = intendedPath(location.state) // ✅ 顶层计算

  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setError(null)

    const trimmedAccount = account.trim()

    // 登录只校验"有没有填"：长度规则属于注册环节，老账号的密码策略未必与当前注册规则一致
    if (trimmedAccount === '' || password === '') {
      setError('请输入账号和密码')
      return
    }

    setSubmitting(true)
    try {
      await login({ user_account: trimmedAccount, user_password: password })
      navigate(from, { replace: true })
    } catch (err) {
      // 后端登录失败返回 400 + "账号或密码错误"（刻意不区分账号不存在/密码错误，防账号探测）
      setError(err instanceof ApiError ? err.message : '登录失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={handleSubmit}>
        <h1 className="auth-title">登录</h1>
        <p className="auth-subtitle">登录后即可生成与管理你的项目</p>

        {error !== null && <p className="auth-error">{error}</p>}

        <label className="auth-field">
          <span className="auth-label">账号</span>
          <input
            className="auth-input"
            value={account}
            onChange={(event) => setAccount(event.target.value)}
            placeholder="请输入账号"
            autoComplete="username"
          />
        </label>

        <label className="auth-field">
          <span className="auth-label">密码</span>
          <input
            className="auth-input"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="请输入密码"
            autoComplete="current-password"
          />
        </label>

        <button className="auth-button" type="submit" disabled={submitting}>
          {submitting ? '登录中…' : '登录'}
        </button>

        <p className="auth-switch">
          还没有账号？<Link to="/register">去注册</Link>
        </p>
      </form>
    </div>
  )
}
