import { useCallback, useEffect, useRef, useState } from 'react'

import { createGeneration, getGeneration } from '@/api/generation_api'
import { clearRunningTask, getRunningTask, setRunningTask } from '@/utils/generation_task'
import type { GenerateAccepted, GenerateRequest, GenerationTask } from '@/types/generation_types'
import { POLL_TIMEOUT_MS, toErrorMessage } from '@/pages/Generate/generate_utils'

/** 一次生成从提交到终态的完整状态（极速表单与会话式生成共用） */
export interface GenerationRunnerState {
  /** 提交中或轮询中（按钮禁用、进度区展示都看它） */
  submitting: boolean
  /** 提交成功后立刻拿到的受理信息（此时任务刚排队，还没有产物） */
  accepted: GenerateAccepted | null
  /** 轮询到的最新任务详情 */
  task: GenerationTask | null
  /** 已等待秒数（长任务里"它还在动"是最重要的反馈） */
  elapsedSec: number
  /** 面向用户的错误文案 */
  error: string | null
  /**
   * 非 null 表示后端停在 `clarifying`（**等用户补充信息**，不是失败）：
   * 内容是后端在 `stage_detail` 里写好的追问。此时已停止轮询。
   */
  clarifying: string | null
}

/** 简单的 sleep（轮询间隔用） */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

const INITIAL: GenerationRunnerState = {
  submitting: false,
  accepted: null,
  task: null,
  elapsedSec: 0,
  error: null,
  clarifying: null,
}

/** 恢复时用的受理信息：字段与 `GenerateAccepted` 对齐，让进度区照常渲染 */
function acceptedFromRunning(taskUuid: string, intervalMs: number): GenerateAccepted {
  return {
    task_uuid: taskUuid,
    status: 'running',
    stage: 'queued',
    stage_text: '正在恢复进度…',
    progress: 0,
    poll_url: `/api/generation/${taskUuid}`,
    poll_interval_ms: intervalMs,
  }
}

/**
 * 生成任务运行器：提交（后端 202 立即返回）→ 轮询阶段 → 终态，**并支持切页/刷新后恢复**。
 *
 * 抽成 hook 的理由：生成页有两个入口（会话式生成、极速单页生成），
 * "提交 + 轮询 + 已等待计时 + clarifying 识别"完全一样，各写一份必然漏改一处。
 *
 * ⚠️ 四个容易踩的点：
 * 1. **卸载后必须停止轮询**（`mountedRef`），否则离开页面后请求还在继续；
 * 2. **`stage='clarifying'` 要停止轮询**：它是"等用户补充"的暂停态，`status` 仍是 running，
 *    照原样轮询会一直转到 20 分钟上限，用户看到的却只是"排队中"；
 * 3. **"开始新会话"必须真的掐断在跑的循环**：所以用 `epochRef` 而不是布尔标志 ——
 *    布尔标志在"同步置 true 再置 false"时，正挂在 `await` 上的循环根本观察不到，
 *    会继续往已经被清空的界面上写状态；
 * 4. **进行中的任务要落 localStorage**：生成跑在后端 worker 里，
 *    切到「我的项目」再回来不该丢进度、更不该让"开始生成"重新可点（会重复提交一个任务）。
 *    真源仍在 MySQL —— 本地只存 task_uuid，恢复时回后端查。
 */
export function useGenerationRunner(): GenerationRunnerState & {
  run: (req: GenerateRequest) => Promise<void>
  reset: () => void
} {
  const [state, setState] = useState<GenerationRunnerState>(INITIAL)
  /** 每开始一轮（提交 / 恢复 / 重置）+1；旧循环凭 epoch 判定自己是否已过期 */
  const epochRef = useRef(0)
  /** 组件是否还挂着（StrictMode 下会在 cleanup 后重新挂载，故 effect 体内重置为 true） */
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      epochRef.current += 1
    }
  }, [])

  const isAlive = useCallback(
    (epoch: number): boolean => mountedRef.current && epochRef.current === epoch,
    [],
  )

  // 轮询期间每秒刷新"已等待 N 秒"（setState 在定时器回调里，不违反 set-state-in-effect）
  useEffect(() => {
    if (!state.submitting) return undefined
    const timer = setInterval(() => {
      setState((prev) => ({ ...prev, elapsedSec: prev.elapsedSec + 1 }))
    }, 1000)
    return () => clearInterval(timer)
  }, [state.submitting])

  /** 轮询到终态；每一步都先确认自己没过期 */
  const pollUntilDone = useCallback(
    async (taskUuid: string, intervalMs: number, epoch: number): Promise<void> => {
      const deadline = Date.now() + POLL_TIMEOUT_MS
      for (;;) {
        // 先等一个间隔再查：刚提交时必然还是 queued，立刻查是白跑一次请求
        await delay(intervalMs)
        if (!isAlive(epoch)) return

        const latest = await getGeneration(taskUuid)
        if (!isAlive(epoch)) return
        setState((prev) => ({ ...prev, task: latest }))

        // 暂停等人：不是失败，把后端写好的追问原样交给界面
        if (latest.stage === 'clarifying') {
          clearRunningTask()
          setState((prev) => ({
            ...prev,
            clarifying: latest.stage_detail ?? '还需要补充一些需求信息',
          }))
          return
        }

        if (latest.status !== 'running') {
          clearRunningTask()
          // 失败原因后端已经写进 error_msg，直接展示
          if (latest.status === 'failed') {
            setState((prev) => ({ ...prev, error: latest.error_msg ?? '生成失败，请重试' }))
          }
          return
        }

        if (Date.now() > deadline) {
          clearRunningTask()
          setState((prev) => ({
            ...prev,
            error: '等待超时。任务可能仍在后台执行，可稍后到「我的项目」查看最终状态',
          }))
          return
        }
      }
    },
    [isAlive],
  )

  const reset = useCallback((): void => {
    // 让在跑的循环立刻过期：只清 state 是不够的，它下一次 await 醒来还会写回来
    epochRef.current += 1
    clearRunningTask()
    setState(INITIAL)
  }, [])

  const run = useCallback(
    async (req: GenerateRequest): Promise<void> => {
      epochRef.current += 1
      const epoch = epochRef.current
      // 先清掉上一次的结果与错误：否则新请求失败时屏幕上还留着旧的成功结果，容易误解
      setState({ ...INITIAL, submitting: true })

      try {
        // 1) 提交：后端立即返回 202，真正的生成由 worker 进程执行
        const created = await createGeneration(req)
        if (!isAlive(epoch)) return
        // 立刻落本地：这一刻起到终态为止，切页/刷新都能恢复进度
        setRunningTask({
          task_uuid: created.task_uuid,
          poll_interval_ms: created.poll_interval_ms,
          prompt: req.prompt,
          started_at: Date.now(),
        })
        setState((prev) => ({ ...prev, accepted: created }))

        // 2) 轮询到终态
        await pollUntilDone(created.task_uuid, created.poll_interval_ms, epoch)
      } catch (err) {
        if (isAlive(epoch)) {
          setState((prev) => ({
            ...prev,
            error: toErrorMessage(err, '生成失败，请检查网络后重试'),
          }))
        }
      } finally {
        // 无论成功失败都要恢复按钮，否则失败一次后按钮永久禁用
        if (isAlive(epoch)) setState((prev) => ({ ...prev, submitting: false }))
      }
    },
    [isAlive, pollUntilDone],
  )

  /**
   * 恢复上一次未结束的任务（切页 / 刷新回来后）。
   *
   * 用 setTimeout 落地状态是刻意的：① `react-hooks/set-state-in-effect` 不接受
   * effect 体内同步 setState；② 等一个轮询间隔再展示，与"继续轮询"的语义一致 ——
   * 若任务其实已经结束，这一次查询会直接把结果卡片渲染出来。
   */
  useEffect(() => {
    const saved = getRunningTask()
    if (saved === null) return undefined

    epochRef.current += 1
    const epoch = epochRef.current

    const timer = setTimeout(() => {
      if (!isAlive(epoch)) return
      setState({
        ...INITIAL,
        submitting: true,
        // 已等待秒数按"提交时刻"续算，切页回来不会归零
        elapsedSec: Math.max(0, Math.floor((Date.now() - saved.started_at) / 1000)),
        accepted: acceptedFromRunning(saved.task_uuid, saved.poll_interval_ms),
      })
      void pollUntilDone(saved.task_uuid, saved.poll_interval_ms, epoch).finally(() => {
        if (isAlive(epoch)) setState((prev) => ({ ...prev, submitting: false }))
      })
    }, saved.poll_interval_ms)

    return () => clearTimeout(timer)
  }, [isAlive, pollUntilDone])

  return { ...state, run, reset }
}
