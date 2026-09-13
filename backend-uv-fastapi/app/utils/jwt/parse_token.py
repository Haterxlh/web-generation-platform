# app/utils/jwt/parse_token.py —— FastAPI 依赖：把请求头里的 token 变成"当前登录用户"
# 作用：所有"需要登录才能访问"的接口，参数里写 Depends(get_current_user)，
#        FastAPI 就会自动先执行这里的校验，把用户对象交给接口。

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.mysql_db import get_mysql_db
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.utils.jwt.security import decode_token

# HTTPBearer：告诉 FastAPI 这个接口要求请求头带 Authorization: Bearer <token>
bearer_scheme = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_mysql_db),
) -> User:
    """对请求头里的 token 进行校验、解析，返回当前登录用户。

    Args:
        credentials: FastAPI 从请求头 Authorization: Bearer xxx 中解析出的凭证。
        db: 数据库会话（由 get_mysql_db 依赖注入）。

    Returns:
        当前登录的 User 对象（已过滤逻辑删除）。

    Raises:
        HTTPException: token 无效/过期返回 401；用户不存在返回 401。
    """
    try:
        user_id = decode_token(credentials.credentials)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录状态无效或已过期，请重新登录",
        )

    user = UserRepository.get_by_id(db, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )
    return user