/** 通用格式化工具（与业务无关） */

/** 字节数格式化为可读文本，如 1.5 MB */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** index).toFixed(1)} ${units[index]}`
}

/** 格式化为本地日期时间字符串 */
export function formatDateTime(value: string | number | Date): string {
  const date = value instanceof Date ? value : new Date(value)
  return date.toLocaleString('zh-CN', { hour12: false })
}
