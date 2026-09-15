# app/repositories/agent/session_repository.py —— 会话表的数据库操作（PG 侧）
#
# 约定与 user/generation 两个 repository 一致：只做 CRUD，查询一律过滤 is_delete == 0。

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import AgentSession


class AgentSessionRepository:
    """把 agent_session 表的所有数据库操作集中在这里，方法名即语义。"""

    @staticmethod
    def create(db: Session, session: AgentSession) -> AgentSession:
        """新增会话并返回（带数据库生成的主键与时间列）。

        Args:
            db: PG 数据库会话。
            session: 尚未入库的会话对象。

        Returns:
            入库后的会话对象。

        Raises:
            ValueError: 传入的对象已经入库。
        """
        if session.id is not None:
            raise ValueError("create() 只接受尚未入库的对象；更新请用 update()")
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def update(db: Session, session: AgentSession) -> AgentSession:
        """提交对会话对象的修改。

        Args:
            db: PG 数据库会话。
            session: 已修改的会话对象。

        Returns:
            更新后的会话对象。

        Raises:
            ValueError: 传入的对象尚未入库。
        """
        if session.id is None:
            raise ValueError("update() 只接受已入库的对象；新增请用 create()")
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    @staticmethod
    def get_by_uuid(db: Session, session_uuid: str) -> AgentSession | None:
        """按 session_uuid 查会话（未删除）。

        Args:
            db: PG 数据库会话。
            session_uuid: 会话唯一标识。

        Returns:
            会话对象；不存在则 None。
        """
        stmt = select(AgentSession).where(
            AgentSession.session_uuid == session_uuid,
            AgentSession.is_delete == 0,
        )
        return db.scalar(stmt)
