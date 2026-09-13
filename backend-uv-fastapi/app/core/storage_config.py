# app/core/storage_config.py —— 生成产物的存储配置
from pathlib import Path

from app.core.settings_base import BASE_DIR, AppSettings


class StorageSettings(AppSettings):
    """产物存储配置（字段名对应 .env 变量名，不区分大小写）。

    Attributes:
        generated_dir: 产物根目录；写相对路径时按 backend-uv-fastapi/ 解析，也可在 .env 写绝对路径。
        preview_prefix: 预览 URL 前缀（步骤 9 用它挂载 StaticFiles）。
    """

    generated_dir: str = "generated"
    preview_prefix: str = "/preview"

    @property
    def generated_path(self) -> Path:
        """产物根目录的绝对路径。"""
        path = Path(self.generated_dir)
        return path if path.is_absolute() else BASE_DIR / path


storage_settings = StorageSettings()