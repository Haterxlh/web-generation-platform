# app/models/agent/agent_message.py —— agent_message 表的 ORM 实体（PostgreSQL 侧）
#
# ⚠️ 本文件只 import PgBase（隔离规则见 app/core/pg_db.py 顶部注释）。

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, SmallInteger, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.pg_db import PgBase


class AgentMessage(PgBase):
    """对话消息实体，对应 PG 里的 ``agent_message`` 表。

    ⚠️ 关于附件的核心约定（见 `docs/agent_refactor_plan.md` §3.6）：

    - ``content`` 存**含别名的原文**（如 ``"用 @doc1 的风格重做"``），
      **绝不存文件正文、绝不存磁盘路径**；
    - 文件由 ``attachments`` 里的别名引用，别名指向 ``generation_source.source_uuid``；
    - 真正"展开成摘要"只发生在拼 prompt 的时候，而且是几百字的 digest，不是全文。

    这样做的收益：prompt 短（省 token）、不泄露路径、文件可重新解析而历史消息无需改写。
    """

    __tablename__ = "agent_message"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键 id"
    )

    message_uuid: Mapped[str] = mapped_column(
        String(64), unique=True, comment="消息唯一标识(uuid4.hex)"
    )

    # 指向 agent_session.id（PG 库内），但沿用项目"不建外键、只加索引"的既有做法
    session_id: Mapped[int] = mapped_column(
        BigInteger, index=True, comment="所属会话 id(agent_session.id)"
    )

    role: Mapped[str] = mapped_column(
        String(16), comment="角色:user/assistant/system/tool"
    )

    # 可能很长（用户粘贴一大段需求、模型回一大段澄清），用 Text
    content: Mapped[str] = mapped_column(
        Text, comment="消息正文(含 @docN 别名原文，不存文件正文/磁盘路径)"
    )

    # 附件引用：[{alias, source_uuid, role, display_name}]
    # 用 JSONB 而不是再开一张关联表：它只在"读消息"时整体取出，不需要按附件反查消息
    attachments: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, comment="附件引用[{alias,source_uuid,role,display_name}]"
    )

    intent: Mapped[str | None] = mapped_column(
        String(16), comment="意图识别结果:chat/generate"
    )

    # 需求槽位：{site_kind, features, audience, style, need_persistence, ...}
    slots: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="需求槽位(缺哪些槽位决定要不要继续澄清)"
    )

    # 是否值得进向量库（二期 RAG 用；阶段 2 起由 chat-agent 标记）。
    # 现在就建列是为了避免二期再改一次表：不是每句都该入向量库，
    # 否则"你好""谢谢"会把检索结果灌满（§5 硬约束 13）。
    is_memorable: Mapped[int] = mapped_column(
        SmallInteger, server_default=text("0"), comment="是否值得进向量库(二期 RAG 用)"
    )

    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )

    is_delete: Mapped[int] = mapped_column(
        SmallInteger, server_default=text("0"), comment="是否删除:0否 1是"
    )
