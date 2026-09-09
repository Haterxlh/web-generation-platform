/**
 * 商品接口模块：演示「一个后端路由文件 ↔ 一个前端 api 模块」的组织方式。
 * 当前对应 backend-uv-fastapi/app/main.py 中的示例路由（后续按业务域拆分）。
 */
import { request } from './http'
import type { Item, ItemUpdateResult } from '@/types'

/** GET /items/{item_id}：查询商品 */
export function getItem(itemId: number, signal?: AbortSignal): Promise<{ item_id: number; q: string | null }> {
  return request(`/api/items/${itemId}`, { signal })
}

/** PUT /items/{item_id}：更新商品 */
export function updateItem(itemId: number, item: Item): Promise<ItemUpdateResult> {
  return request(`/api/items/${itemId}`, { method: 'PUT', body: item })
}
