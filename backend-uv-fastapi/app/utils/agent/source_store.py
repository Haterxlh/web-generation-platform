# app/utils/agent/source_store.py —— 上传附件的落盘（含路径与文件名的安全校验）
#
# 职责边界：只负责"把上传的字节安全写到 uploads/{user_id}/{source_uuid}/ 下"，
# 不做业务判断（是否允许这个类型、要不要解析，都由 service 决定）。
#
# 与产物落盘（`app/utils/weg_gen/file_writer.py`）的区别，以及为什么没有复用：
#   1. 产物名是**我们自己钉死的白名单名**（index.html / style.css），可以用 `safe_name()` 严格校验；
#      而上传文件的原始名是用户给的（"需求说明.md"、带空格、带中文、甚至 `../../x`），
#      按 `safe_name()` 那套规则会**全部拒绝** —— 用户连正常的中文文件名都传不上来。
#   2. 所以这里采取"**展示名与磁盘名分离**"：
#      - `display_name`（进数据库、给用户看）保留原始文件名；
#      - 磁盘上固定存成 `source{后缀}`，后缀必须落在解析层的白名单里。
#      于是磁盘名永远安全，而用户看到的还是自己的文件名。

from pathlib import Path

from app.core.storage_config import storage_settings
from app.utils.doc import SUPPORTED_EXTENSIONS
from app.utils.weg_gen.file_writer import safe_name

# 磁盘上的固定文件名（不含后缀）：一个 source_uuid 对应一个附件，不会重名
DISK_STEM = "source"


class UploadStoreError(ValueError):
    """上传落盘失败（标识不合法、后缀不在白名单等）。"""


def source_dir(user_id: int, source_uuid: str) -> Path:
    """附件目录的绝对路径：uploads/{user_id}/{source_uuid}。

    Args:
        user_id: 用户 id。
        source_uuid: 附件唯一标识（uuid4.hex）。

    Returns:
        目录的 Path 对象（不保证已存在）。

    Raises:
        UploadStoreError: source_uuid 含非法字符（防路径穿越）。
    """
    try:
        safe_uuid = safe_name(source_uuid)
    except ValueError as error:
        raise UploadStoreError(f"附件标识不合法：{source_uuid!r}") from error
    return storage_settings.uploads_path / str(int(user_id)) / safe_uuid


def relative_dir(user_id: int, source_uuid: str) -> str:
    """附件的**相对**目录（形如 ``"1/a3f9…"``），用于落库。

    只存相对路径：换存储位置（或将来换对象存储）时数据不失效 ——
    与产物 `resultDir` 同一约定。

    Args:
        user_id: 用户 id。
        source_uuid: 附件唯一标识。

    Returns:
        相对目录（始终用正斜杠，便于拼 URL 与跨平台一致）。
    """
    source_dir(user_id, source_uuid)  # 先做一次校验
    return f"{int(user_id)}/{source_uuid}"


def disk_name(filename: str | None) -> str:
    """由原始文件名推出"磁盘上的安全文件名"。

    后缀取原始文件名的后缀并**对照解析层白名单**：不在白名单里就不带后缀。
    这样即使有人传 `../../etc/passwd`，磁盘名也只是一个没有后缀的 `source`。

    Args:
        filename: 原始文件名（可为 None）。

    Returns:
        形如 ``source.md`` 或 ``source``。
    """
    suffix = Path(filename or "").suffix.lower()
    return f"{DISK_STEM}{suffix}" if suffix in SUPPORTED_EXTENSIONS else DISK_STEM


def save_upload(
    user_id: int, source_uuid: str, filename: str | None, data: bytes
) -> tuple[str, Path]:
    """把上传字节写入附件目录。

    Args:
        user_id: 用户 id。
        source_uuid: 附件唯一标识。
        filename: 原始文件名（只用于取后缀）。
        data: 文件字节。

    Returns:
        (落库用的**相对路径**, 磁盘上的绝对路径)。相对路径形如 ``"1/a3f9…/source.md"``。

    Raises:
        UploadStoreError: 标识不合法，或 data 为空。
    """
    if not data:
        raise UploadStoreError("上传内容为空")

    directory = source_dir(user_id, source_uuid)
    directory.mkdir(parents=True, exist_ok=True)

    target = directory / disk_name(filename)
    # 与产物落盘同一考虑：显式二进制写，不做任何编码/换行转换
    target.write_bytes(data)

    return f"{relative_dir(user_id, source_uuid)}/{target.name}", target
