# app/core/storage_config.py —— 生成产物与上传附件的存储配置
from pathlib import Path

from app.core.settings_base import BASE_DIR, AppSettings


class StorageSettings(AppSettings):
    """产物存储配置（字段名对应 .env 变量名，不区分大小写）。

    Attributes:
        generated_dir: 产物根目录；写相对路径时按 backend-uv-fastapi/ 解析，也可在 .env 写绝对路径。
        uploads_dir: 上传附件根目录（阶段 3 起使用）；口径与 generated_dir 一致。
        preview_prefix: 预览 URL 前缀（步骤 9 用它挂载 StaticFiles）。
    """

    generated_dir: str = "generated"
    uploads_dir: str = "uploads"
    preview_prefix: str = "/preview"

    @property
    def generated_path(self) -> Path:
        """产物根目录的绝对路径。"""
        path = Path(self.generated_dir)
        return path if path.is_absolute() else BASE_DIR / path

    @property
    def uploads_path(self) -> Path:
        """上传附件根目录的绝对路径。

        ⚠️ 与 generated_path **分开**存放：产物是"给用户看的结果"，
        附件是"用户给的输入"，两者的生命周期与清理策略完全不同
        （产物可按任务清理，附件在会话存续期内必须保留以便重复解析）。
        """
        path = Path(self.uploads_dir)
        return path if path.is_absolute() else BASE_DIR / path


storage_settings = StorageSettings()