/**
 * "正在进行的生成任务"的本地记录：统一维护 localStorage 的键名。
 *
 * 为什么需要它：生成在**后端 worker 里**跑，前端只是轮询者。
 * 但轮询状态此前只活在 `GeneratePage` 的组件状态里 —— 用户点「我的项目」再切回来，
 * 组件被卸载重建，进度条就消失了、按钮也重新可点（还可能重复提交一个任务）。
 *
 * 存的是"我提交过哪个任务"，不是"任务状态"：真源始终在 MySQL，
 * 恢复时拿 task_uuid 回后端查最新状态即可（Redis/本地都不是真源，这条原则前后一致）。
 */

/** localStorage 中保存"进行中的任务"的键名 */
const RUNNING_TASK_KEY = 'wgp_running_task'

/** 一个正在进行（或刚结束、尚未被页面消费）的生成任务 */
export interface RunningTask {
  /** 任务唯一标识（回后端查状态的凭据） */
  task_uuid: string
  /** 后端建议的轮询间隔（毫秒） */
  poll_interval_ms: number
  /** 提交时的需求原文（仅用于恢复后的展示，不参与生成） */
  prompt: string
  /** 提交时刻（`Date.now()`）：恢复"已等待 N 秒"用，避免切页回来计时归零 */
  started_at: number
}

/** 读取进行中的任务；没有或数据损坏时返回 null */
export function getRunningTask(): RunningTask | null {
  try {
    const raw = localStorage.getItem(RUNNING_TASK_KEY)
    if (raw === null) return null
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null) return null
    const item = parsed as Partial<RunningTask>
    if (typeof item.task_uuid !== 'string' || item.task_uuid === '') return null
    return {
      task_uuid: item.task_uuid,
      poll_interval_ms:
        typeof item.poll_interval_ms === 'number' && item.poll_interval_ms > 0
          ? item.poll_interval_ms
          : 1500,
      prompt: typeof item.prompt === 'string' ? item.prompt : '',
      started_at: typeof item.started_at === 'number' ? item.started_at : Date.now(),
    }
  } catch {
    // 隐私模式 / 脏数据：读不到就当没有进行中的任务，不影响正常使用
    return null
  }
}

/** 记下"我提交了这个任务"（提交成功后立刻调用） */
export function setRunningTask(task: RunningTask): void {
  try {
    localStorage.setItem(RUNNING_TASK_KEY, JSON.stringify(task))
  } catch {
    // 存不进去只会导致切页后丢进度，不该让提交本身失败
  }
}

/** 任务到达终态（或用户主动重开）后清掉记录 */
export function clearRunningTask(): void {
  try {
    localStorage.removeItem(RUNNING_TASK_KEY)
  } catch {
    // 同上：清理失败无须上报
  }
}
