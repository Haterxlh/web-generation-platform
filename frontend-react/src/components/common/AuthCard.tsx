import type { ReactNode, SubmitEvent } from 'react'
import { Link } from 'react-router-dom'

import styles from './AuthCard.module.css'

interface AuthCardProps {
  /** 卡片标题 */
  title: string
  /** 标题下的一句说明 */
  subtitle: string
  /** 错误提示；null 表示不显示 */
  error: string | null
  /** 提交按钮文案 */
  submitLabel: string
  /** 提交中按钮文案 */
  submittingLabel: string
  /** 是否正在提交（禁用按钮） */
  submitting: boolean
  /** 底部"去注册 / 去登录"的引导文案 */
  switchText: string
  /** 底部链接目标 */
  switchTo: string
  /** 底部链接文案 */
  switchLabel: string
  onSubmit: (event: SubmitEvent<HTMLFormElement>) => void
  /** 表单字段（各页面自己的输入框） */
  children: ReactNode
}

/**
 * 登录 / 注册共用的卡片外壳：居中容器 + 标题 + 错误区 + 提交按钮 + 底部切换链接。
 *
 * 抽出来的理由：两个页面此前的差别只有"标题文案 + 几个输入框"，
 * 其余 90% 的标记与样式完全重复 —— 改一处要同步改两处，正是 UI 漂移的起点。
 */
export default function AuthCard({
  title,
  subtitle,
  error,
  submitLabel,
  submittingLabel,
  submitting,
  switchText,
  switchTo,
  switchLabel,
  onSubmit,
  children,
}: AuthCardProps) {
  return (
    <div className={styles.page}>
      <form className={styles.card} onSubmit={onSubmit}>
        <h1 className={styles.title}>{title}</h1>
        <p className={styles.subtitle}>{subtitle}</p>

        {error !== null && <p className={styles.error}>{error}</p>}

        {children}

        <button className={styles.submit} type="submit" disabled={submitting}>
          {submitting ? submittingLabel : submitLabel}
        </button>

        <p className={styles.switch}>
          {switchText}
          <Link to={switchTo}>{switchLabel}</Link>
        </p>
      </form>
    </div>
  )
}
