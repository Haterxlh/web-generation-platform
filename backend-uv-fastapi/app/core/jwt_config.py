# app/core/jwt_config.py —— JWT 配置：从 .env 读取密钥等信息
# 为什么放 core：core 放"跨层基础组件"（配置、数据库会话），JWT 配置属于这一类

from app.core.settings_base import AppSettings


class JwtSettings(AppSettings):
    """
    JWT 相关配置，字段名与 .env 里的变量名对应（不区分大小写）。
    """
    jwt_secret: str                      # 签名密钥（.env 里的 JWT_SECRET）
    jwt_algorithm: str = "HS256"         # 加密算法
    jwt_expire_minutes: int = 1440       # token 有效期(分钟)：1440 = 24 小时
    # 预览票据有效期（分钟）：比登录态短得多，因为它只是"打开一次预览"用的
    preview_ticket_minutes: int = 30


jwt_settings = JwtSettings()  # 全局共用一份配置