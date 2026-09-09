# app/main.py —— FastAPI 应用入口：负责创建 app 并登记所有路由模块

from fastapi import FastAPI

from app.api.user import router as user_router

app = FastAPI(title="Web 生成平台", description="Web 生成平台后端 API")

# 登记用户模块路由：prefix=/api 叠加路由自身的 /users
# → 实际访问路径为 /api/users/register、/api/users/login、/api/users/current
app.include_router(user_router, prefix="/api")