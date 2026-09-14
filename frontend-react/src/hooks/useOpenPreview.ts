import { useState } from 'react'

import { issuePreviewTicket } from '@/api/generation_api'
import { ApiError } from '@/api/http'

/**
 * 打开预览的共享逻辑：签票据 → 复用占位窗口跳转。
 *
 * 生成页与项目页共用同一套流程（尤其是"同步阶段先占住窗口"这一步极易漏写），
 * 所以抽成 hook：调用方只管给 task_uuid，其余细节都在这里。
 */
export function useOpenPreview(): {
  openPreview: (taskUuid: string) => Promise<void>
  openingUuid: string | null
  error: string | null
} {
  /** 正在打开的任务 uuid：用于逐行禁用按钮、显示"正在打开…" */
  const [openingUuid, setOpeningUuid] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function openPreview(taskUuid: string): Promise<void> {
    setOpeningUuid(taskUuid)
    setError(null)

    // ① 必须在"用户点击"的同步阶段占住窗口：await 之后再 open 会被当成弹窗拦截
    // ② 不能用 'noopener'：带上它 window.open 必然返回 null，就拿不到句柄了
    const previewWindow = window.open('', '_blank')

    try {
      const ticket = await issuePreviewTicket(taskUuid)

      if (previewWindow === null) {
        setError('浏览器拦截了新窗口，请允许本站弹出窗口后重试')
        return
      }

      previewWindow.opener = null
      previewWindow.location.href = ticket.preview_url
    } catch (err) {
      previewWindow?.close() // 失败时别留一个空白标签页
      setError(err instanceof ApiError ? err.message : '打开预览失败，请稍后重试')
    } finally {
      setOpeningUuid(null)
    }
  }

  return { openPreview, openingUuid, error }
}
