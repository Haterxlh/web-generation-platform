# app/utils/security.py —— 安全工具箱：密码加密 + JWT 签发/解析
# 为什么放 utils：这是与业务无关的通用函数（加密、token），哪里都能用

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.jwt_config import jwt_settings


# ========== 密码部分 ==========

def hash_password(plain_password: str) -> str:
    """
    把明文密码变成"加盐哈希"再入库。
    bcrypt 会自动加随机盐，所以同一个密码两次加密结果不同——这是特性不是 bug，
    攻击者无法通过比对密文猜到密码。
    
    Args:
        plain_password (str): 用户输入的明文密码
    Returns:
        str: 加盐哈希后的密码
    """
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")  # 转成 str 存进 user_password 列


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    登录时校验：用用户输入的明文去和库里存的哈希比对
    
    Args:
        plain_password (str): 用户输入的明文密码
        hashed_password (str): 库里存的加盐哈希密码
    Returns:
        bool: 如果匹配则返回 True，否则返回 False
    """
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        hashed_password.encode("utf-8"),
    )


# ========== JWT 部分 ==========

def create_access_token(user_id: int) -> str:
    """
    登录成功后，为用户签发一个 token。
    token 里只放用户 id(sub) 和过期时间(exp)，不塞密码等敏感信息。
    
    Args:
        user_id (int): 用户 id
    Returns:
        str: JWT token 字符串，有效期 1440 分钟（24 小时）。
    Raises:
        jwt.ExpiredSignatureError: 如果 token 过期
        jwt.DecodeError: 如果 token 被篡改
        jwt.InvalidTokenError: 如果 token 格式错误
    """
    expire_at = datetime.now(timezone.utc) + timedelta(minutes=jwt_settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire_at}
    # 用密钥签名：token 里任何字符被篡改，校验都会失败
    return jwt.encode(payload, jwt_settings.jwt_secret, algorithm=jwt_settings.jwt_algorithm)


def decode_token(token: str) -> int:
    """
    解析前端带回来的 token，取出里面的用户 id。
    token 无效 / 被篡改 / 过期都会抛异常，由调用方处理。
    
    Args:
        token (str): 前端带回来的 JWT token 字符串
    Returns:
        int: 用户 id
    Raises:
        jwt.ExpiredSignatureError: 如果 token 过期
        jwt.DecodeError: 如果 token 被篡改
        jwt.InvalidTokenError: 如果 token 格式错误
    """
    payload = jwt.decode(token, jwt_settings.jwt_secret, algorithms=[jwt_settings.jwt_algorithm])
    return int(payload["sub"])