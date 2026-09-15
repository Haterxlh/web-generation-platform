# app/repositories/agent/message_repository.py —— 消息表的数据库操作（PG 侧）

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.agent import AgentMessage


class AgentMessageRepository:
    """把 agent_message 表的所有数据库操作集中在这里，方法名即语义。"""

    @staticmethod
    def create(db: Session, message: AgentMessage) -> AgentMessage:
        """新增消息并返回。

        Args:
            db: PG 数据库会话。
            message: 尚未入库的消息对象。

        Returns:
            入库后的消息对象。

        Raises:
            ValueError: 传入的对象已经入库。
        """
        if message.id is not None:
            raise ValueError("create() 只接受尚未入库的对象")
        db.add(message)
        db.commit()
        db.refresh(message)
        return message

    @staticmethod
    def recent_by_session(db: Session, session_id: int, limit: int = 50) -> list[AgentMessage]:
        """取某会话**最近** limit 条消息，返回顺序为 **新 → 旧**。

        为什么刻意返回"新 → 旧"：调用方（service）要按 token 预算**从最近往前累加**，
        超预算就停。如果这里返回旧→新，调用方就得先全取出来再倒着走，反而更绕。

        Args:
            db: PG 数据库会话。
            session_id: 会话 id。
            limit: 最多取多少条。

        Returns:
            消息列表（新 → 旧）。
        """
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id, AgentMessage.is_delete == 0)
            .order_by(AgentMessage.id.desc())
            .limit(limit)
        )
        return list(db.scalars(stmt))

    @staticmethod
    def list_by_session(db: Session, session_id: int, limit: int = 200) -> list[AgentMessage]:
        """按时间顺序（旧 → 新）取某会话的消息，用于历史回放。

        Args:
            db: PG 数据库会话。
            session_id: 会话 id。
            limit: 最多取多少条。

        Returns:
            消息列表（旧 → 新）。
        """
        stmt = (
            select(AgentMessage)
            .where(AgentMessage.session_id == session_id, AgentMessage.is_delete == 0)
            .order_by(AgentMessage.id.asc())
            .limit(limit)
        )
        return list(db.scalars(stmt))

    @staticmethod
    def count_by_session(db: Session, session_id: int) -> int:
        """统计某会话的消息条数。

        Args:
            db: PG 数据库会话。
            session_id: 会话 id。

        Returns:
            条数。
        """
        stmt = (
            select(func.count())
            .select_from(AgentMessage)
            .where(AgentMessage.session_id == session_id, AgentMessage.is_delete == 0)
        )
        return db.scalar(stmt) or 0
