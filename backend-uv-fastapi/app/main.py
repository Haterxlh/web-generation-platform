# app/main.py —— FastAPI 应用入口：负责创建 app 并登记所有路由模块

from fastapi import FastAPI

from app.api.generation import router as generation_router
from app.api.user import router as user_router
from app.core.storage_config import storage_settings
from app.core.preview_static import PreviewStaticFiles


app = FastAPI(title="Web 生成平台", description="Web 生成平台后端 API")

# 登记用户模块路由：prefix=/api 叠加路由自身的 /user
# → /api/user/register、/api/user/login、/api/user/current
app.include_router(user_router, prefix="/api")

# 登记生成模块路由
# → /api/generation/create、/api/generation/list、/api/generation/{task_uuid}
app.include_router(generation_router, prefix="/api")

# 把产物目录挂成静态资源：/preview/1/<task_uuid>/index.html 可直接在浏览器打开
# 坑 1：目录必须已存在，否则 StaticFiles 在【启动时】就报错（不是等请求来了才报）
# 坑 2：html=True 让 /preview/1/<uuid>/ 也能命中目录下的 index.html（否则必须写全文件名）
generated_path = storage_settings.generated_path
generated_path.mkdir(parents=True, exist_ok=True)
app.mount(
    storage_settings.preview_prefix,
    PreviewStaticFiles(directory=generated_path, html=True),   # ← 只改这一处
    name="preview",
)