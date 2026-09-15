# app/repositories/generation_repository.py —— 数据访问层：generation_task 表的"查存管家"
# 职责：只做数据库操作（CRUD），不写业务规则
# 与 user_repository.py 保持一致：所有查询都带 is_delete == 0（逻辑删除）

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.generation_task import GenerationTask


class GenerationTaskRepository:
    """把 generation_task 表的所有数据库操作集中在这里，方法名即语义"""

    @staticmethod
    def create(db: Session, task: GenerationTask) -> GenerationTask:
        """把新任务写进数据库并返回（带数据库生成的主键 id、默认 status、时间列）。

        Args:
            db: 数据库会话。
            task: 任务对象。

        Returns:
            入库后的任务对象。
        """
        if task.id is not None:
            raise ValueError("create() 只接受尚未入库的任务对象；更新请用 update()")
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def update(db: Session, task: GenerationTask) -> GenerationTask:
        """提交对任务对象的修改。

        Args:
            db: 数据库会话。
            task: 已修改的任务对象。

        Returns:
            更新后的任务对象。
        """
        if task.id is None:
            raise ValueError("update() 只接受已入库的任务对象；新增请用 create()")
        db.add(task)   # 对已入库对象是空操作；保留它只是让"纳入会话"这个语义完整，代价为 0
        db.commit()
        db.refresh(task)
        return task

    @staticmethod
    def get_by_task_uuid(db: Session, task_uuid: str) -> GenerationTask | None:
        """按 task_uuid 查任务（未删除）。

        Args:
            db: 数据库会话。
            task_uuid: 任务唯一标识。

        Returns:
            任务对象；不存在则 None。
        """
        stmt = select(GenerationTask).where(
            GenerationTask.task_uuid == task_uuid,
            GenerationTask.is_delete == 0,
        )
        return db.scalar(stmt)

    @staticmethod
    def list_by_user(
        db: Session, user_id: int, offset: int = 0, limit: int = 20
    ) -> list[GenerationTask]:
        """按用户分页查任务，新的在前。

        Args:
            db: 数据库会话。
            user_id: 用户 id。
            offset: 跳过条数。
            limit: 本页条数。

        Returns:
            任务对象列表。
        """
        stmt = (
            select(GenerationTask)
            .where(GenerationTask.user_id == user_id, GenerationTask.is_delete == 0)
            .order_by(GenerationTask.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(db.scalars(stmt))

    @staticmethod
    def count_by_user(db: Session, user_id: int) -> int:
        """统计某用户的任务总数（分页用）。

        Args:
            db: 数据库会话。
            user_id: 用户 id。

        Returns:
            条数。
        """
        stmt = (
            select(func.count())
            .select_from(GenerationTask)
            .where(GenerationTask.user_id == user_id, GenerationTask.is_delete == 0)
        )
        return db.scalar(stmt) or 0

    @staticmethod
    def list_stale_running(
        db: Session, deadline: datetime, exclude_stages: Sequence[str] = ()
    ) -> list[GenerationTask]:
        """列出"仍标记为 running，但最后更新时间早于 deadline"的任务（僵尸回收用）。

        为什么要靠 updateTime 判断陈旧：worker 进程被强杀时来不及写任何终态，
        库里只会留下一条永远 running 的记录 —— 判据只能是"它多久没动过了"。
        ⚠️ 因此推进阶段时必须**主动**写 updateTime（本表 DDL 没有 ON UPDATE 子句），
        详见 generation_service._set_stage。

        Args:
            db: 数据库会话。
            deadline: 时间阈值；updateTime 早于它的才算僵尸。
            exclude_stages: 不参与回收的阶段（如 `clarifying`：等用户补充信息，不是孤儿）。

        Returns:
            僵尸任务列表（可能为空）。
        """
        stmt = select(GenerationTask).where(
            GenerationTask.status == "running",
            GenerationTask.is_delete == 0,
            # 排除"正在等用户补充信息"的阶段（由调用方传入，本层不认识 AgentStage —— 
            # repositories 是最底层，不该依赖 agents 的枚举）。
            # not_in([]) 在 SQLAlchemy 2.0 里渲染为永真（NOT IN (NULL) OR (1 = 1)），
            # 所以不传排除项时语义就是"不排除任何阶段"，不必写分支。
            GenerationTask.stage.not_in(list(exclude_stages)),
            GenerationTask.update_time < deadline,
        )
        return list(db.scalars(stmt))