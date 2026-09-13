# app/utils/file_writer.py —— 产物落盘（含路径安全校验）
# 职责边界：只负责"把 {文件名: 内容} 安全写进产物目录"，不做业务判断

import re
from pathlib import Path

from app.core.storage_config import storage_settings

# 白名单式校验：只允许 字母/数字/下划线/连字符，用点分隔的多段
# 这一条正则就把 ../、绝对路径、C:\、子目录 全部从根上堵死
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")


class UnsafeFileNameError(ValueError):
    """文件名不合法（含路径分隔符、..、绝对路径等）。"""


def _safe_name(filename: str) -> str:
    """校验并返回安全的文件名。

    Args:
        filename: 待校验的文件名（只允许单层文件名，不允许任何路径成分）。

    Returns:
        校验通过的文件名。

    Raises:
        UnsafeFileNameError: 文件名为空或含路径成分/非法字符。
    """
    name = (filename or "").strip()
    if not name or ".." in name or not SAFE_NAME_RE.match(name):
        raise UnsafeFileNameError(f"非法文件名：{filename!r}")
    return name


def task_dir(user_id: int, task_uuid: str) -> Path:
    """任务产物目录的绝对路径：{产物根目录}/{user_id}/{task_uuid}。

    Args:
        user_id: 发起用户 id。
        task_uuid: 任务唯一标识（同时作为目录名）。

    Returns:
        目录的 Path 对象（不保证已存在）。
    """
    return storage_settings.generated_path / str(user_id) / _safe_name(task_uuid)


def write_files(user_id: int, task_uuid: str, files: dict[str, str]) -> str:
    """把一批文件写入任务目录（同名覆盖）。

    Args:
        user_id: 发起用户 id。
        task_uuid: 任务唯一标识（同时作为目录名）。
        files: {文件名: 文件内容}。

    Returns:
        产物**相对目录**（形如 "1/a3f9..."），用于落库（只存相对路径，见设计约定 §4）。

    Raises:
        ValueError: files 为空。
        UnsafeFileNameError: 含非法文件名。
    """
    if not files:
        raise ValueError("files 为空，没有可写入的内容")

    # 先把所有文件名校验完再动手写：避免"写到一半撞上非法名字"，留下半成品目录
    safe_files = {_safe_name(name): content for name, content in files.items()}

    directory = task_dir(user_id, task_uuid)
    directory.mkdir(parents=True, exist_ok=True)  # 目录已存在也不报错（重试时复用同一目录）

    for name, content in safe_files.items():
        # newline="\n"：禁止 Windows 自动把 \n 转成 \r\n，
        # 保证同一份模型输出在 Windows 开发机与 Linux 服务器上产出**字节一致**的文件
        (directory / name).write_text(content, encoding="utf-8", newline="\n")

    # 注意：这里的 "/" 是手写的，不能用 str(directory) —— Windows 上 Path 会给出反斜杠，
    # 而这个值要存进数据库、还要拼进 URL，必须是正斜杠
    return f"{user_id}/{task_uuid}"