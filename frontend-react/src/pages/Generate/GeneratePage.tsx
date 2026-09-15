import { useEffect, useRef, useState } from 'react'
import type { SubmitEvent } from 'react'

import { createGeneration, getGeneration } from '@/api/generation_api'
import { useOpenPreview } from '@/hooks/useOpenPreview'
import { ApiError } from '@/api/http'
import type { GenType, GenerateAccepted, GenerationTask } from '@/types/generation_types'

/**
 * 前端校验：与后端 generation_schemas.py 的 GenerateRequest 约束一致（2~2000 字）。
 * 返回 null 表示通过，否则返回要展示给用户的提示。
 */
function validate(prompt: string): string | null {
  if (prompt.length < 2) return '需求描述至少 2 个字'
  if (prompt.length > 2000) return '需求描述最多 2000 个字'
  return null
}

/**
 * 把异常转成给用户看的文案。
 * 403 单独处理：FastAPI 的 HTTPBearer 在"请求头没带 token"时返回的是 403（不是 401），
 * 文案是英文的 Not authenticated，直接展示很突兀；这里换成中文引导。
 */
function toErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return '登录状态已失效，请重新登录后再试'
    // 后端失败时 detail 形如"任务队列不可用，请稍后重试"，本身就是给用户看的中文
    return err.message
  }
  return '生成失败，请检查网络后重试'
}

/**
 * 轮询的总时长上限。
 * 后端有 15 分钟的任务超时（AGENT_JOB_TIMEOUT_SECONDS）与僵尸回收兜底，
 * 前端这里再放一个稍宽的上限，避免用户在页面上无限等待。
 */
const POLL_TIMEOUT_MS = 20 * 60 * 1000

/** 简单的 sleep（轮询间隔用） */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * 生成页：输入需求 → 提交（后端 202 立即返回）→ 轮询进度 → 展示结果。
 *
 * 为什么改成轮询：后端已改为异步执行（arq worker 独立进程），
 * 提交接口不再阻塞到生成结束。一次复杂生成可能要几分钟，
 * 所以全程要向用户展示"走到哪一步了"，而不是一个静止的"请稍候"。
 */
export default function GeneratePage() {
  const [prompt, setPrompt] = useState('')
  const [genType, setGenType] = useState<GenType>('single')

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** 提交成功后立刻拿到的受理信息（此时任务刚排队，还没有产物） */
  const [accepted, setAccepted] = useState<GenerateAccepted | null>(null)
  /** 轮询到的最新任务详情 */
  const [task, setTask] = useState<GenerationTask | null>(null)
  /** 已等待秒数（长任务里"它还在动"是最重要的反馈） */
  const [elapsedSec, setElapsedSec] = useState(0)
  const { openPreview, openingUuid, error: previewError } = useOpenPreview()

  // 卸载后要停止轮询：否则离开页面后请求还在继续，回到本页时 setState 会作用在已卸载组件上
  const cancelledRef = useRef(false)
  useEffect(() => {
    return () => {
      cancelledRef.current = true
    }
  }, [])

  // 轮询期间每秒刷新"已等待 N 秒"
  useEffect(() => {
    if (!submitting) return undefined
    const timer = setInterval(() => setElapsedSec((sec) => sec + 1), 1000)
    return () => clearInterval(timer)
  }, [submitting])

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setError(null)
    // 先清掉上一次的结果：否则新请求失败时，屏幕上还留着旧的成功结果，容易误解
    setTask(null)
    setAccepted(null)

    const trimmedPrompt = prompt.trim()

    // 先做前端校验：不合格直接提示，不发请求
    const invalidMessage = validate(trimmedPrompt)
    if (invalidMessage !== null) {
      setError(invalidMessage)
      return
    }

    cancelledRef.current = false
    setSubmitting(true)
    setElapsedSec(0)

    try {
      // 1) 提交：后端立即返回 202（不再同步等生成结果）
      const created = await createGeneration({ prompt: trimmedPrompt, gen_type: genType })
      setAccepted(created)

      // 2) 轮询进度，直到 status 不再是 running
      const deadline = Date.now() + POLL_TIMEOUT_MS
      for (;;) {
        // 先等一个间隔再查：刚提交时必然还是 queued，立刻查是白跑一次请求
        await delay(created.poll_interval_ms)
        if (cancelledRef.current) return

        const latest = await getGeneration(created.task_uuid)
        if (cancelledRef.current) return
        setTask(latest)

        if (latest.status !== 'running') {
          // 失败原因后端已经写进 error_msg，直接展示
          if (latest.status === 'failed') setError(latest.error_msg ?? '生成失败，请重试')
          return
        }
        if (Date.now() > deadline) {
          setError('等待超时。任务可能仍在后台执行，可稍后到「我的项目」查看最终状态')
          return
        }
      }
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      // 无论成功失败都要恢复按钮，否则失败一次后按钮永久禁用
      setSubmitting(false)
    }
  }

  // 进度区展示的数据：优先用轮询到的最新阶段，退而用受理响应里的初始阶段
  const progressStage = task?.stage_text ?? accepted?.stage_text ?? '正在提交'
  const progressValue = task?.progress ?? accepted?.progress ?? 0
  const progressDetail = task?.stage_detail ?? null

  return (
    <section className="page">
      <h1>生成应用</h1>
      <p className="page-desc">描述你想要的网页，AI 会生成可直接打开的完整页面。</p>

      <form className="gen-form" onSubmit={handleSubmit}>
        <label className="gen-field">
          <span className="gen-label">需求描述</span>
          <textarea
            className="gen-textarea"
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="例如：做一个待办清单页面，可以添加、勾选完成和删除，数据存在 localStorage，风格简洁"
            rows={5}
            maxLength={2000}
            disabled={submitting}
          />
          {/* 计数器用原值（用户所见），校验用 trim 后的值（防止全空格通过） */}
          <span className="gen-counter">{prompt.length} / 2000</span>
        </label>

        {/* fieldset disabled：一次性禁用内部所有表单控件 */}
        <fieldset className="gen-fieldset" disabled={submitting}>
          <legend className="gen-label">生成类型</legend>

          <label className="gen-radio">
            <input
              type="radio"
              name="genType"
              value="single"
              checked={genType === 'single'}
              onChange={() => setGenType('single')}
            />
            <span>单个 HTML 文件（样式与脚本内联，出图快）</span>
          </label>

          <label className="gen-radio">
            <input
              type="radio"
              name="genType"
              value="multi"
              checked={genType === 'multi'}
              onChange={() => setGenType('multi')}
            />
            <span>多文件（index.html + style.css + script.js）</span>
          </label>
        </fieldset>

        {error !== null && <p className="gen-error">{error}</p>}

        <button className="gen-button" type="submit" disabled={submitting}>
          {submitting ? '正在生成，请稍候…' : '开始生成'}
        </button>
      </form>

      {submitting && (
        <div className="gen-progress">
          <div className="gen-progress-head">
            <span className="gen-progress-stage">{progressStage}</span>
            <span className="gen-progress-elapsed">
              已等待 {elapsedSec} 秒
              {accepted !== null && ` · 任务 ${accepted.task_uuid.slice(0, 8)}`}
            </span>
          </div>

          <div
            className="gen-progress-track"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progressValue}
            aria-label="生成进度"
          >
            <div className="gen-progress-fill" style={{ width: `${progressValue}%` }} />
          </div>

          {progressDetail !== null && <p className="gen-progress-detail">{progressDetail}</p>}

          <p className="gen-hint">
            生成在后台独立进程执行，可以离开本页；任务与产物会保存在「我的项目」里。
          </p>
        </div>
      )}

      {!submitting && task !== null && task.status === 'success' && (
        <div className="gen-result">
          <h2 className="gen-result-title">生成成功</h2>
          <ul className="gen-result-list">
            <li>任务 ID：{task.task_uuid}</li>
            <li>类型：{task.gen_type}</li>
            <li>文件：{task.file_list.join('、') || '（无）'}</li>
            <li>
              耗时：
              {task.duration_ms !== null ? `${(task.duration_ms / 1000).toFixed(1)} 秒` : '未知'}
            </li>
            <li>
              token 用量：输入 {task.input_tokens ?? 0} / 输出 {task.output_tokens ?? 0}
              （其中思考 {task.reasoning_tokens ?? 0}）
            </li>
          </ul>

          <button
            className="gen-button gen-button-inline"
            type="button"
            onClick={() => void openPreview(task.task_uuid)}
            disabled={openingUuid === task.task_uuid}
          >
            {openingUuid === task.task_uuid ? '正在打开…' : '打开预览'}
          </button>

          {previewError !== null && <p className="gen-error">{previewError}</p>}
        </div>
      )}
    </section>
  )
}
