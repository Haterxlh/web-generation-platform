import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { listGenerations } from '@/api/generation_api'
import { ApiError } from '@/api/http'
import { useOpenPreview } from '@/hooks/useOpenPreview'
import type { GenerationTask, GenStatus, GenType } from '@/types/generation_types'

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

/** 生成类型 → 中文文案 */
const GEN_TYPE_TEXT: Record<GenType, string> = {
  single: '单文件',
  multi: '多文件',
}

/**
 * 格式化后端时间：返回的是无时区的本地时间字符串（如 2026-09-14T18:37:55）。
 * 直接做字符串处理，既不用引日期库，也不会踩时区解析的坑。
 */
function formatTime(value: string | null): string {
  if (value === null) return '-'
  return value.replace('T', ' ').slice(0, 16)
}

/** 项目列表页：我的生成历史（分页），可重新打开预览 */
export default function ProjectsPage() {
  const { openPreview, openingUuid, error: previewError } = useOpenPreview()

  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const [items, setItems] = useState<GenerationTask[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // useCallback：让 load 的引用保持稳定，否则下面的 useEffect 每次渲染都会重跑（→ 无限请求）
  const load = useCallback(async (targetPage: number): Promise<void> => {
    setLoading(true)
    setError(null)
    try {
      const data = await listGenerations(targetPage, PAGE_SIZE)
      setItems(data.items)
      setTotal(data.total)
      setPage(targetPage)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '加载失败，请稍后重试')
    } finally {
      setLoading(false)
    }
  }, [])

  // 首次进入加载第 1 页（开发环境的 StrictMode 会让 effect 跑两次，属预期行为，见讲解 6.4②）
  // void 表示忽视 load 的返回值
  // 依赖 load 函数的引用
  useEffect(() => {
    void load(1)
  }, [load])

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <section className="page">
      <h1>我的项目</h1>
      <p className="page-desc">这里是你所有的生成记录，点击可重新打开预览。</p>

      {error !== null && <p className="gen-error">{error}</p>}
      {loading && <p className="proj-empty">加载中…</p>}

      {!loading && items.length === 0 && (
        <p className="proj-empty">
          还没有生成记录，去 <Link to="/generate">生成应用</Link> 创建第一个吧。
        </p>
      )}

      {!loading && items.length > 0 && (
        <>
          <ul className="proj-list">
            {items.map((task) => (
              <li key={task.task_uuid} className="proj-item">
                <div className="proj-main">
                  <p className="proj-prompt">{task.prompt}</p>

                  <p className="proj-meta">
                    <span className={`proj-status proj-status-${task.status}`}>
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
                  {task.error_msg !== null && <p className="proj-fail">{task.error_msg}</p>}
                </div>

                <button
                  className="gen-button gen-button-inline"
                  type="button"
                  // 只有成功任务才有产物；running / failed 点下去必然被后端 400 拒绝
                  disabled={task.status !== 'success' || openingUuid === task.task_uuid}
                  onClick={() => void openPreview(task.task_uuid)}
                >
                  {openingUuid === task.task_uuid ? '正在打开…' : '打开预览'}
                </button>
              </li>
            ))}
          </ul>

          <div className="proj-pager">
            <button
              className="proj-page-button"
              type="button"
              disabled={page <= 1 || loading}
              onClick={() => void load(page - 1)}
            >
              上一页
            </button>

            <span className="proj-page-info">
              第 {page} / {totalPages} 页 · 共 {total} 条
            </span>

            <button
              className="proj-page-button"
              type="button"
              disabled={page >= totalPages || loading}
              onClick={() => void load(page + 1)}
            >
              下一页
            </button>
          </div>
        </>
      )}

      {previewError !== null && <p className="gen-error">{previewError}</p>}
    </section>
  )
}
