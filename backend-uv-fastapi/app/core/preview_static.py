# app/core/preview_static.py —— 带鉴权的静态预览
# 为什么放 core：这是一个 ASGI 层的基础组件（类似 mysql_db.py 的 engine），不属于业务

from http.cookies import SimpleCookie

from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles

from app.utils.jwt.security import decode_preview_ticket

# Cookie 名（签发方 api/generation.py 也要用它，所以导出成常量，避免两边写不一致）
PREVIEW_COOKIE_NAME = "wgp_preview"


class PreviewStaticFiles(StaticFiles):
    """预览静态资源：请求必须携带"该任务"的预览票据 Cookie。

    为什么用 Cookie 而不是 Authorization 头：
    浏览器加载 ``<link href="style.css">`` / ``<script src="script.js">`` 这类**子资源**时，
    不会携带前端自定义的 Authorization 头 —— 只有 Cookie 会被自动带上。
    如果这里只认 Bearer 头，结果是"页面能打开、样式和脚本全 403"。

    Cookie 的 path 被签发方限制成 ``/preview/{user_id}/{task_uuid}/``，
    所以票据只会在这一次任务的预览路径下被浏览器发送，不会泄漏到别的接口。
    """

    async def __call__(self, scope, receive, send) -> None:
        """先校验票据，通过后再交给 StaticFiles 处理。"""
        if scope["type"] != "http":
            # lifespan / websocket 之类直接放行（StaticFiles 自己会处理）
            await super().__call__(scope, receive, send)
            return

        if not self._authorized(scope):
            response = PlainTextResponse("预览未授权或票据已过期，请重新打开预览", status_code=403)
            await response(scope, receive, send)
            return

        await super().__call__(scope, receive, send)

    @staticmethod
    def _authorized(scope) -> bool:
        """校验 Cookie 里的票据，并要求票据绑定的任务与 URL 路径一致。

        Args:
            scope: ASGI scope。

        Returns:
            True 表示放行。
        """
        cookie_header = ""
        for key, value in scope.get("headers", []):
            # scope["headers"] 里的 header 名和值都是 bytes 字节串，不是 Python 的 str 字符串。
            if key == b"cookie":
                cookie_header = value.decode("latin-1")
                break
        if not cookie_header:
            return False

        jar = SimpleCookie()
        jar.load(cookie_header)
        morsel = jar.get(PREVIEW_COOKIE_NAME)
        if morsel is None:
            return False

        # ⚠️ 关键坑：挂载后按 ASGI 规范，scope["path"] 仍然是**完整路径**，
        # 挂载前缀存在 scope["root_path"] 里。必须先剥掉前缀，
        # 才能拿到 "1/<uuid>/index.html" 这样的相对路径。
        root_path = scope.get("root_path") or ""
        path = scope.get("path") or ""
        if root_path and path.startswith(root_path):
            path = path[len(root_path):]
        parts = [part for part in path.split("/") if part]
        if len(parts) < 2:
            return False

        ticket = decode_preview_ticket(morsel.value)
        if ticket is None:
            return False

        # 双重校验：票据里的人 + 票据里的任务，都要和 URL 路径对得上
        return str(ticket.get("sub")) == parts[0] and ticket.get("task_uuid") == parts[1]