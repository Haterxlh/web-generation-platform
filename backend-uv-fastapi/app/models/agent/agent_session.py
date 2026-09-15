# app/models/agent/agent_session.py —— agent_session 表的 ORM 实体（PostgreSQL 侧）
#
# ⚠️ 本文件只 import PgBase。PG 侧与 MySQL 侧的 declarative Base 必须严格隔离，
#    否则 create_all 会「在错误的库里建表且不报错」。详见 app/core/pg_db.py 顶部注释。

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.pg_db import PgBase


class AgentSession(PgBase):
    """对话会话实体，对应 PG 里的 ``agent_session`` 表。

    一个会话 = 用户与 Agent 的一段连续对话（阶段 2 起使用）。
    它承载了「跨轮次复用」这件事：用户在 chat-agent 里澄清完需求，
    下一轮触发生成时，需求草稿 / 文档 digest / 规划结果都挂在这个会话上。
    """

    __tablename__ = "agent_session"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键 id"
    )

    # 对外标识：与生成模块的 task_uuid 同一个套路，不对外暴露自增 id
    session_uuid: Mapped[str] = mapped_column(
        String(64), unique=True, comment="会话唯一标识(uuid4.hex)"
    )

    # 所属用户：指向 **MySQL 的 user 表**，但跨库不建外键、不 JOIN，只存 id（§3.4 铁律）
    user_id: Mapped[int] = mapped_column(
        BigInteger, index=True, comment="所属用户 id(MySQL user 表，跨库不 JOIN)"
    )

    title: Mapped[str | None] = mapped_column(
        String(255), comment="会话标题(取首条用户消息前若干字)"
    )

    status: Mapped[str] = mapped_column(
        String(16), server_default=text("'active'"), comment="状态:active/archived"
    )

    current_stage: Mapped[str | None] = mapped_column(
        String(16), comment="本会话最近一次生成的 Agent 阶段(取值同 agents/stages.py)"
    )

    # 时间列统一用 timestamptz（PG 侧的惯例）：避免"这串时间到底是本地时间还是 UTC"的歧义。
    # ⚠️ 因此 Python 侧写入时要带时区（datetime.now(timezone.utc)），
    #    传 naive datetime 会被 PG 按会话时区解释。
    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), comment="更新时间"
    )

    # 逻辑删除沿用项目统一约定（0/1，而不是 PG 的 boolean）：
    # 让两个库的查询写法一致（都写 is_delete == 0），少一条要记的规则
    is_delete: Mapped[int] = mapped_column(
        SmallInteger, server_default=text("0"), comment="是否删除:0否 1是"
    )
