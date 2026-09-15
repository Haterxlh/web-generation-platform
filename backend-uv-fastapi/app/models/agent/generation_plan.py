# app/models/agent/generation_plan.py —— generation_plan 表的 ORM 实体（PostgreSQL 侧）
#
# ⚠️ 本文件只 import PgBase（隔离规则见 app/core/pg_db.py 顶部注释）。
#
# 为什么推理产物放 PG 而不是塞进 MySQL 的 generation_task（§0.3 决策 10、§3.4）：
# `FinalRequirement` / `FilePlan` 属于 Agent 的"思考过程"，与"业务元数据"是两类数据；
# 混在一起会让 generation_task 变成杂物间。跨库不 JOIN，只靠 task_uuid 在 service 层组装。
#
# 本表还承担一件**为优化提示词服务**的事：把「模型预估」与「实际发生」记在同一行，
# 于是"难度判得准不准、预算给得够不够"可以直接查，而不必去翻日志：

#   预估侧：difficulty_declared（模型原始判定）/ difficulty（Python 复核后的最终值）
#           planned_file_count / budget_steps / budget_output_tokens
#   实际侧：actual_steps（web-agent 真正的工具调用步数）/ actual_file_count /
#           outcome_status / finished_at
#
# 两侧放在同一行是刻意的：否则对账要跨库拼两次查询，"查一下最近 20 次规划准不准"这种
# 日常动作都变得很麻烦，最后就没人查了。

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Integer, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.pg_db import PgBase


class GenerationPlan(PgBase):
    """一次生成的规划记录（阶段 5 起使用）。

    一行 = 一次规划尝试（同一任务重跑规划会产生多行，按 id 取最新）。
    """

    __tablename__ = "generation_plan"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键 id"
    )

    plan_uuid: Mapped[str] = mapped_column(
        String(64), unique=True, comment="规划唯一标识(uuid4.hex)"
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, index=True, comment="所属用户 id(MySQL user 表，跨库不 JOIN)"
    )

    # 与 MySQL 的 generation_task.taskUuid 对应；跨库只存 id，不建外键、不 JOIN
    task_uuid: Mapped[str] = mapped_column(
        String(64), index=True, comment="所属生成任务标识(MySQL generation_task.taskUuid)"
    )

    session_id: Mapped[int | None] = mapped_column(
        BigInteger, comment="来源会话 id(agent_session.id)；非对话入口发起时为空"
    )

    # FinalRequirement 的字典形式（结构见 app/agents/state.py）
    final_requirement: Mapped[dict[str, Any]] = mapped_column(
        JSONB, comment="最终需求(FinalRequirement)"
    )

    # FilePlan 的字典形式：交付清单 + 难度 + 入口 + 技术约束 + 外部资源
    file_plan: Mapped[dict[str, Any]] = mapped_column(JSONB, comment="交付计划(FilePlan)")

    # ---- 预估侧 ----
    difficulty: Mapped[str] = mapped_column(
        String(16), comment="最终难度(Python 复核后，只会上调)"
    )
    difficulty_declared: Mapped[str] = mapped_column(
        String(16), comment="模型原始声明的难度(与最终难度对照，用于优化提示词)"
    )
    planned_file_count: Mapped[int] = mapped_column(
        Integer, comment="计划交付的文件数(清洗后)"
    )
    budget_steps: Mapped[int] = mapped_column(Integer, comment="该难度的步数上限")
    budget_output_tokens: Mapped[int] = mapped_column(Integer, comment="该难度的输出 token 预算")
    validation_warnings: Mapped[list[Any] | None] = mapped_column(
        JSONB, comment="计划清洗阶段的警告(非法文件名、难度上调、悬空依赖等)"
    )

    # ---- 实际侧（由阶段 6 的生成循环结束时回填）----
    actual_steps: Mapped[int | None] = mapped_column(
        Integer, comment="实际使用的工具调用步数(生成结束后回填)"
    )
    actual_file_count: Mapped[int | None] = mapped_column(
        Integer, comment="实际交付的文件数(生成结束后回填)"
    )
    outcome_status: Mapped[str | None] = mapped_column(
        String(16), comment="结果:success/failed(生成结束后回填)"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="生成结束时间(生成结束后回填)"
    )

    # 时间列统一 timestamptz；⚠️ 本表**没有** ON UPDATE，
    # update_time 必须在 UPDATE 时显式写入（与 generation_task 同一教训）
    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), comment="创建时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), comment="更新时间"
    )

    is_delete: Mapped[int] = mapped_column(
        SmallInteger, server_default=text("0"), comment="是否删除:0否 1是"
    )
