/**
 * 生成模块数据类型：与 backend-uv-fastapi/app/schemas/generation_schemas.py 的 Pydantic 模型对齐。
 * 后端出入参字段为蛇形命名，前端保持 snake_case，不做驼峰转换。
 */

/** 生成类型：single=单个 HTML 文件；multi=html+css+js 多文件（对应后端 Literal["single","multi"]） */
export type GenType = 'single' | 'multi'

/** 生成任务状态（对应后端 status 字段的取值） */
export type GenStatus = 'running' | 'success' | 'failed'

/** 发起一次生成的请求体：对应后端 GenerateRequest */
export interface GenerateRequest {
  /** 网页需求描述（后端限制 2~2000 字） */
  prompt: string
  /** 生成类型，默认 single */
  gen_type: GenType
}

/** 生成任务详情：对应后端 GenerationTaskResponse */
export interface GenerationTask {
  /** 任务唯一标识（产物目录名与接口路径都用它） */
  task_uuid: string
  /** 用户需求原文 */
  prompt: string
  /** 生成类型 */
  gen_type: GenType
  /** 状态：running / success / failed */
  status: GenStatus
  /** 产物相对目录，失败或未完成时为 null */
  result_dir: string | null
  /** 产物文件名列表（后端已把 JSON 字符串解析成数组） */
  file_list: string[]
  /** 网页预览地址（相对路径，如 /preview/1/xxx/index.html），无产物时为 null */
  preview_url: string | null
  /** 失败原因 */
  error_msg: string | null
  /** 耗时（毫秒） */
  duration_ms: number | null
  /** 创建时间（ISO 字符串，如 "2026-09-14T18:37:55"） */
  create_time: string | null
  /** 输入 token 数 */
  input_tokens: number | null
  /** 输出 token 数（含思考） */
  output_tokens: number | null
  /** 其中思考 token 数 */
  reasoning_tokens: number | null
}

/** 生成历史列表（分页）：对应后端 GenerationListResponse */
export interface GenerationListResponse {
  /** 总条数 */
  total: number
  /** 当前页数据 */
  items: GenerationTask[]
}

/** 预览票据签发结果：对应后端 PreviewTicketResponse */
export interface PreviewTicketResponse {
  /** 预览地址（前端直接在新标签页打开） */
  preview_url: string
  /** 票据有效期（秒） */
  expires_in: number
}
