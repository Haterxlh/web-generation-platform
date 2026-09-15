import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, KeyboardEvent } from 'react'

import Button from '@/components/common/Button'
import AttachmentChips from './AttachmentChips'
import { ACCEPT_UPLOAD, PROMPT_MAX, type ChatAttachment, type ChatMessage } from './generate_utils'
import styles from './ChatPanel.module.css'

interface ChatPanelProps {
  messages: ChatMessage[]
  /** 正在发送/等待回复 */
  sending: boolean
  /** 正在上传或解析附件 */
  uploading: boolean
  /** 生成中或恢复会话中：输入与上传一并禁用 */
  busy: boolean
  /** 本轮已经挂上、还没随消息发出去的附件 */
  pending: ChatAttachment[]
  /** 是否已有会话（没有会话就不能上传：附件别名的作用域是会话） */
  canUpload: boolean
  /** 恢复历史会话中 */
  restoring: boolean
  error: string | null
  onSend: (text: string) => void
  onPickFile: (file: File) => void
  onRemovePending: (sourceUuid: string) => void
  onNewSession: () => void
}

/**
 * 聊天区：消息列表 + 待发送附件 + 输入框。
 *
 * 输入框的草稿状态留在这里（不外提）：它只服务于这个输入框，
 * 提到页面级反而会让"发送后清空"这种小事跨组件传递。
 */
export default function ChatPanel({
  messages,
  sending,
  uploading,
  busy,
  pending,
  canUpload,
  restoring,
  error,
  onSend,
  onPickFile,
  onRemovePending,
  onNewSession,
}: ChatPanelProps) {
  const [draft, setDraft] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // 新消息进来时滚到底部：否则用户看不到助手的回复，以为没反应
  useEffect(() => {
    const node = listRef.current
    if (node !== null) node.scrollTop = node.scrollHeight
  }, [messages, sending])

  const inputDisabled = busy || sending || restoring

  function submit(): void {
    const text = draft.trim()
    // 与后端 AgentChatRequest 一致：**不允许只传附件不说话**
    if (text === '' || inputDisabled) return
    onSend(text)
    setDraft('')
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    // Enter 发送、Shift+Enter 换行：聊天的通用习惯，且不必额外解释
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>): void {
    const file = event.target.files?.[0]
    // 先清空 input 的值：否则连续选同一个文件不会再触发 change
    event.target.value = ''
    if (file !== undefined) onPickFile(file)
  }

  const uploadDisabled = !canUpload || inputDisabled || uploading

  return (
    <section className={styles.panel}>
      <div className={styles.head}>
        <h2 className={styles.headTitle}>和 Agent 聊需求</h2>
        <div className={styles.headMeta}>
          {restoring && '正在恢复上次的会话…'}
          {!restoring && messages.length === 0 && '说清"做什么"和"有哪些功能"就能开始生成'}
          {!restoring && messages.length > 0 && `共 ${messages.length} 条消息`}
          {messages.length > 0 && (
            <>
              {' · '}
              <button className={styles.linkButton} type="button" onClick={onNewSession}>
                开始新会话
              </button>
            </>
          )}
        </div>
      </div>

      <div className={styles.messages} ref={listRef}>
        {restoring && <p className={styles.empty}>正在恢复上次的会话…</p>}

        {!restoring && messages.length === 0 && (
          <p className={styles.empty}>
            例如：「做一个单页待办清单，能添加、勾选完成和删除，数据存在浏览器里」
            <br />
            信息不够时 Agent 会追问，补齐后就能开始生成。
          </p>
        )}

        {messages.map((message) => (
          <div
            key={message.id}
            className={`${styles.row} ${message.role === 'user' ? styles.rowUser : ''}`.trim()}
          >
            <div
              className={[
                styles.bubble,
                message.role === 'user' ? styles.bubbleUser : styles.bubbleAssistant,
                message.variant === 'notice' ? styles.bubbleNotice : '',
              ]
                .filter(Boolean)
                .join(' ')}
            >
              {message.attachments.length > 0 && (
                <div className={styles.bubbleAttachments}>
                  <AttachmentChips items={message.attachments} />
                </div>
              )}
              {message.content}
            </div>
          </div>
        ))}

        {sending && (
          <div className={styles.row}>
            <div className={`${styles.bubble} ${styles.bubbleAssistant}`}>正在思考…</div>
          </div>
        )}
      </div>

      <div className={styles.composer}>
        {pending.length > 0 && (
          <div className={styles.pending}>
            <AttachmentChips items={pending} onRemove={onRemovePending} />
          </div>
        )}

        <div className={styles.composerRow}>
          <textarea
            className={styles.textarea}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="描述你想要的页面；也可以先发一句建立会话，再上传需求文档 / 设计规范"
            rows={2}
            maxLength={PROMPT_MAX}
            disabled={inputDisabled}
          />

          <Button
            type="button"
            disabled={inputDisabled || draft.trim() === ''}
            onClick={submit}
          >
            {sending ? '发送中…' : '发送'}
          </Button>
        </div>

        <div className={styles.actions}>
          <label
            className={`${styles.fileLabel} ${uploadDisabled ? styles.fileLabelDisabled : ''}`.trim()}
            title={canUpload ? '添加附件' : '先发送一条消息创建会话，然后就可以挂附件了'}
          >
            {uploading ? '解析中…' : '添加附件'}
            <input
              ref={fileRef}
              className={styles.fileInput}
              type="file"
              accept={ACCEPT_UPLOAD}
              onChange={handleFileChange}
              disabled={uploadDisabled}
            />
          </label>

          <span className={styles.hint}>
            {canUpload
              ? '支持 .pdf / .html / .md / .txt（≤10MB）；上传后会先理解成需求摘要，再随消息引用'
              : '先发送一条消息创建会话，之后就可以挂附件了'}
          </span>
        </div>

        {error !== null && <p className={styles.error}>{error}</p>}
      </div>
    </section>
  )
}
