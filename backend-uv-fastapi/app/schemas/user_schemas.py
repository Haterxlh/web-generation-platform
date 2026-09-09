# app/schemas/user.py —— 用户模块的"出入参模板"（Pydantic 模型）
# 作用：
# 1) 自动校验请求参数（不合格返回 422）  
# 2) 控制响应只含白名单字段
# 为什么单独一个 schemas 目录：ORM 模型(models)管"数据库长什么样"，
# 出入参模型(schemas)管"接口长什么样"，两者分开，避免把密码等内部字段暴露出去。

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    """
    注册请求体：前端调用 /user/register 时按这个格式传参
    """
    user_account: str = Field(min_length=2, max_length=32, description="登录账号")
    user_password: str = Field(min_length=6, max_length=64, description="密码(至少6位)")


class LoginRequest(BaseModel):
    """
    登录请求体：前端调用 /user/login 时按这个格式传参
    """
    user_account: str = Field(description="登录账号")
    user_password: str = Field(description="密码")


class UserResponse(BaseModel):
    """
    用户信息响应体 —— 安全白名单！
    不写 user_password、is_delete：密码哈希、内部标记永远不下发到前端。
    from_attributes=True 表示：允许直接把 ORM 的 User 对象转成这个模型。
    """
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_account: str
    user_name: str | None = None
    user_avatar: str | None = None
    user_profile: str | None = None
    user_role: str
    create_time: Optional[datetime] = None


class LoginResponse(BaseModel):
    """
    登录成功后的响应：token + 用户信息
    """
    access_token: str = Field(description="JWT，后续请求带上它证明身份")
    token_type: str = "bearer"
    user: UserResponse