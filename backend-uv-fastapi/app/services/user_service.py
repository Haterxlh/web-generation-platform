# app/services/user_service.py —— 业务层：注册/登录的"规则中枢"
# 职责：只写业务规则（查重、校验密码、签发 token），不直接碰 HTTP 和 SQL
# 调用链：路由(api) → 本层(service) → 数据层(repository)

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.user_schemas import LoginRequest, LoginResponse, RegisterRequest
from app.utils.jwt.security import create_access_token, hash_password, verify_password


class UserService:
    """用户相关的业务操作集合。"""

    @staticmethod
    def register(db: Session, req: RegisterRequest) -> User:
        """
        注册新用户。
        流程：查重 → 密码加密 → 入库。

        Args:
            db: 数据库会话（由 FastAPI 依赖注入）。
            req: 注册请求体（账号 + 密码）。

        Returns:
            入库后的 User 对象（含数据库生成的主键）。

        Raises:
            HTTPException: 账号已存在时返回 409。
        """
        # 规则1：账号不允许重复
        exists = UserRepository.get_by_user_account(db, req.user_account)
        if exists is not None:
            raise HTTPException(status_code=409, detail="该账号已被注册")

        # 规则2：密码绝不明文入库，先转成 bcrypt 哈希
        user = User(
            user_account=req.user_account,
            user_password=hash_password(req.user_password),
        )
        return UserRepository.create(db, user)

    @staticmethod
    def login(db: Session, req: LoginRequest) -> LoginResponse:
        """用户登录。

        流程：查用户 → 校验密码 → 签发 token。

        Args:
            db: 数据库会话（由 FastAPI 依赖注入）。
            req: 登录请求体（账号 + 密码）。

        Returns:
            登录响应: JWT token + 用户信息。

        Raises:
            HTTPException: 账号不存在或密码错误时返回 400。
        """
        # 获取 ORM 的 user 对象
        user = UserRepository.get_by_user_account(db, req.user_account)

        # 规则：账号不存在 和 密码错误 提示一样，防止攻击者试探"哪些账号存在"
        if user is None or not verify_password(req.user_password, user.user_password):
            raise HTTPException(status_code=400, detail="账号或密码错误")

        token = create_access_token(user.id)
        # UserResponse 开了 from_attributes
        # ORM 的 user 对象可直接转成schemas 的 UserResponse 对象
        # user 不能直接返回给前端，因为它含有敏感字段 user_password、is_delete
        return LoginResponse(access_token=token, user=user)