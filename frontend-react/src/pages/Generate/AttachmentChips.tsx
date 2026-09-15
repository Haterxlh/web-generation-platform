import type { ChatAttachment } from './generate_utils'
import styles from './AttachmentChips.module.css'

interface AttachmentChipsProps {
  items: ChatAttachment[]
  /** 传了才显示"移除"按钮（已发送消息里的 chip 不可移除） */
  onRemove?: (sourceUuid: string) => void
}

/** 解析状态 → chip 的附加样式（failed / parsing 需要视觉区分） */
function stateClass(status: ChatAttachment['parse_status']): string {
  if (status === 'failed') return styles.chipFailed
  if (status === 'pending' || status === 'parsing') return styles.chipParsing
  return ''
}

/** 解析状态 → 悬浮提示的补充说明 */
function stateHint(item: ChatAttachment): string {
  if (item.parse_status === 'failed') return `解析失败：${item.parse_error ?? '文件内容读不出来'}`
  if (item.parse_status === 'pending' || item.parse_status === 'parsing') return '解析中…'
  return '解析成功'
}

/**
 * 附件 chip 列表：`@doc1` 别名 + 原始文件名 + 解析状态。
 *
 * 为什么别名与文件名都要显示：别名是**消息正文里真实存在的引用**（用户看到 `@doc1` 得知道它是谁），
 * 文件名是用户自己认得出的东西 —— 只给一个都不够用。
 */
export default function AttachmentChips({ items, onRemove }: AttachmentChipsProps) {
  if (items.length === 0) return null

  return (
    <ul className={styles.list}>
      {items.map((item) => (
        <li
          key={item.source_uuid}
          className={`${styles.chip} ${stateClass(item.parse_status)}`.trim()}
          title={stateHint(item)}
        >
          <span className={styles.alias}>{item.alias}</span>
          <span className={styles.name}>{item.display_name ?? '（未命名文件）'}</span>
          {onRemove !== undefined && (
            <button
              className={styles.remove}
              type="button"
              title="不再引用这个附件"
              onClick={() => onRemove(item.source_uuid)}
            >
              ×
            </button>
          )}
        </li>
      ))}
    </ul>
  )
}
