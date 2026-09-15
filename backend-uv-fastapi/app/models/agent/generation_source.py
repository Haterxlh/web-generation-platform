# app/models/agent/generation_source.py —— generation_source 表的 ORM 实体（PostgreSQL 侧）
#
# ⚠️ 本文件只 import PgBase（隔离规则见 app/core/pg_db.py 顶部注释）。

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.pg_db import PgBase


class GenerationSource(PgBase):
    """内容源 / 风格源实体（用户上传的 PDF、HTML 等），对应 PG 里的 ``generation_source`` 表。

    阶段 3 起使用：上传即入库并分配别名 ``@docN``，解析后把结构化结果写进 ``digest``。

    别名规则（见 `docs/agent_refactor_plan.md` §3.6）：
    - ``alias`` 由**系统生成**（``@doc1`` 形式），不是拿文件名当别名 ——
      文件名会重名、含空格/特殊字符、还可被构造成 `../../etc/passwd`；
    - ``display_name`` 存原文件名，**只用于展示与宽松解析**；
    - 别名**不可重命名、不可复用**，作用域是会话，因此唯一约束建在 ``(session_id, alias)`` 上。
    """

    __tablename__ = "generation_source"

    # 别名在**会话内**唯一。跨会话引用需要显式挂载并分配新别名，
    # 这样就不必让别名全局唯一（那会带来分配与回收的复杂度）
    __table_args__ = (
        UniqueConstraint("session_id", "alias", name="uq_generation_source_session_alias"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键 id"
    )

    source_uuid: Mapped[str] = mapped_column(
        String(64), unique=True, comment="源文件唯一标识(uuid4.hex)"
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, index=True, comment="所属用户 id(MySQL user 表，跨库不 JOIN)"
    )

    session_id: Mapped[int] = mapped_column(
        BigInteger, index=True, comment="所属会话 id(agent_session.id)"
    )

    # 形如 "@doc1"；只允许 [A-Za-z0-9_-]，从格式上就与邮箱（a@b.com）错开
    alias: Mapped[str] = mapped_column(
        String(32), comment="会话内别名(形如 @doc1)"
    )

    display_name: Mapped[str | None] = mapped_column(
        String(255), comment="原始文件名(只用于展示与宽松解析)"
    )

    mime: Mapped[str | None] = mapped_column(String(128), comment="MIME 类型")

    size_bytes: Mapped[int | None] = mapped_column(BigInteger, comment="文件大小(字节)")

    # content=内容源 / style=风格源 / both=两者都是（同一份 HTML 常常两者都占）
    role: Mapped[str] = mapped_column(
        String(16), server_default=text("'content'"), comment="角色:content/style/both"
    )

    # 存相对路径，不存绝对路径（换存储位置时数据不失效，同 resultDir 的做法）
    storage_path: Mapped[str | None] = mapped_column(
        String(512), comment="落盘相对路径(uploads/{user_id}/...)"
    )

    parse_status: Mapped[str] = mapped_column(
        String(16), server_default=text("'pending'"),
        comment="解析状态:pending/parsing/success/failed",
    )

    parse_error: Mapped[str | None] = mapped_column(
        String(1024), comment="解析失败原因(如扫描版 PDF 无文本层)"
    )

    # RequirementDigest 的结构化结果：{content_points, style_spec, constraints, open_questions}
    digest: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="解析出的需求摘要(RequirementDigest)"
    )

    # 是否已进向量库（二期 RAG 用）。现在就建列，避免二期再改表
    is_indexed: Mapped[int] = mapped_column(
        SmallInteger, server_default=text("0"), comment="是否已进向量库(二期 RAG 用)"
    )

    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), comment="更新时间"
    )

    is_delete: Mapped[int] = mapped_column(
        SmallInteger, server_default=text("0"), comment="是否删除:0否 1是"
    )
