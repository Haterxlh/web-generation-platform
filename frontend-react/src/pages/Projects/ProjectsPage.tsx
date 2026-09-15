import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { listGenerations } from '@/api/generation_api'
import { ApiError } from '@/api/http'
import Button from '@/components/common/Button'
import { useOpenPreview } from '@/hooks/useOpenPreview'
import type { GenerationTask, GenStatus, GenType } from '@/types/generation_types'
import styles from './ProjectsPage.module.css'

/** 每页条数（后端 page_size 允许 1~100，这里取一个适合阅读的值） */
const PAGE_SIZE = 10

/**
 * 状态 → 中文文案。
 * 用 Record<GenStatus, string> 而不是普通对象：将来后端新增状态时，TS 会立刻提示"缺一个键"。
 */
const STATUS_TEXT: Record<GenStatus, string> = {
  running: '生成中',
  success: '成功',
  failed: '失败',
}

/** 状态 → 样式类名（同样用 Record 保证新增状态时编译期报错） */
const STATUS_CLASS: Record<GenStatus, string> = {
  running: styles.statusRunning,
  success: styles.statusSuccess,
  failed: styles.statusFailed,
}

/**
 * 生成类型 → 中文文案。
 * `multi` 已退役（阶段 7），但历史任务里还有它的记录 —— 照实显示，不假装没有。
 */
const GEN_TYPE_TEXT: Record<GenType, string> = {
  agent: 'Agent 生成',
  single: '单页极速',
  multi: '多文件（已退役）',
}

/**
 * 格式化后端时间：返回的是无时区的本地时间字符串（如 2026-09-14T18:37:55）。
 * 直接做字符串处理，既不用引日期库，也不会踩时区解析的坑。
 */
function formatTime(value: string | null): string {
  if (value === null) return '-'
  return value.replace('T', ' ').slice(0, 16)
}

/** 统一的错误文案：抽出来是为了让"首屏加载"和"翻页加载"两处不各写一份 */
function toErrorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : '加载失败，请稍后重试'
}

/** 项目列表页：我的生成历史（分页），可重新打开预览 */
export default function ProjectsPage() {
  const { openPreview, openingUuid, error: previewError } = useOpenPreview()

  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [items, setItems] = useState<GenerationTask[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  /** 用户点击翻页时的加载：同步置位 loading / error（事件处理器里 setState 不受 effect 规则约束） */
  const load = useCallback(async (targetPage: number): Promise<void> => {
    setLoading(true)
    setError(null)
    try {
      const data = await listGenerations(targetPage, PAGE_SIZE)
      setItems(data.items)
      setTotal(data.total)
      setPage(targetPage)
    } catch (err) {
      setError(toErrorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  // 首次进入加载第 1 页。
  // ⚠️ 这里**刻意不复用 load()**：eslint 规则 react-hooks/set-state-in-effect 不允许
  // effect 体内（哪怕只是间接）同步调用 setState —— 而 load 的第一句就是 setLoading(true)。
  // 该规则认可的形态是"在回调函数里 setState"，所以下面把 setState 全部放进 Promise 回调。
  // 顺带用 cancelled 兜住"组件已卸载（含 StrictMode 开发期二次挂载）后请求才返回"的竞态。
  useEffect(() => {
    let cancelled = false

    listGenerations(1, PAGE_SIZE)
      .then((data) => {
        if (cancelled) return
        setItems(data.items)
        setTotal(data.total)
        setPage(1)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(toErrorMessage(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <section className="page">
      <h1>我的项目</h1>
      <p className="page-desc">这里是你所有的生成记录，点击可重新打开预览。</p>

      {error !== null && <p className={styles.error}>{error}</p>}
      {loading && <p className={styles.empty}>加载中…</p>}

      {!loading && items.length === 0 && (
        <p className={styles.empty}>
          还没有生成记录，去 <Link to="/generate">生成应用</Link> 创建第一个吧。
        </p>
      )}

      {!loading && items.length > 0 && (
        <>
          <ul className={styles.list}>
            {items.map((task) => (
              <li key={task.task_uuid} className={styles.item}>
                <div className={styles.main}>
                  <p className={styles.prompt}>{task.prompt}</p>

                  <p className={styles.meta}>
                    <span className={`${styles.status} ${STATUS_CLASS[task.status]}`}>
                      {STATUS_TEXT[task.status]}
                    </span>
                    <span>{GEN_TYPE_TEXT[task.gen_type]}</span>
                    <span>{formatTime(task.create_time)}</span>
                    {task.duration_ms !== null && (
                      <span>{(task.duration_ms / 1000).toFixed(1)} 秒</span>
                    )}
                    {task.output_tokens !== null && <span>输出 {task.output_tokens} token</span>}
                  </p>

                  {/* 失败原因必须展示：否则用户只知道"失败了"，不知道"为什么" */}
                  {task.error_msg !== null && <p className={styles.fail}>{task.error_msg}</p>}
                </div>

                <Button
                  variant="ghost"
                  size="sm"
                  // 只有成功任务才有产物；running / failed 点下去必然被后端 400 拒绝
                  disabled={task.status !== 'success' || openingUuid === task.task_uuid}
                  onClick={() => void openPreview(task.task_uuid)}
                >
                  {openingUuid === task.task_uuid ? '正在打开…' : '打开预览'}
                </Button>
              </li>
            ))}
          </ul>

          <div className={styles.pager}>
            <Button
              variant="ghost"
              size="sm"
              disabled={page <= 1 || loading}
              onClick={() => void load(page - 1)}
            >
              上一页
            </Button>

            <span className={styles.pageInfo}>
              第 {page} / {totalPages} 页 · 共 {total} 条
            </span>

            <Button
              variant="ghost"
              size="sm"
              disabled={page >= totalPages || loading}
              onClick={() => void load(page + 1)}
            >
              下一页
            </Button>
          </div>
        </>
      )}

      {previewError !== null && <p className={styles.error}>{previewError}</p>}
    </section>
  )
}
