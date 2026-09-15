import { useState } from 'react'
import type { SubmitEvent } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'

import { ApiError } from '@/api/http'
import AuthCard from '@/components/common/AuthCard'
import AuthField from '@/components/common/AuthField'
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
    <AuthCard
      title="登录"
      subtitle="登录后即可生成与管理你的项目"
      error={error}
      submitLabel="登录"
      submittingLabel="登录中…"
      submitting={submitting}
      switchText="还没有账号？"
      switchTo="/register"
      switchLabel="去注册"
      onSubmit={handleSubmit}
    >
      <AuthField
        label="账号"
        value={account}
        onChange={setAccount}
        placeholder="请输入账号"
        autoComplete="username"
      />
      <AuthField
        label="密码"
        type="password"
        value={password}
        onChange={setPassword}
        placeholder="请输入密码"
        autoComplete="current-password"
      />
    </AuthCard>
  )
}
