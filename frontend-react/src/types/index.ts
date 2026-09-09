/**
 * 数据模型层：与后端 app/models 对应的数据类型定义。
 * 对接 FastAPI DTO，字段与后端保持一致（Pydantic 定义见 backend-uv-fastapi/app/models）。
 */

/** 商品项：对应后端示例 Item（Pydantic 模型） */
export interface Item {
  /** 商品名称 */
  name: string
  /** 商品价格（> 0） */
  price: number
  /** 是否优惠商品 */
  is_offer?: boolean | null
}

/** PUT /items/{item_id} 的响应 */
export interface ItemUpdateResult {
  item_name: string
  item_id: number
}
