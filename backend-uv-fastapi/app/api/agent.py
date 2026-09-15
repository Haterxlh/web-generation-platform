# app/api/agent.py —— Agent 对话模块的 HTTP 路由（表现层）
# 职责：收请求 → 调 service → 返回响应；不写业务规则、不写 SQL、不写 prompt
#
# ⚠️ 本模块的数据源是 **PG**（会话 / 消息都在 Agent 域），
# 所以注入的是 `get_pg_db` 而不是 `get_mysql_db` —— 传错 session 会直接 ProgrammingError
# （阶段 1 的 check_pg.py 就是在验收这件事）。

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.core.pg_db import get_pg_db
from app.models.user import User
from app.schemas.agent_schemas import (
    AgentChatRequest,
    AgentChatResponse,
    AgentSessionDetailResponse,
    SourceOut,
    SourceUploadResponse,
)
from app.services.agent_chat_service import AgentChatService
from app.services.source_service import SourceService
from app.utils.jwt.parse_token import get_current_user

# prefix: 本模块所有接口统一挂在 /agent 下（main.py 再叠一层 /api）
router = APIRouter(prefix="/agent", tags=["Agent 对话"])


@router.post("/chat", response_model=AgentChatResponse, summary="与 Agent 对话（澄清需求）")
def chat(
    req: AgentChatRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_pg_db),
) -> AgentChatResponse:
    """处理一轮对话：意图识别 → 澄清追问 / 需求确认。

    这是**同步**接口（不是 202 异步）：一轮对话只含 1~2 次模型调用，通常 3~8 秒，
    用户本来就在等回复。真正的耗时操作是"生成"，那条路径才走队列（`/api/generation/create`）。

    - 不传 `session_uuid` → 新建会话；响应里带回新的 `session_uuid`，后续轮次传它。
    - 响应里的 `ready_to_generate` 表示需求是否已足够开始生成；
      前端据此决定是否显示"开始生成"。**本接口不会创建生成任务**
      （会话到生成的交接由阶段 6 的编排图负责）。

    Args:
        req: 本轮消息与附件引用。
        current_user: 当前登录用户（由 get_current_user 依赖注入）。
        db: PG 数据库会话。

    Returns:
        助手回复 + 当前槽位 + 是否可生成 + 本轮 token 用量。

    Raises:
        HTTPException: `session_uuid` 不存在或不属于当前用户 → 404。
    """
    return AgentChatService.chat(db, current_user.id, req)


@router.get(
    "/session/{session_uuid}",
    response_model=AgentSessionDetailResponse,
    summary="查询会话详情与历史消息",
)
def get_session(
    session_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_pg_db),
) -> AgentSessionDetailResponse:
    """查询某个会话的详情与全部消息（只能查自己的）。

    用途有二：前端渲染对话历史；以及验证"多轮对话已完整落库、可回放"。

    Args:
        session_uuid: 会话唯一标识。
        current_user: 当前登录用户。
        db: PG 数据库会话。

    Returns:
        会话详情（含消息列表，旧 → 新）。
    """
    return AgentChatService.get_session(db, current_user.id, session_uuid)


@router.post(
    "/source/upload",
    response_model=SourceUploadResponse,
    summary="上传附件（需求文档 / 设计规范）",
)
async def upload_source(
    session_uuid: str = Form(description="归属会话标识（附件别名的作用域是会话）"),
    file: UploadFile = File(description="待上传文件：.pdf / .html / .htm / .md / .txt"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_pg_db),
) -> SourceUploadResponse:
    """上传一个附件：落盘 → 分配别名 ``@docN`` → 解析 → 理解 → 落库。

    **这是同步接口**（可能含多次模型调用）：上传后要把文档理解成需求摘要，
    结果会缓存进库、跨轮复用，所以后续对话不会再为同一个文件重复付费。
    长文档（PDF / 超长 HTML）走分块理解，耗时随篇幅增长；阶段 6 会把它接进异步编排图。

    - `parse_status=success`：解析与理解都完成，`digest` 里有结构化结果；
    - `parse_status=failed`：**不是 HTTP 错误** —— 文件已保存、别名已分配，
      只是读不懂（扫描版 PDF、编码无法识别等），`parse_error` 里是面向用户的原因。

    Args:
        session_uuid: 归属会话标识（必须先有一次 `/agent/chat` 创建的会话）。
        file: 上传的文件。
        current_user: 当前登录用户。
        db: PG 数据库会话。

    Returns:
        上传结果（别名 + 角色 + 解析状态 + 理解结果 + token 用量）。

    Raises:
        HTTPException: 会话不存在 → 404；内容为空 / 超限 / 类型不支持 → 400。
    """
    # UploadFile 由 Starlette 落到 SpooledTemporaryFile：大文件会先落临时盘、不会全量进内存。
    # 体积上限在 service 里统一校验（与解析层同一个常量，避免两处口径漂移）
    data = await file.read()
    return SourceService.upload(
        db,
        current_user.id,
        session_uuid=session_uuid,
        filename=file.filename,
        mime=file.content_type,
        data=data,
    )


@router.get(
    "/source/list",
    response_model=list[SourceOut],
    summary="查询会话下的附件（渲染 @docN chip 用）",
)
def list_sources(
    session_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_pg_db),
) -> list[SourceOut]:
    """列出某个会话下的全部附件。

    消息正文里只存别名（``@doc1``），"这个别名对应哪个文件"的映射在附件表里 ——
    前端要渲染文件名 chip 就得查这里。

    Args:
        session_uuid: 会话标识。
        current_user: 当前登录用户。
        db: PG 数据库会话。

    Returns:
        附件列表（旧 → 新）。
    """
    return SourceService.list_sources(db, current_user.id, session_uuid)
