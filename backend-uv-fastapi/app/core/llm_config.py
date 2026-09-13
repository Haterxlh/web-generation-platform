# app/core/llm_config.py —— 大模型（DeepSeek）配置：从 .env 读取
# 为什么放 core：与 mysql_config.py / jwt_config.py 同类，属于"跨层基础组件"

from typing import Literal
from app.core.settings_base import AppSettings

class LlmSettings(AppSettings):
    """
    DeepSeek 模型配置，字段名与 .env 里的变量名对应（不区分大小写）。
    """
    # 密钥：故意不给默认值 → .env 没配就在启动时立刻报错（快速失败，和 jwt_secret 同一个套路）
    deepseek_api_key: str

    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-flash"

    # 用 Literal 做白名单校验：.env 里写错档位会当场报错，而不是悄悄发个 400 给 API
    deepseek_thinking: Literal["enabled", "disabled"] = "enabled"
    deepseek_reasoning_effort: Literal["low", "high", "max"] = "high"

    # 仅非思考模式生效
    llm_temperature: float = 0.3
    llm_max_tokens: int = 16384


llm_settings = LlmSettings()  # 全局共用一份