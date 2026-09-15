import { useState } from 'react'

import Button from '@/components/common/Button'
import { useGenerationRunner } from '@/hooks/useGenerationRunner'
import GenerationProgress from './GenerationProgress'
import { PROMPT_MAX, validatePrompt } from './generate_utils'
import styles from './QuickGenerateForm.module.css'

/**
 * 「极速生成（单页）」：一句话直接出单个 HTML 文件。
 *
 * 为什么保留这条入口（阶段 7 数据支持）：`single` 模式的输入 token 只有 agent 的 1/76
 * （对照实验：510 vs 39055），对"明确就是一页"的需求更快更省。
 * 但它对多页需求只能把一切塞进一个文件，所以默认入口仍是会话式 agent 生成。
 *
 * 用原生 `<details>` 做折叠：不引入额外状态，键盘与读屏器行为由浏览器保证。
 * 这里**不提供 multi**：`multi` 已退役（阶段 7），界面上不该再出现它。
 */
export default function QuickGenerateForm() {
  const [prompt, setPrompt] = useState('')
  const [localError, setLocalError] = useState<string | null>(null)
  const runner = useGenerationRunner()

  const busy = runner.submitting

  function handleSubmit(): void {
    const trimmed = prompt.trim()
    const invalid = validatePrompt(trimmed)
    if (invalid !== null) {
      setLocalError(invalid)
      return
    }
    setLocalError(null)
    void runner.run({ prompt: trimmed, gen_type: 'single' })
  }

  return (
    <details className={styles.wrapper}>
      <summary className={styles.summary}>极速生成（单个 HTML 文件）</summary>

      <div className={styles.body}>
        <p className={styles.desc}>
          需求已经想清楚、并且**只需要一页**时用这条：一次调用出一个自带样式与脚本的
          HTML 文件，比 Agent 流水线快也更省 token。需要多页面 / 多文件时请用上面的对话式生成。
        </p>

        <label>
          <textarea
            className={styles.textarea}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="例如：做一个极简的待办清单页面，可以添加、勾选完成和删除条目"
            rows={4}
            maxLength={PROMPT_MAX}
            disabled={busy}
          />
          {/* 计数器显示原值（用户所见），校验用 trim 后的值（防止全空格通过） */}
          <span className={styles.counter}>
            {prompt.length} / {PROMPT_MAX}
          </span>
        </label>

        <div className={styles.submitRow}>
          <Button disabled={busy} onClick={handleSubmit}>
            {busy ? '正在生成…' : '开始生成'}
          </Button>
        </div>

        {localError !== null && <p className={styles.error}>{localError}</p>}

        <GenerationProgress
          accepted={runner.accepted}
          task={runner.task}
          elapsedSec={runner.elapsedSec}
          clarifying={runner.clarifying}
          error={runner.error}
          running={runner.submitting}
          typeLabel="单页极速"
        />
      </div>
    </details>
  )
}
