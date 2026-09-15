# app/main.py —— FastAPI 应用入口：负责创建 app、登记路由、管理进程级资源

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.agent import router as agent_router
from app.api.generation import router as generation_router
from app.api.user import router as user_router
from app.core.arq_pool import close_pool, init_pool
from app.core.storage_config import storage_settings
from app.core.preview_static import PreviewStaticFiles

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时建 arq 连接池，关闭时释放。

    为什么必须显式建池、不能每请求建一次：
    ``POST /api/generation/create`` 要往 Redis 队列投任务，而建池是**异步**操作、
    只能在事件循环里做一次；每请求建一次既慢又浪费连接。

    ⚠️ 刻意不让"Redis 不可用"导致应用起不来：
    队列挂了不该连查看历史、预览产物都跟着用不了。
    此处的失败只记日志；投递任务时 ``init_pool()`` 会再试一次（它幂等可重试），
    仍然失败则由 create 接口返回 503。

    Args:
        app: FastAPI 应用实例。

    Yields:
        None（请求处理期间）。
    """
    try:
        await init_pool()
    except Exception as error:  # noqa: BLE001 —— 故意兜住所有连接异常，避免启动即崩
        logger.error("arq 连接池初始化失败（生成接口将不可用，其余接口正常）：%s", error)
    yield
    await close_pool()


app = FastAPI(
    title="Web 生成平台",
    description="Web 生成平台后端 API",
    lifespan=lifespan,
)

# 登记用户模块路由：prefix=/api 叠加路由自身的 /user
# → /api/user/register、/api/user/login、/api/user/current
app.include_router(user_router, prefix="/api")

# 登记生成模块路由
# → /api/generation/create（202 异步提交）、/list、/{task_uuid}、/{task_uuid}/preview-ticket
app.include_router(generation_router, prefix="/api")

# 登记 Agent 对话模块路由 → /api/agent/chat、/api/agent/session/{session_uuid}
app.include_router(agent_router, prefix="/api")

# 把产物目录挂成静态资源：/preview/1/<task_uuid>/index.html 可直接在浏览器打开
# 坑 1：目录必须已存在，否则 StaticFiles 在【启动时】就报错（不是等请求来了才报）
# 坑 2：html=True 让 /preview/1/<uuid>/ 也能命中目录下的 index.html（否则必须写全文件名）
generated_path = storage_settings.generated_path
generated_path.mkdir(parents=True, exist_ok=True)
app.mount(
    storage_settings.preview_prefix,
    PreviewStaticFiles(directory=generated_path, html=True),
    name="preview",
)
