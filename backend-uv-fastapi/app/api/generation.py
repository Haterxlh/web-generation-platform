# app/api/generation.py —— 生成模块的 HTTP 路由（表现层）
# 职责：收请求 → 调 service → 返回响应；不写业务规则、不写 SQL、不写 prompt

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.preview_static import PREVIEW_COOKIE_NAME
from app.core.storage_config import storage_settings
from app.core.mysql_db import get_mysql_db
from app.core.jwt_config import jwt_settings
from app.models.user import User
from app.schemas.generation_schemas import (
    GenerateAcceptedResponse,
    GenerateRequest,
    GenerationListResponse,
    GenerationTaskResponse,
    PreviewTicketResponse,
)
from app.services.generation_service import GenerationService
from app.utils.jwt.parse_token import get_current_user
from app.utils.jwt.security import create_preview_ticket

# prefix: 本模块所有接口统一挂在 /generation 下（main.py 再叠一层 /api）
# tags:   让 /docs 文档按"生成"分组显示
router = APIRouter(prefix="/generation", tags=["生成"])


@router.post(
    "/create",
    status_code=202,
    response_model=GenerateAcceptedResponse,
    summary="提交一次生成（异步执行，立即返回）",
)
async def create_generation(
    req: GenerateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_mysql_db),
) -> GenerateAcceptedResponse:
    """提交生成任务并**立即返回 202**（不再同步等待结果）。

    真正的生成由独立的 arq worker 进程执行。前端拿到 `task_uuid` 后，
    按 `poll_interval_ms` 轮询 `GET /api/generation/{task_uuid}`：
    用响应里的 `stage_text` / `progress` 显示进度，`status` 变成
    `success` / `failed` 时停止轮询。

    ⚠️ 为什么改成异步：实测复杂需求单次生成已 157 秒；后续叠加意图识别 / 文档解析 /
    规划 / 多轮工具调用会到 3~5 分钟，同步等待必然前端超时。

    Args:
        req: 生成请求（需求 + 类型）。
        current_user: 当前登录用户（由 get_current_user 依赖注入）。
        db: 数据库会话。

    Returns:
        202：任务标识、初始阶段、轮询地址与建议轮询间隔。

    Raises:
        HTTPException: 生成类型未实现 → 501；任务队列（Redis）不可用 → 503。
    """
    return await GenerationService.create(db, current_user.id, req)


# ⚠️ /list 必须声明在 /{task_uuid} 之前：
# FastAPI 按声明顺序匹配，否则 "list" 会被当成一个 task_uuid 吃掉，直接 404
@router.get("/list", response_model=GenerationListResponse, summary="我的生成历史")
def list_generations(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_mysql_db),
) -> GenerationListResponse:
    """分页查询当前用户的生成历史（新的在前）。

    Args:
        page: 页码。
        page_size: 每页条数。
        current_user: 当前登录用户。
        db: 数据库会话。

    Returns:
        总数 + 当前页数据。
    """
    return GenerationService.list_tasks(db, current_user.id, page=page, page_size=page_size)


@router.post(
    "/{task_uuid}/preview-ticket",
    response_model=PreviewTicketResponse,
    summary="签发预览票据（写 HttpOnly Cookie）",
)
def issue_preview_ticket(
    task_uuid: str,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_mysql_db),
) -> PreviewTicketResponse:
    """为某个任务的预览签发短期票据，并通过 HttpOnly Cookie 下发。

    前端流程：先调本接口（带 Bearer token），拿到 preview_url 后在新标签页打开。
    浏览器随后加载的 index.html / style.css / script.js 都会自动带上这个 Cookie。

    Args:
        task_uuid: 任务唯一标识。
        response: 用于写 Set-Cookie。
        current_user: 当前登录用户。
        db: 数据库会话。

    Returns:
        预览地址 + 有效期（秒）。

    Raises:
        HTTPException: 任务不存在或不属于当前用户 → 404；任务没有产物 → 400。
    """
    # 复用 get_task 里的归属校验（"谁能看"这条规则只写一处）
    task = GenerationService.get_task(db, current_user.id, task_uuid)
    if not task.preview_url:
        raise HTTPException(status_code=400, detail="该任务没有产物，无法预览")

    response.set_cookie(
        key=PREVIEW_COOKIE_NAME,
        value=create_preview_ticket(current_user.id, task_uuid),
        max_age=jwt_settings.preview_ticket_minutes * 60,
        # path 锁死在本次任务的预览目录：浏览器只会把票据发给这个路径
        path=f"{storage_settings.preview_prefix}/{current_user.id}/{task_uuid}/",
        httponly=True,   # JS 读不到，降低 XSS 偷票据的风险
        samesite="lax",
        secure=False,    # 本地开发是 http；上生产（https）必须改成 True
    )
    return PreviewTicketResponse(
        preview_url=task.preview_url,
        expires_in=jwt_settings.preview_ticket_minutes * 60,
    )


@router.get("/{task_uuid}", response_model=GenerationTaskResponse, summary="查询生成任务详情")
def get_generation(
    task_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_mysql_db),
) -> GenerationTaskResponse:
    """查询单个生成任务详情（只能查自己的），同时充当**进度轮询接口**。

    因为生成已改为异步（202 + 队列），前端提交后靠本接口轮询：
    看 `stage` / `stage_text` / `progress` 显示进度，看 `status` 判断是否结束。

    Args:
        task_uuid: 任务唯一标识。
        current_user: 当前登录用户。
        db: 数据库会话。

    Returns:
        任务详情。
    """
    return GenerationService.get_task(db, current_user.id, task_uuid)
