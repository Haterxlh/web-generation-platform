import type { ChangeEvent } from 'react'

import styles from './AuthField.module.css'

interface AuthFieldProps {
  /** 字段名（显示在输入框上方） */
  label: string
  /** 当前值（受控） */
  value: string
  /** 值变化回调 */
  onChange: (value: string) => void
  /** 占位提示 */
  placeholder?: string
  /** 输入类型；密码用 'password' */
  type?: 'text' | 'password'
  /** 浏览器自动填充提示（username / current-password / new-password） */
  autoComplete?: string
}

/**
 * 带标签的输入框：登录 / 注册共用。
 *
 * ⚠️ onChange 直接回调**值**而不是事件对象：调用方几乎总是 `setState(event.target.value)`，
 * 让每个使用点都重复解包事件只会增加噪音。需要事件时再改成传事件也不难。
 */
export default function AuthField({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
  autoComplete,
}: AuthFieldProps) {
  return (
    <label className={styles.field}>
      <span className={styles.label}>{label}</span>
      <input
        className={styles.input}
        type={type}
        value={value}
        onChange={(event: ChangeEvent<HTMLInputElement>) => onChange(event.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete}
      />
    </label>
  )
}
