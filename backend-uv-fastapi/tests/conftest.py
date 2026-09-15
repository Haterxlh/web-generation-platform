"""pytest 全局夹具。

为什么这里要**覆盖 pytest 内置的 ``tmp_path``**：

本项目的开发环境启用了文件沙箱，写入系统临时目录下的
``%TEMP%/pytest-of-<user>/`` 会被直接拒绝（``PermissionError: [WinError 5]``），
于是所有用到 ``tmp_path`` 的用例在 **setup 阶段**就报错 —— 与被测代码无关。

这里把它重定向到工作区内的 ``tests/.pytest_tmp/``（可写，且已在 .gitignore 中忽略），
用例代码因此**不需要任何改动**，在沙箱内外行为一致。
"""

import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

_TMP_ROOT = Path(__file__).resolve().parent / ".pytest_tmp"
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Iterator[Path]:
    """每个用例一个独立的临时目录（位于工作区内，用完即删）。

    目录名由用例名派生：排查失败时一眼能看出是谁留下的。
    用例名里的参数化括号、斜杠等会被替换成下划线。

    Args:
        request: pytest 提供的用例上下文（用来取用例名）。

    Yields:
        该用例专属的临时目录。
    """
    name = _UNSAFE_CHARS.sub("_", request.node.name)[:80]
    path = _TMP_ROOT / name
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
