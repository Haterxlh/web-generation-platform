# app/models/generation_task.py —— generation_task 表的 ORM 实体
# 命名与写法完全对齐 user.py：驼峰列名显式映射、逻辑删除、三时间列、Google docstring

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, SmallInteger, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mysql_db import MysqlBase


class GenerationTask(MysqlBase):
    """生成任务实体，对应 MySQL 里的 generation_task 表"""

    __tablename__ = "generation_task"

    id: Mapped[int] = mapped_column(
        "id", BigInteger, primary_key=True, autoincrement=True, comment="主键 id"
    )

    # 对外标识：目录名与接口路径都用它，绝不对外暴露自增 id（设计约定 §4）
    task_uuid: Mapped[str] = mapped_column(
        "taskUuid", String(64), unique=True, comment="任务唯一标识(uuid4.hex)"
    )

    # 发起用户：加索引，"我的生成历史"是最常用的查询
    user_id: Mapped[int] = mapped_column(
        "userId", BigInteger, index=True, comment="发起用户 id"
    )

    # 用户需求：可能很长，用 Text
    # prompt 用 Text 而不是 String(256)。 → 用户需求可能几百字；VARCHAR 在大字段上性能无优势，Text 更合适。
    prompt: Mapped[str] = mapped_column("prompt", Text, comment="用户需求原文")

    # 生成类型：single / multi（字符串存库，可读；合法值由 Pydantic Literal 兜住）
    gen_type: Mapped[str] = mapped_column(
        "genType", String(16), comment="生成类型:single/multi"
    )

    # 状态：running / success / failed
    # server_default 必须声明，否则插入时 SQLAlchemy 会显式写 NULL 触发 NOT NULL 报错（Q9 的坑）
    status: Mapped[str] = mapped_column(
        "status", String(16), server_default=text("'running'"), comment="状态:running/success/failed"
    )

    # 产物目录：存"相对路径"，不存绝对路径（换存储位置时数据不失效）
    result_dir: Mapped[str | None] = mapped_column(
        "resultDir", String(512), comment="产物相对目录"
    )

    # 产物文件名清单：MySQL 无数组类型，用 JSON 数组字符串存，schema 层转 list
    file_list: Mapped[str | None] = mapped_column(
        "fileList", String(1024), comment="产物文件名(JSON数组字符串)"
    )

    error_msg: Mapped[str | None] = mapped_column(
        "errorMsg", String(1024), comment="失败原因"
    )

    duration_ms: Mapped[int | None] = mapped_column(
        "durationMs", Integer, comment="耗时(毫秒)"
    )

    edit_time: Mapped[datetime] = mapped_column(
        "editTime", DateTime, server_default=text("CURRENT_TIMESTAMP"), comment="编辑时间"
    )
    create_time: Mapped[datetime] = mapped_column(
        "createTime", DateTime, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        "updateTime",
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
        comment="更新时间",
    )

    is_delete: Mapped[int] = mapped_column(
        "isDelete", SmallInteger, server_default=text("0"), comment="是否删除:0否 1是"
    )
    
    # ===== token 用量（失败的任务也要记，因为它们同样烧了额度）=====
    input_tokens: Mapped[int | None] = mapped_column(
        "inputTokens", Integer, comment="输入token数"
    )
    output_tokens: Mapped[int | None] = mapped_column(
        "outputTokens", Integer, comment="输出token数(含思考)"
    )
    reasoning_tokens: Mapped[int | None] = mapped_column(
        "reasoningTokens", Integer, comment="其中思考token数"
    )