# 保存为 app/utils/utils_check/check_preview_auth.py 后运行：
# uv run python -m app.utils.utils_check.check_preview_auth
import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.preview_static import PREVIEW_COOKIE_NAME, PreviewStaticFiles
from app.utils.jwt.security import create_preview_ticket

ROOT = Path("generated") / "_preview_authtest"


def main() -> None:
    """造两个任务的产物，验证 Cookie 票据的 8 种情形。"""
    shutil.rmtree(ROOT, ignore_errors=True)
    for task, title in (("taskA", "A"), ("taskB", "B")):
        (ROOT / "1" / task).mkdir(parents=True)
        (ROOT / "1" / task / "index.html").write_text(f"<h1>{title}</h1>", encoding="utf-8")
        (ROOT / "1" / task / "style.css").write_text("body{}", encoding="utf-8")

    app = FastAPI()
    app.mount("/preview", PreviewStaticFiles(directory=ROOT, html=True), name="preview")

    def client_with(cookie: str | None = None, cookie_path: str = "/") -> TestClient:
        client = TestClient(app)
        if cookie is not None:
            client.cookies.set(PREVIEW_COOKIE_NAME, cookie, path=cookie_path)
        return client

    ok = client_with(create_preview_ticket(1, "taskA"), "/preview/1/taskA/")
    print("① 不带 Cookie                 ->", client_with().get("/preview/1/taskA/index.html").status_code)
    print("② Cookie 乱码                 ->", client_with("garbage", "/preview/1/taskA/").get("/preview/1/taskA/index.html").status_code)
    print("③ 合法票据 + index.html       ->", ok.get("/preview/1/taskA/index.html").status_code)
    print("④ 合法票据 + style.css(子资源)->", ok.get("/preview/1/taskA/style.css").status_code)
    print("⑤ 合法票据 + 目录首页         ->", ok.get("/preview/1/taskA/").status_code)
    print("⑥ 合法票据 + 访问别人任务     ->", ok.get("/preview/1/taskB/index.html").status_code)
    print("⑦ B 的票据放到 A 的路径下     ->", client_with(create_preview_ticket(1, "taskB"), "/preview/1/taskA/").get("/preview/1/taskA/index.html").status_code)
    print("⑧ 篡改票据                    ->", client_with(create_preview_ticket(1, "taskA")[:-1] + "x", "/preview/1/taskA/").get("/preview/1/taskA/index.html").status_code)

    shutil.rmtree(ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()