import type { ReactNode } from 'react'

import styles from './Button.module.css'

interface ButtonProps {
  children: ReactNode
  /** primary=主操作（实心蓝）；ghost=次要操作（描边） */
  variant?: 'primary' | 'ghost'
  size?: 'md' | 'sm'
  /** 是否撑满父容器宽度 */
  block?: boolean
  /** 表单提交按钮传 'submit' */
  type?: 'button' | 'submit'
  disabled?: boolean
  title?: string
  onClick?: () => void
}

/**
 * 通用按钮：把「主/次操作 + 两种尺寸」这类反复出现的样式收在一处。
 *
 * 不做成"万能组件"：Loading 态、图标按钮等特殊形态各自在业务组件里处理，
 * 硬塞进这里会让 props 迅速膨胀。
 */
export default function Button({
  children,
  variant = 'primary',
  size = 'md',
  block = false,
  type = 'button',
  disabled = false,
  title,
  onClick,
}: ButtonProps) {
  const className = [
    styles.button,
    styles[variant],
    styles[size],
    block ? styles.block : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <button className={className} type={type} disabled={disabled} title={title} onClick={onClick}>
      {children}
    </button>
  )
}
