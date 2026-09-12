/**
 * 用户模块数据类型：与 backend-uv-fastapi/app/schemas/user_schemas.py 的 Pydantic 模型对齐。
 * 后端出入参字段为蛇形命名，前端保持 snake_case，不做驼峰转换。
 */

/** 注册请求体：对应后端 RegisterRequest */
export interface RegisterRequest {
  /** 登录账号（2~32 位） */
  user_account: string
  /** 密码（至少 6 位） */
  user_password: string
}

/** 登录请求体：对应后端 LoginRequest */
export interface LoginRequest {
  /** 登录账号 */
  user_account: string
  /** 密码 */
  user_password: string
}

/** 用户信息（安全白名单，不含密码）：对应后端 UserResponse */
export interface User {
  id: number
  user_account: string
  user_name: string | null
  user_avatar: string | null
  user_profile: string | null
  user_role: string
  create_time: string | null
}

/** 登录响应：对应后端 LoginResponse */
export interface LoginResponse {
  /** JWT，后续请求通过 Authorization: Bearer <token> 带上 */
  access_token: string
  /** token 类型，固定 "bearer" */
  token_type: string
  /** 当前登录用户信息 */
  user: User
}
