# app/core/jwt_config.py —— JWT 配置：从 .env 读取密钥等信息
# 为什么放 core：core 放"跨层基础组件"（配置、数据库会话），JWT 配置属于这一类

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 和 mysql_config.py 一样，用绝对路径定位 .env（在 backend-uv-fastapi/ 下）
BASE_DIR = Path(__file__).resolve().parent.parent.parent
ENV_FILE = BASE_DIR / ".env"


class JwtSettings(BaseSettings):
    """
    JWT 相关配置，字段名与 .env 里的变量名对应（不区分大小写）。
    """

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    jwt_secret: str                      # 签名密钥（.env 里的 JWT_SECRET）
    jwt_algorithm: str = "HS256"         # 加密算法
    jwt_expire_minutes: int = 1440       # token 有效期(分钟)：1440 = 24 小时


jwt_settings = JwtSettings()  # 全局共用一份配置