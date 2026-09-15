# app/api/agent.py —— Agent 对话模块的 HTTP 路由（表现层）
# 职责：收请求 → 调 service → 返回响应；不写业务规则、不写 SQL、不写 prompt
#
# ⚠️ 本模块的数据源是 **PG**（会话 / 消息都在 Agent 域），
# 所以注入的是 `get_pg_db` 而不是 `get_mysql_db` —— 传错 session 会直接 ProgrammingError
# （阶段 1 的 check_pg.py 就是在验收这件事）。

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.pg_db import get_pg_db
from app.models.user import User
from app.schemas.agent_schemas import (
    AgentChatRequest,
    AgentChatResponse,
    AgentSessionDetailResponse,
)
from app.services.agent_chat_service import AgentChatService
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
