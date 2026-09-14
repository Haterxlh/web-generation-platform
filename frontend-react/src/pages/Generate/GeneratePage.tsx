import { useState } from 'react'
import type { SubmitEvent } from 'react'

import { createGeneration } from '@/api/generation_api'
import { useOpenPreview } from '@/hooks/useOpenPreview'
import { ApiError } from '@/api/http'
import type { GenType, GenerationTask } from '@/types/generation_types'

/**
 * 前端校验：与后端 generation_schemas.py 的 GenerateRequest 约束一致（2~2000 字）。
 * 返回 null 表示通过，否则返回要展示给用户的提示。
 * 放在组件外：纯函数、不依赖组件状态，也便于将来单测（与 RegisterPage 同一写法）。
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
    // 后端失败时 detail 形如"生成失败：模型输出被 max_tokens 截断…"，本身就是给用户看的中文
    return err.message
  }
  return '生成失败，请检查网络后重试'
}

/** 生成页：输入需求 → 调后端同步生成 → 展示结果 */
export default function GeneratePage() {
  const [prompt, setPrompt] = useState('')
  const [genType, setGenType] = useState<GenType>('single')

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [task, setTask] = useState<GenerationTask | null>(null)
  const { openPreview, openingUuid, error: previewError } = useOpenPreview()

  async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setError(null)
    // 先清掉上一次的结果：否则新请求失败时，屏幕上还留着旧的成功结果，容易误解
    setTask(null)

    const trimmedPrompt = prompt.trim()

    // 先做前端校验：不合格直接提示，不发请求（省一次 30~120 秒的往返）
    const invalidMessage = validate(trimmedPrompt)
    if (invalidMessage !== null) {
      setError(invalidMessage)
      return
    }

    setSubmitting(true)
    try {
      const created = await createGeneration({ prompt: trimmedPrompt, gen_type: genType })
      setTask(created)
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      // 无论成功失败都要恢复按钮，否则失败一次后按钮永久禁用
      setSubmitting(false)
    }
  }

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
          {submitting ? '生成中，请稍候（约 30~120 秒）…' : '开始生成'}
        </button>
      </form>

      {submitting && (
        <p className="gen-hint">模型正在思考并写代码，这期间请不要关闭页面，也不要重复提交。</p>
      )}

      {task !== null && (
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
