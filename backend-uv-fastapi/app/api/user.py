# app/api/user.py —— 用户模块的 HTTP 路由（表现层）
# 职责：收请求 → 调 service → 返回响应；不写业务规则、不写 SQL

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.utils.jwt.parse_token import get_current_user
from app.core.mysql_db import get_mysql_db
from app.models.user import User
from app.schemas.user_schemas import LoginRequest, LoginResponse, RegisterRequest, UserResponse
from app.services.user_service import UserService

# prefix: 本模块所有接口统一挂在 /user 下
# tags:   让 /docs 文档按"用户"分组显示
router = APIRouter(prefix="/user", tags=["用户"])


@router.post("/register", response_model=UserResponse, summary="注册新用户")
def register(req: RegisterRequest, db: Session = Depends(get_mysql_db)) -> User:
    """注册接口。response_model 保证响应只含 UserResponse 白名单字段（绝无密码）。"""
    return UserService.register(db, req)


@router.post("/login", response_model=LoginResponse, summary="登录获取token")
def login(req: LoginRequest, db: Session = Depends(get_mysql_db)) -> LoginResponse:
    """登录接口：成功返回 token + 用户信息。"""
    return UserService.login(db, req)


@router.get("/current", response_model=UserResponse, summary="获取当前登录用户")
def current_user(current_user: User = Depends(get_current_user)) -> User:
    """需要登录的接口：token 校验通过后，把当前用户返回给前端。"""
    return current_user