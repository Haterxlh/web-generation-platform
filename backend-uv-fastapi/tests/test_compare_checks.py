# tests/test_compare_checks.py —— 阶段 7 对照脚本判据的离线回归用例
#
# 为什么给一个开发期自检脚本写测试：**判据本身也会写错**。
# 第一版 `_local_refs()` 把 JS 模板串（`<img src="${esc(p.image)}">`）也当成静态引用，
# 于是把一个完全正常的产物判成"引用的本地资源缺失"——若没有离线重判这条退路，
# 修正判据就只能重跑一次模型（再烧一次钱），人就会倾向于将错就错。
# 因此这里把那次假阳性固化成用例：判据以后只会更严，不会再犯同一个错。

from pathlib import Path

from app.utils.utils_check.check_compare import _check_integrity, _local_refs


def _write(path: Path, text: str) -> Path:
    """写一个文件（父目录已存在），返回路径。"""
    path.write_text(text, encoding="utf-8")
    return path


def test_template_string_in_script_is_not_a_static_ref() -> None:
    """`<script>` 里的 JS 模板串不算静态引用（第一版假阳性的原样复现）。"""
    html = (
        "<html><body><div id='list'></div>"
        "<script>const t = `<img src=\"${esc(p.image)}\" alt=\"x\">`;</script>"
        "</body></html>"
    )
    assert _local_refs(html) == []


def test_markup_refs_are_found() -> None:
    """标记语言里的本地引用要能被找到，且剥掉 query/hash。"""
    html = (
        '<html><head><link rel="stylesheet" href="style.css?v=2"></head>'
        '<body><img src="./img/logo.png#top"><a href="#anchor">x</a>'
        '<a href="https://example.com/a.css">外链</a></body></html>'
    )
    assert _local_refs(html) == ["style.css", "./img/logo.png"]


def test_inline_template_expression_is_skipped() -> None:
    """直接写在标签属性里的模板表达式同样跳过（运行期才成形，不是静态引用）。"""
    assert _local_refs('<html><body><img src="${item.url}"></body></html>') == []


def test_missing_referenced_asset_fails_integrity(tmp_path: Path) -> None:
    """入口引用了不存在的 style.css → 判不完整（这是 multi 在复杂需求上翻车的形状）。"""
    _write(tmp_path / "index.html", '<html><head><link href="style.css" rel="stylesheet">'
                                    "</head><body>ok</body></html>")
    ok, failures, missing = _check_integrity(
        tmp_path, ["index.html"], {"index.html": (tmp_path / "index.html").stat().st_size}
    )
    assert ok is False
    assert missing == ["style.css"]
    assert any("本地资源缺失" in failure for failure in failures)


def test_complete_artifact_passes_integrity(tmp_path: Path) -> None:
    """三件套齐全、引用都在 → 判完整（入口要超过 200 字节的下限，主体因此塞了点内容）。"""
    _write(tmp_path / "style.css", "body{color:red}" * 20)
    _write(tmp_path / "script.js", "console.log(1);" * 20)
    _write(
        tmp_path / "index.html",
        '<html><head><link href="style.css" rel="stylesheet"></head>'
        "<body>" + "hello world " * 30 + "</body><script src=\"script.js\"></script></html>",
    )
    files = ["index.html", "style.css", "script.js"]
    sizes = {name: (tmp_path / name).stat().st_size for name in files}
    ok, failures, missing = _check_integrity(tmp_path, files, sizes)
    assert (ok, failures, missing) == (True, [], [])


def test_declared_but_not_written_file_fails_integrity(tmp_path: Path) -> None:
    """清单声明了却没落盘（sizes 为 -1）→ 判不完整，且指名道姓。"""
    _write(tmp_path / "index.html", "<html><body>" + "x" * 300 + "</body></html>")
    ok, failures, _ = _check_integrity(
        tmp_path, ["index.html", "style.css"], {"index.html": 320, "style.css": -1}
    )
    assert ok is False
    assert any("style.css 未落盘" in failure for failure in failures)


def test_truncated_html_fails_integrity(tmp_path: Path) -> None:
    """输出被 token 上限截断（没有 </html>）→ 判不完整。"""
    _write(tmp_path / "index.html", "<html><body>" + "x" * 300)
    ok, failures, _ = _check_integrity(
        tmp_path, ["index.html"], {"index.html": (tmp_path / "index.html").stat().st_size}
    )
    assert ok is False
    assert any("截断" in failure for failure in failures)


def test_missing_entry_file_fails_integrity(tmp_path: Path) -> None:
    """没有入口文件就无法预览 → 判不完整。"""
    _write(tmp_path / "style.css", "body{}")
    ok, failures, _ = _check_integrity(tmp_path, ["style.css"], {"style.css": 6})
    assert ok is False
    assert any("入口文件" in failure for failure in failures)


def test_ref_escaping_artifact_dir_is_flagged(tmp_path: Path) -> None:
    """引用越出产物目录（../secret）→ 单独标注，不当成普通缺失。"""
    _write(tmp_path / "index.html", '<html><body><script src="../x.js"></script>'
                                    + "y" * 300 + "</body></html>")
    _, _, missing = _check_integrity(
        tmp_path, ["index.html"], {"index.html": (tmp_path / "index.html").stat().st_size}
    )
    assert missing == ["../x.js（越出产物目录）"]
