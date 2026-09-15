import Button from '@/components/common/Button'
import { useOpenPreview } from '@/hooks/useOpenPreview'
import type { GenerateAccepted, GenerationTask } from '@/types/generation_types'
import { formatElapsed } from './generate_utils'
import styles from './GenerationProgress.module.css'

interface GenerationProgressProps {
  /** 提交后立刻拿到的受理信息 */
  accepted: GenerateAccepted | null
  /** 轮询到的最新任务详情 */
  task: GenerationTask | null
  /** 已等待秒数 */
  elapsedSec: number
  /** 非 null 表示后端停在 clarifying（等用户补充信息），**不是失败** */
  clarifying: string | null
  /** 面向用户的错误文案 */
  error: string | null
  /** 是否仍在提交/轮询中 */
  running: boolean
  /** 这次生成用的是什么模式（会话式与极速表单的文案不同） */
  typeLabel: string
}

/**
 * 生成过程的三态展示：进行中 / 暂停等人 / 失败 / 成功结果。
 *
 * ⚠️ 关键区分（阶段 7 给阶段 8 定的硬要求）：
 * `clarifying` 是**暂停等人**，不是失败 —— 用警示色 + "需要补充信息"的措辞，
 * 并明确告诉用户去哪里补（下面的对话区）。把它渲染成红色"生成失败"，
 * 用户会去重试一个其实在等他的任务。
 */
export default function GenerationProgress({
  accepted,
  task,
  elapsedSec,
  clarifying,
  error,
  running,
  typeLabel,
}: GenerationProgressProps) {
  const { openPreview, openingUuid, error: previewError } = useOpenPreview()

  // 进度区展示的数据：优先用轮询到的最新阶段，退而用受理响应里的初始阶段
  const stageText = task?.stage_text ?? accepted?.stage_text ?? '正在提交'
  const progressValue = task?.progress ?? accepted?.progress ?? 0
  const detail = task?.stage_detail ?? null

  if (clarifying !== null) {
    return (
      <section className={styles.clarify}>
        <h2 className={styles.clarifyTitle}>还差一点信息，生成已暂停</h2>
        <p className={styles.clarifyText}>
          {clarifying}
          <br />
          请在下面的对话区补充说明，然后重新点「开始生成」——
          这不是失败，任务停在原地等你。
        </p>
      </section>
    )
  }

  if (error !== null) {
    return (
      <section className={styles.error}>
        <h2 className={styles.errorTitle}>生成失败</h2>
        <p className={styles.errorText}>{error}</p>
      </section>
    )
  }

  if (running) {
    return (
      <section className={styles.progress}>
        <div className={styles.head}>
          <span className={styles.stage}>
            {typeLabel} · {stageText}
          </span>
          <span className={styles.elapsed}>
            已等待 {formatElapsed(elapsedSec)}
            {accepted !== null && ` · 任务 ${accepted.task_uuid.slice(0, 8)}`}
          </span>
        </div>

        <div
          className={styles.track}
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progressValue}
          aria-label="生成进度"
        >
          <div className={styles.fill} style={{ width: `${progressValue}%` }} />
        </div>

        {detail !== null && <p className={styles.detail}>{detail}</p>}

        <p className={styles.hint}>
          生成在后台独立进程执行，可以离开本页；任务与产物会保存在「我的项目」里。
        </p>
      </section>
    )
  }

  if (task === null || task.status !== 'success') return null

  return (
    <section className={styles.result}>
      <h2 className={styles.resultTitle}>生成成功</h2>
      <ul className={styles.resultList}>
        <li>任务 ID：{task.task_uuid}</li>
        <li>模式：{typeLabel}</li>
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

      <div className={styles.actions}>
        <Button
          disabled={openingUuid === task.task_uuid}
          onClick={() => void openPreview(task.task_uuid)}
        >
          {openingUuid === task.task_uuid ? '正在打开…' : '打开预览'}
        </Button>
        <span className={styles.note}>
          预览票据绑定你的账号且 30 分钟过期，链接不能分享给别人。
        </span>
      </div>

      {previewError !== null && <p className={styles.errorText}>{previewError}</p>}
    </section>
  )
}
