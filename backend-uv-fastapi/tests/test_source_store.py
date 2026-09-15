"""阶段 3 的离线测试（四）：上传附件的落盘（source_store）。

守两件事：

1. **磁盘名与展示名分离**。用户的上传原名可能是 ``需求说明.md``（中文 + 空格），
   也可能是 ``../../etc/passwd``（恶意）。若直接拿原名落盘：
   前者被 `safe_name()` 拒绝（正常用户传不上来），后者直接写出目录。
   所以磁盘上固定存 ``source{后缀}``，后缀还必须落在解析层的白名单里。
2. **只有白名单后缀能被保留** —— 后缀决定了解析器怎么读这个文件，
   放任任意后缀等于让用户决定我们调用哪个解析器。
"""

from pathlib import Path

import pytest

from app.core.storage_config import storage_settings
from app.utils.agent.source_store import (
    UploadStoreError,
    disk_name,
    relative_dir,
    save_upload,
    source_dir,
)


@pytest.fixture
def uploads_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把上传根目录临时改到用例的临时目录，避免污染真实的 uploads/。"""
    root = tmp_path / "uploads"
    monkeypatch.setattr(storage_settings, "uploads_dir", str(root))
    return root


# --------------------------------------------------------------------------
# 1. 路径
# --------------------------------------------------------------------------


def test_source_dir_layout(uploads_root: Path) -> None:
    """目录结构：uploads/{user_id}/{source_uuid}。"""
    directory = source_dir(7, "a3f9" * 8)

    assert directory == uploads_root / "7" / ("a3f9" * 8)


def test_relative_dir_uses_forward_slashes(uploads_root: Path) -> None:
    """相对路径存库、还要拼 URL，必须是正斜杠（Windows 上 Path 会给反斜杠）。"""
    relative = relative_dir(7, "abc123")

    assert relative == "7/abc123"
    assert "\\" not in relative


def test_relative_dir_does_not_create_directory(uploads_root: Path) -> None:
    """只算路径不落盘（落盘是 save_upload 的事）。"""
    relative_dir(7, "abc123")

    assert not uploads_root.exists()


def test_illegal_source_uuid_is_rejected(uploads_root: Path) -> None:
    """⚠️ 标识里带路径成分必须拒绝 —— 否则附件会写到 uploads 之外。"""
    for bad in ["../evil", "a/b", "a\\b", "..", "C:/x", ""]:
        with pytest.raises(UploadStoreError):
            source_dir(1, bad)


def test_uploads_and_generated_roots_are_separate() -> None:
    """附件与产物的根目录必须分开（生命周期与清理策略不同）。"""
    assert storage_settings.uploads_path != storage_settings.generated_path
    assert storage_settings.uploads_path.name == "uploads"
    assert storage_settings.generated_path.name == "generated"


# --------------------------------------------------------------------------
# 2. 磁盘文件名：展示名与磁盘名分离
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("需求说明.md", "source.md"),
        ("report.PDF", "source.pdf"),
        ("设计规范.html", "source.html"),
        ("备注.txt", "source.txt"),
        ("说明.htm", "source.htm"),
        ("no-extension", "source"),
        ("恶意.docx", "source"),  # 不在白名单 → 不带后缀
        ("../../etc/passwd", "source"),  # 无后缀 → 仍是安全的固定名
        (None, "source"),
    ],
)
def test_disk_name_is_always_safe(filename: str | None, expected: str) -> None:
    """磁盘名永远由我们决定；只有白名单后缀会被保留。"""
    assert disk_name(filename) == expected


def test_disk_name_never_contains_path_parts() -> None:
    """无论原始名多恶意，磁盘名都必须只是一个普通文件名。"""
    name = disk_name("../../../tmp/x..md")

    assert "/" not in name and "\\" not in name and ".." not in name


# --------------------------------------------------------------------------
# 3. 落盘
# --------------------------------------------------------------------------


def test_save_upload_writes_bytes_and_returns_relative_path(uploads_root: Path) -> None:
    """落盘成功：返回相对路径（含文件名），内容按字节原样写入。"""
    data = "做一个待办清单".encode("utf-8")

    relative, target = save_upload(3, "abc123", "需求.txt", data)

    assert relative == "3/abc123/source.txt"
    assert target == uploads_root / "3" / "abc123" / "source.txt"
    assert target.read_bytes() == data, "必须按字节原样保存（不能做任何编码转换）"


def test_save_upload_preserves_chinese_bytes_in_gbk(uploads_root: Path) -> None:
    """⚠️ 上传的 GBK 文件必须**原样**保存：解析层的编码回退就是靠原始字节判断的。"""
    data = "中文需求".encode("gb18030")

    _relative, target = save_upload(3, "abc123", "需求.txt", data)

    assert target.read_bytes() == data


def test_save_upload_creates_directories(uploads_root: Path) -> None:
    """目录不存在时自动创建（首次上传的场景）。"""
    _relative, target = save_upload(9, "deadbeef", "a.html", b"<html></html>")

    assert target.parent.is_dir()


def test_save_upload_overwrites_same_source(uploads_root: Path) -> None:
    """同一 source_uuid 再次写入是覆盖语义（重传/重解析时复用同一目录）。"""
    save_upload(1, "same", "a.txt", b"first")
    _relative, target = save_upload(1, "same", "a.txt", b"second")

    assert target.read_bytes() == b"second"
    assert len(list(target.parent.iterdir())) == 1, "同一附件目录下只应有一个文件"


def test_save_upload_rejects_empty_data(uploads_root: Path) -> None:
    """空内容不落盘（空文件在解析层也会被拒，这里提前拦住，避免留下垃圾目录）。"""
    with pytest.raises(UploadStoreError, match="内容为空"):
        save_upload(1, "abc", "a.txt", b"")


def test_save_upload_cannot_escape_uploads_root(uploads_root: Path) -> None:
    """⚠️ 恶意文件名 + 恶意标识都不能把文件写到 uploads 根目录之外。"""
    with pytest.raises(UploadStoreError):
        save_upload(1, "../../evil", "../../evil.md", b"x")

    _relative, target = save_upload(1, "ok123", "../../evil.md", b"x")

    assert target.resolve().is_relative_to(uploads_root.resolve())
    assert target.name == "source.md"
