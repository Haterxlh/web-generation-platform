import { useState } from 'react'
import type { SubmitEvent } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { ApiError } from '@/api/http'
import AuthCard from '@/components/common/AuthCard'
import AuthField from '@/components/common/AuthField'
import { useAuth } from '@/hooks/auth_context'
import { intendedPath } from '@/utils/navigation'

/**
 * 前端校验：与后端 user_schemas.py 的 Field 约束保持一致。
 * 返回 null 表示通过，否则返回要展示给用户的提示。
 * 放在组件外：它是纯函数，不依赖组件状态，也便于将来单测。
 */
function validate(account: string, password: string, confirmPassword: string): string | null {
  if (account.length < 2 || account.length > 32) return '账号长度需为 2~32 位'
  if (password.length < 6 || password.length > 64) return '密码长度需为 6~64 位'
  if (password !== confirmPassword) return '两次输入的密码不一致'
  return null
}

/** 注册页：注册成功后由 AuthProvider 自动登录，并跳转首页 */
export default function RegisterPage() {
  const { register } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = intendedPath(location.state)

  // 每个输入框一个状态：值从 state 来，变化写回 state（即"受控组件"）
  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    // ① 阻止浏览器默认提交行为（默认会提交表单并刷新整个页面）
    event.preventDefault()
    setError(null)

    const trimmedAccount = account.trim()

    // ② 先做前端校验：不合格直接提示，不发请求
    const invalidMessage = validate(trimmedAccount, password, confirmPassword)
    if (invalidMessage !== null) {
      setError(invalidMessage)
      return
    }

    // ③ 发请求：register 内部先 POST /register，成功后再 POST /login 自动登录
    setSubmitting(true)
    try {
      await register({ user_account: trimmedAccount, user_password: password })
      // replace: true → 用 from 替换当前历史记录，避免用户点"后退"又回到注册页
      navigate(from, { replace: true })
    } catch (err) {
      // http.ts 已把后端的 detail 提取成 ApiError.message，如"该账号已被注册"
      setError(err instanceof ApiError ? err.message : '注册失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthCard
      title="注册"
      subtitle="创建一个账号，开始生成你的 Web 应用"
      error={error}
      submitLabel="注册"
      submittingLabel="注册中…"
      submitting={submitting}
      switchText="已有账号？"
      switchTo="/login"
      switchLabel="去登录"
      onSubmit={handleSubmit}
    >
      <AuthField
        label="账号"
        value={account}
        onChange={setAccount}
        placeholder="2~32 位"
        autoComplete="username"
      />
      <AuthField
        label="密码"
        type="password"
        value={password}
        onChange={setPassword}
        placeholder="至少 6 位"
        autoComplete="new-password"
      />
      <AuthField
        label="确认密码"
        type="password"
        value={confirmPassword}
        onChange={setConfirmPassword}
        placeholder="再输一次密码"
        autoComplete="new-password"
      />
    </AuthCard>
  )
}
