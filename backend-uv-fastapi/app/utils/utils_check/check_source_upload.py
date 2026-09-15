# app/utils/utils_check/check_source_upload.py —— 开发期自检：文档解析与附件上传
#
# 运行（在 backend-uv-fastapi 下，必须用 -m 让 app 包可导入）：
#   uv run python -m app.utils.utils_check.check_source_upload
#
# 两段，代价差别很大，所以分开：
#
#   第一段（离线）：解析层自检 —— 不连模型、不连库、不起 HTTP。
#        覆盖 GBK 编码回退、Markdown 结构保留、HTML 设计令牌、扫描版 PDF 报错这四类真实场景。
#        这一段随时可跑，是排查"上传解析"问题的第一现场。
#
#   第二段（在线，⚠️ 真实调用大模型并按 token 计费；需要 API 在跑，不可用时自动跳过）：
#        /api/agent/source/upload 走一遍 —— 分配 @docN、角色否决（.md 不能当风格源）、
#        HTML 的设计令牌进入 digest、附件列表可查。

import shutil
import sys
import time
import uuid
from pathlib import Path

import httpx

from app.core.storage_config import BASE_DIR
from app.utils.agent.source_store import save_upload
from app.utils.doc import DocParseError, parse_document

API_BASE = "http://127.0.0.1:8000/api"
PASSWORD = "probe123456"

# ⚠️ Windows 中文控制台的默认编码是 GBK：打印 ⚠️ / ✅ 这类字符会直接
# UnicodeEncodeError 让脚本崩掉（本脚本实测踩到）。切到 UTF-8 并容错，
# 宁可个别符号显示成问号，也不能让自检因为"打印不出来"而失败。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 自检产生的临时文件放 tmps/（根 .gitignore 已忽略），跑完即删
WORK_DIR = BASE_DIR / "tmps" / "source_check"

SAMPLE_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
  body { background: #0f172a; color: #e2e8f0; font-family: "Inter", sans-serif; font-size: 16px; }
  .card { border-radius: 8px; padding: 24px; display: grid; }
  @media (max-width: 640px) { .card { display: block; } }
</style></head>
<body><header id="top" class="site-header"><h1>企业官网设计规范</h1></header>
<main><section id="hero" class="card"><p>主色用于行动按钮。</p></section></main></body></html>
"""

SAMPLE_MD = """# 需求说明

## 功能列表

| 功能 | 说明 |
| --- | --- |
| 添加 | 支持回车提交 |
| 删除 | 二次确认 |

```js
// # 这行在代码块里，不是标题
console.log(1)
```

## 约束

- 必须响应式
- 数据存 localStorage
"""


def _write(name: str, data: bytes) -> Path:
    """把样例文件写到自检目录。"""
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    path = WORK_DIR / name
    path.write_bytes(data)
    return path


def _blank_pdf() -> bytes:
    """造一个没有文本层的 PDF（模拟扫描件）。"""
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _text_pdf(pages: list[str]) -> bytes:
    """手写一个**带文本层**的多页 PDF（真实字节，供 pypdf 解析）。

    为什么不用库生成：pypdf 只会读，造"含文字的 PDF"需要 reportlab 这类重依赖，
    不值得为一次自检引入。这里按 PDF 语法手写对象并算好 xref 偏移。
    （测试里有一份等价实现：`tests/test_doc_parsers.py` 的 `_build_text_pdf`。
    两边不共享代码是刻意的 —— 自检脚本不该 import 测试代码。）
    """
    contents = [
        f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1") for text in pages
    ]
    kids = " ".join(f"{4 + index * 2} 0 R" for index in range(len(pages)))
    bodies: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, content in enumerate(contents):
        page_number = 4 + index * 2
        stream_number = page_number + 1
        bodies.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {stream_number} 0 R >>".encode()
        )
        bodies.append(
            b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_pos = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


def check_parsers() -> bool:
    """第一段：解析层（无 LLM）。"""
    print("=" * 74)
    print("1) 解析层自检（离线，无模型）")

    failures: list[str] = []

    # ① GBK 的中文 .txt（中文 Windows 记事本的默认编码）
    gbk_text = "需求说明：做一个带筛选的待办清单，风格极简白底。"
    gbk_path = _write("gbk.txt", gbk_text.encode("gb18030"))
    try:
        doc = parse_document(gbk_path)
        ok = gbk_text in doc.text and doc.encoding == "gb18030"
        print(f"   [GBK .txt]      编码={doc.encoding} 正文前 20 字={doc.text[:20]!r} -> {'通过' if ok else '失败'}")
        if not ok:
            failures.append("GBK .txt 未被正确解码（是不是回退到 errors='ignore' 了？）")
    except DocParseError as error:
        failures.append(f"GBK .txt 解析报错：{error}")

    # ② 无法识别的编码（0xFF 在 UTF-8 与 GB18030 里都非法）
    try:
        parse_document(_write("broken.txt", b"\xff\xff\xff\xff"))
        failures.append("无法识别的编码竟然解析成功了 —— 应当明确报错")
        print("   [坏编码 .txt]   竟然通过 -> 失败")
    except DocParseError as error:
        print(f"   [坏编码 .txt]   按预期报错：{str(error)[:46]}… -> 通过")

    # ③ Markdown：结构保留 + 标题骨架（代码块里的 # 不算标题）
    md_path = _write("需求.md", SAMPLE_MD.encode("utf-8"))
    doc = parse_document(md_path)
    md_ok = (
        doc.skeleton == ["# 需求说明", "## 功能列表", "## 约束"]
        and "| 添加 | 支持回车提交 |" in doc.text
        and "console.log(1)" in doc.text
    )
    print(f"   [.md 结构]      骨架={doc.skeleton} -> {'通过' if md_ok else '失败'}")
    if not md_ok:
        failures.append(".md 的结构/骨架抽取不符合预期")

    # ④ HTML：正文不混脚本、设计令牌齐全
    html_path = _write("spec.html", SAMPLE_HTML.encode("utf-8"))
    doc = parse_document(html_path)
    tokens = doc.design_tokens
    html_ok = (
        doc.can_be_style_source()
        and "#0f172a" in tokens.get("colors", [])
        and "Inter" in tokens.get("font_families", [])
        and "8px" in tokens.get("border_radius", [])
        and tokens.get("layout", {}).get("@media") == 1
    )
    print(f"   [HTML 设计令牌] colors={tokens.get('colors')} fonts={tokens.get('font_families')}")
    print(f"                   radius={tokens.get('border_radius')} layout={tokens.get('layout')}")
    print(f"                   骨架={doc.skeleton[:3]}")
    print(f"                   -> {'通过' if html_ok else '失败'}")
    if not html_ok:
        failures.append("HTML 设计令牌抽取不完整（风格源就靠它）")

    # ⑤ 扫描版 PDF：必须报错，不能"成功"返回空需求
    scan_path = _write("scan.pdf", _blank_pdf())
    try:
        parse_document(scan_path)
        failures.append("扫描版 PDF 竟然解析成功 —— 会产生空需求让模型瞎编")
        print("   [扫描版 .pdf]   竟然通过 -> 失败")
    except DocParseError as error:
        print(f"   [扫描版 .pdf]   按预期报错：{str(error)[:46]}… -> 通过")

    # ⑥ 落盘：GBK 字节必须原样保存（回退判据就是原始字节）
    relative, stored = save_upload(0, uuid.uuid4().hex, "需求.txt", gbk_text.encode("gb18030"))
    disk_ok = stored.read_bytes() == gbk_text.encode("gb18030")
    print(f"   [落盘字节]      {relative} -> {'通过' if disk_ok else '失败'}")
    if not disk_ok:
        failures.append("上传落盘时字节被改动了（编码回退会因此失效）")

    if failures:
        print("   结论   -> 不通过：")
        for item in failures:
            print(f"             · {item}")
        return False
    print("   结论   -> 通过")
    return True


def check_http_upload() -> bool:
    """第二段：真实 HTTP 上传（需要 API 在跑；会真实调用模型）。"""
    print("=" * 74)
    print("2) 附件上传 HTTP 全链路（⚠️ 真实模型；需要 API 在跑）")

    try:
        with httpx.Client(timeout=180) as client:
            account = f"src{uuid.uuid4().hex[:10]}"
            client.post(
                f"{API_BASE}/user/register",
                json={"user_account": account, "user_password": PASSWORD},
            ).raise_for_status()
            token = client.post(
                f"{API_BASE}/user/login",
                json={"user_account": account, "user_password": PASSWORD},
            ).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            # 先建会话（别名的作用域是会话）
            chat = client.post(
                f"{API_BASE}/agent/chat",
                headers=headers,
                json={"message": "我想做个企业官网，先看看我上传的设计规范"},
            )
            chat.raise_for_status()
            session_uuid = chat.json()["session_uuid"]
            print(f"   会话     -> {session_uuid}")

            problems: list[str] = []

            # ① 上传 Markdown（需求说明书）—— 角色必须被否决为 content
            started = time.perf_counter()
            md_resp = client.post(
                f"{API_BASE}/agent/source/upload",
                headers=headers,
                data={"session_uuid": session_uuid},
                files={"file": ("需求说明.md", SAMPLE_MD.encode("utf-8"), "text/markdown")},
            )
            md_resp.raise_for_status()
            md = md_resp.json()
            print(f"   ① .md    -> alias={md['alias']} role={md['role']} status={md['parse_status']}"
                  f"（{time.perf_counter() - started:.1f}s）")
            print(f"              摘要: {md['digest']['summary'] if md['digest'] else '（无）'}")
            print(f"              硬要求: {md['digest']['constraints'] if md['digest'] else []}")
            print(f"              警告: {md['warnings']}")
            if md["alias"] != "@doc1":
                problems.append(f"第一个附件应为 @doc1，实得 {md['alias']}")
            if md["role"] != "content":
                problems.append(f".md 的角色应为 content（无设计令牌），实得 {md['role']}")
            if md["parse_status"] != "success":
                problems.append(f".md 解析失败：{md['parse_error']}")

            # ② 上传 HTML（设计规范）—— 允许 style/both，且设计令牌来自解析器
            html_resp = client.post(
                f"{API_BASE}/agent/source/upload",
                headers=headers,
                data={"session_uuid": session_uuid},
                files={"file": ("设计规范.html", SAMPLE_HTML.encode("utf-8"), "text/html")},
            )
            html_resp.raise_for_status()
            html = html_resp.json()
            print(f"   ② .html  -> alias={html['alias']} role={html['role']} status={html['parse_status']}")
            print(f"              配色: {html['digest']['style_spec']['colors'] if html['digest'] else []}")
            print(f"              字体: {html['digest']['style_spec']['font_families'] if html['digest'] else []}")
            if html["alias"] != "@doc2":
                problems.append(f"第二个附件应为 @doc2，实得 {html['alias']}")
            if html["role"] not in ("style", "both"):
                problems.append(f"含设计令牌的 HTML 应可作风格源，实得 {html['role']}")
            if html["digest"] and "#0f172a" not in html["digest"]["style_spec"]["colors"]:
                problems.append("HTML 的配色没有来自解析器（模型转述会失真）")

            # ③ 列表接口：别名 chip 的数据来源
            listing = client.get(
                f"{API_BASE}/agent/source/list",
                headers=headers,
                params={"session_uuid": session_uuid},
            )
            listing.raise_for_status()
            items = listing.json()
            print(f"   ③ 列表   -> {[(item['alias'], item['display_name'], item['role']) for item in items]}")
            if len(items) != 2:
                problems.append(f"附件列表应有 2 条，实得 {len(items)}")

            # ④ 真实 PDF（带文本层、两页）：验收"真实 PDF 能跑通"
            pdf_bytes = _text_pdf(
                ["Requirements: build a todo list web page.", "Constraint: responsive layout."]
            )
            pdf_resp = client.post(
                f"{API_BASE}/agent/source/upload",
                headers=headers,
                data={"session_uuid": session_uuid},
                files={"file": ("specs.pdf", pdf_bytes, "application/pdf")},
            )
            pdf_resp.raise_for_status()
            pdf = pdf_resp.json()
            print(f"   ④ PDF    -> alias={pdf['alias']} status={pdf['parse_status']}"
                  f" 摘要={(pdf['digest']['summary'] if pdf['digest'] else '（无）')[:40]}")
            if pdf["parse_status"] != "success":
                problems.append(f"带文本层的 PDF 应解析成功，实得 {pdf['parse_status']}：{pdf['parse_error']}")
            if pdf["role"] != "content":
                problems.append(f"PDF 抽不出设计令牌，角色应为 content，实得 {pdf['role']}")

            # ⑤ GBK 编码的中文 .txt：验收编码回退
            gbk_resp = client.post(
                f"{API_BASE}/agent/source/upload",
                headers=headers,
                data={"session_uuid": session_uuid},
                files={"file": ("中文需求.txt", "需求：做一个带筛选的待办清单。".encode("gb18030"), "text/plain")},
            )
            gbk_resp.raise_for_status()
            gbk = gbk_resp.json()
            print(f"   ⑤ GBK    -> alias={gbk['alias']} status={gbk['parse_status']}"
                  f" 警告={gbk['warnings']}")
            if gbk["parse_status"] != "success":
                problems.append(f"GBK 的 .txt 应解析成功，实得 {gbk['parse_status']}：{gbk['parse_error']}")

            # ⑥ 坏文件：返回 failed 而不是 5xx
            bad = client.post(
                f"{API_BASE}/agent/source/upload",
                headers=headers,
                data={"session_uuid": session_uuid},
                files={"file": ("scan.pdf", _blank_pdf(), "application/pdf")},
            )
            print(f"   ⑥ 扫描版 -> HTTP {bad.status_code} status={bad.json().get('parse_status')}"
                  f" error={(bad.json().get('parse_error') or '')[:40]}…")
            if bad.status_code != 200:
                problems.append(f"扫描版 PDF 不该是 HTTP {bad.status_code}（应 200 + parse_status=failed）")
            if bad.json().get("parse_status") != "failed":
                problems.append("扫描版 PDF 的 parse_status 应为 failed")

            if problems:
                print("   结论   -> 不通过：")
                for item in problems:
                    print(f"             · {item}")
                return False
            print("   结论   -> 通过")
            return True
    except httpx.HTTPError as error:
        print(f"   跳过   -> API 不可用（{type(error).__name__}），"
              f"先执行 `uv run fastapi dev` 再重跑本段")
        return True  # 不算失败：本段是可选验证


def main() -> int:
    """跑完全部检查，返回进程退出码（0=全通过）。"""
    try:
        results = {
            "解析层（离线）": check_parsers(),
            "上传 HTTP 全链路": check_http_upload(),
        }
    finally:
        # 自检产物不留痕：临时文件与上传样例一并清掉
        shutil.rmtree(WORK_DIR, ignore_errors=True)
        shutil.rmtree(BASE_DIR / "uploads" / "0", ignore_errors=True)

    print("=" * 74)
    for name, passed in results.items():
        print(f"   {'✅' if passed else '❌'} {name}")
    passed_all = all(results.values())
    print(f"\n汇总：{'全部通过' if passed_all else '存在失败项'}")
    return 0 if passed_all else 1


if __name__ == "__main__":
    sys.exit(main())
