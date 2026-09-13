# app/core/settings_base.py —— 配置基类：.env 的定位与读取规则只写这一处
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 本文件在 app/core/ 下：parents[0]=core, parents[1]=app, parents[2]=backend-uv-fastapi
# （写 parents[2] 比 .parent.parent.parent 更明确：下标就是"往上几层"）
BASE_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BASE_DIR / ".env"


class AppSettings(BaseSettings):
    """所有配置类的基类：统一 .env 的位置与解析行为。"""

    # extra="ignore" 表示：忽略 .env 里没有的字段，只保留 model_config 里的字段，
    # 如果 .env 里有字段，但是 model_config 里没有的字段，会报错。
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",  # 一份 .env 被多个 Settings 共享，各取所需
    )