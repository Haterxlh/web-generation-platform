import { useEffect, useState } from 'react'

import { chatWithAgent, getAgentSession, uploadSource } from '@/api/agent_api'
import { useGenerationRunner } from '@/hooks/useGenerationRunner'
import { clearAgentSessionUuid, getAgentSessionUuid, setAgentSessionUuid } from '@/utils/agent_session'
import ChatPanel from './ChatPanel'
import GenerationProgress from './GenerationProgress'
import QuickGenerateForm from './QuickGenerateForm'
import RequirementCard from './RequirementCard'
import {
  buildGenerationPrompt,
  isReadyToGenerate,
  lastUserText,
  missingRequiredSlots,
  newMessageId,
  sessionDetailToMessages,
  toErrorMessage,
  validateUpload,
  type ChatAttachment,
  type ChatMessage,
} from './generate_utils'
import type { RequirementSlots } from '@/types/agent_types'
import styles from './GeneratePage.module.css'

/**
 * 生成页（会话式）：
 *   聊天澄清需求 → 挂附件（需求文档 / 设计规范）→ 需求确认 → 生成（202 + 轮询）→ 预览
 *
 * 两条入口：
 *   - 默认：Agent 流水线（阶段 7 对照实验后定为默认路径 —— 唯一能交付多页产物的实现）
 *   - 折叠的「极速生成（单页）」：明确只要一页时更快更省
 *   `multi` 已退役，界面上不再出现（见 docs/agent_refactor_plan.md 阶段 7）。
 */
export default function GeneratePage() {
  /** 当前会话 uuid；null 表示还没建会话（此时不能上传附件） */
  const [sessionUuid, setSessionUuid] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  /** 本轮已挂上、还没随消息发出去的附件 */
  const [pending, setPending] = useState<ChatAttachment[]>([])
  const [slots, setSlots] = useState<RequirementSlots | null>(null)
  const [ready, setReady] = useState(false)
  const [missingSlots, setMissingSlots] = useState<string[]>([])

  // 初值直接由"本地有没有会话 uuid"推导，而不是一律先 true 再在 effect 里同步改回 false：
  // 那次同步 setState 既多余、又会触发级联渲染（react-hooks/set-state-in-effect），
  // 还会让首次进入的用户在第一帧看到"正在恢复上次的会话…"的闪烁。
  const [restoring, setRestoring] = useState(() => getAgentSessionUuid() !== null)
  const [sending, setSending] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)

  const runner = useGenerationRunner()
  const busy = runner.submitting

  // 刷新后回放上次的会话：后端没有"会话列表"接口，本地存着 session_uuid 就够了。
  // ⚠️ setState 全部放在 Promise 回调里（react-hooks/set-state-in-effect 不接受 effect 体内同步 setState）；
  // "没有会话"的情形在 useState 初值里就处理掉了，这里直接返回。
  useEffect(() => {
    const saved = getAgentSessionUuid()
    if (saved === null) return undefined

    let cancelled = false
    getAgentSession(saved)
      .then((detail) => {
        if (cancelled) return
        setSessionUuid(saved)
        setMessages(sessionDetailToMessages(detail))
        setSlots(detail.slots)
        setReady(isReadyToGenerate(detail.slots))
      })
      .catch(() => {
        // 会话被删 / 不属于当前账号（换账号登录后必然如此）：丢掉本地记录，从头开始
        if (!cancelled) clearAgentSessionUuid()
      })
      .finally(() => {
        if (!cancelled) setRestoring(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  /** 发送一轮对话 */
  async function handleSend(text: string): Promise<void> {
    if (sending || busy) return
    setChatError(null)

    const attached = pending
    // 消息正文里带上别名原文：历史回放时能看到"这条消息引用了哪个文件"，
    // 与后端"消息只存别名、不存文件内容"的设计一致
    const aliasPrefix = attached.map((item) => item.alias).join(' ')
    const content = aliasPrefix === '' ? text : `${aliasPrefix} ${text}`

    // 乐观更新：先把用户这句话放上屏幕，再发请求（等待 3~8 秒时界面不该像没反应）
    const localId = newMessageId()
    setMessages((prev) => [
      ...prev,
      { id: localId, role: 'user', content, attachments: attached, variant: 'normal' },
    ])
    setPending([])
    setSending(true)

    try {
      const response = await chatWithAgent({
        session_uuid: sessionUuid,
        message: content,
        attachments: attached.map((item) => ({ source_uuid: item.source_uuid, role: item.role })),
      })

      // 首次对话后端才给出 session_uuid：立刻落本地，刷新后还能回来
      if (sessionUuid === null) {
        setSessionUuid(response.session_uuid)
        setAgentSessionUuid(response.session_uuid)
      }

      setMessages((prev) => [
        ...prev,
        {
          id: response.message_uuid,
          role: 'assistant',
          content: response.reply,
          attachments: [],
          variant: 'normal',
        },
      ])
      setSlots(response.slots)
      setMissingSlots(response.missing_slots)
      setReady(response.ready_to_generate)
    } catch (err) {
      setChatError(toErrorMessage(err, '发送失败，请稍后重试'))
      // 失败时把附件放回待发送区：不让用户为了同一次引用重新上传一遍
      setPending(attached)
    } finally {
      setSending(false)
    }
  }

  /** 选择并上传一个附件 */
  async function handlePickFile(file: File): Promise<void> {
    if (sessionUuid === null) {
      setChatError('先发送一条消息建立会话，然后就可以挂附件了')
      return
    }

    const invalid = validateUpload(file)
    if (invalid !== null) {
      setChatError(invalid)
      return
    }

    setChatError(null)
    setUploading(true)
    try {
      const result = await uploadSource(sessionUuid, file)
      setPending((prev) => [
        ...prev,
        {
          source_uuid: result.source_uuid,
          alias: result.alias,
          display_name: result.display_name,
          role: result.role,
          parse_status: result.parse_status,
          parse_error: result.parse_error,
        },
      ])

      // 解析失败不是"上传失败"：文件与别名都在。必须说出来，否则用户以为它生效了
      if (result.parse_status === 'failed') {
        setMessages((prev) => [
          ...prev,
          {
            id: newMessageId(),
            role: 'assistant',
            content: `附件 ${result.alias} 没能解析出内容：${
              result.parse_error ?? '文件读不出来'
            }。它会保留在会话里，但你最好换一个可复制文本的文件（扫描版 PDF 需要先 OCR）。`,
            attachments: [],
            variant: 'notice',
          },
        ])
      }
    } catch (err) {
      setChatError(toErrorMessage(err, '上传失败，请稍后重试'))
    } finally {
      setUploading(false)
    }
  }

  /** 开始生成：把确认卡片上的需求交给 Agent 流水线 */
  function handleStart(): void {
    if (sessionUuid === null) return
    // 只用槽位（用户刚确认过的那份需求）；用户原话仅在槽位拼不出东西时兜底
    const prompt = buildGenerationPrompt(slots, lastUserText(messages))
    void runner.run({ prompt, gen_type: 'agent', session_uuid: sessionUuid })
  }

  /** 开始新会话：清本地记录与全部页面状态（旧会话仍留在后端，可被历史记录引用） */
  function handleNewSession(): void {
    clearAgentSessionUuid()
    setSessionUuid(null)
    setMessages([])
    setPending([])
    setSlots(null)
    setReady(false)
    setMissingSlots([])
    setChatError(null)
    runner.reset()
  }

  return (
    <section className={`page ${styles.section}`}>
      <h1>生成应用</h1>
      <p className="page-desc">
        和 Agent 聊清楚需求（可以上传需求文档或设计规范），确认无误后一键生成可直接打开的网页。
      </p>

      <ChatPanel
        messages={messages}
        sending={sending}
        uploading={uploading}
        busy={busy}
        pending={pending}
        canUpload={sessionUuid !== null}
        restoring={restoring}
        error={chatError}
        onSend={(text) => void handleSend(text)}
        onPickFile={(file) => void handlePickFile(file)}
        onRemovePending={(sourceUuid) =>
          setPending((prev) => prev.filter((item) => item.source_uuid !== sourceUuid))
        }
        onNewSession={handleNewSession}
      />

      {(slots !== null || messages.length > 0) && (
        <RequirementCard
          slots={slots}
          ready={ready}
          // 后端返回的 missing_slots 优先；刷新回放时拿不到它，就按同一口径自己算
          missingSlots={missingSlots.length > 0 ? missingSlots : missingRequiredSlots(slots)}
          busy={busy}
          onStart={handleStart}
        />
      )}

      <GenerationProgress
        accepted={runner.accepted}
        task={runner.task}
        elapsedSec={runner.elapsedSec}
        clarifying={runner.clarifying}
        error={runner.error}
        running={runner.submitting}
        typeLabel="Agent 生成"
      />

      <QuickGenerateForm />
    </section>
  )
}
