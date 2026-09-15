# app/repositories/agent/plan_repository.py —— generation_plan 表的数据库操作（PG 侧）
#
# 约定与其它 repository 一致：只做 CRUD，查询过滤 is_delete == 0。
#
# 本表是「预估 vs 实际」的对账载体，因此有两个方法值得特别注意：
#   - `mark_outcome()`：阶段 6 的生成循环结束时**回填实际值**（实际步数 / 文件数 / 结果）；
#   - `list_recent()`：一次查询拿到最近若干次规划的对照数据，供优化提示词使用。

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import GenerationPlan


class GenerationPlanRepository:
    """把 generation_plan 表的所有数据库操作集中在这里，方法名即语义。"""

    @staticmethod
    def create(db: Session, plan: GenerationPlan) -> GenerationPlan:
        """新增规划记录并返回。

        Args:
            db: PG 数据库会话。
            plan: 尚未入库的对象。

        Returns:
            入库后的对象。

        Raises:
            ValueError: 传入的对象已经入库。
        """
        if plan.id is not None:
            raise ValueError("create() 只接受尚未入库的对象；更新请用 update()")
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return plan

    @staticmethod
    def update(db: Session, plan: GenerationPlan) -> GenerationPlan:
        """提交对规划记录的修改。

        ⚠️ 本表**没有** ON UPDATE：`update_time` 必须在这里显式写入，
        否则它永远停在创建时间（`server_onupdate` 不会出现在 UPDATE 语句里，
        generation_task 上已经踩过同一个坑）。

        Args:
            db: PG 数据库会话。
            plan: 已修改的对象。

        Returns:
            更新后的对象。

        Raises:
            ValueError: 传入的对象尚未入库。
        """
        if plan.id is None:
            raise ValueError("update() 只接受已入库的对象；新增请用 create()")
        plan.update_time = datetime.now(timezone.utc)
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return plan

    @staticmethod
    def get_by_uuid(db: Session, plan_uuid: str) -> GenerationPlan | None:
        """按 plan_uuid 查（未删除）。

        Args:
            db: PG 数据库会话。
            plan_uuid: 规划唯一标识。

        Returns:
            规划记录；不存在则 None。
        """
        stmt = select(GenerationPlan).where(
            GenerationPlan.plan_uuid == plan_uuid,
            GenerationPlan.is_delete == 0,
        )
        return db.scalar(stmt)

    @staticmethod
    def get_latest_by_task_uuid(db: Session, task_uuid: str) -> GenerationPlan | None:
        """取某个任务**最近一次**规划（同一任务可能重跑规划）。

        Args:
            db: PG 数据库会话。
            task_uuid: 生成任务标识。

        Returns:
            最近一次规划记录；不存在则 None。
        """
        stmt = (
            select(GenerationPlan)
            .where(
                GenerationPlan.task_uuid == task_uuid,
                GenerationPlan.is_delete == 0,
            )
            .order_by(GenerationPlan.id.desc())
            .limit(1)
        )
        return db.scalar(stmt)

    @staticmethod
    def list_recent(
        db: Session, *, user_id: int | None = None, limit: int = 20
    ) -> list[GenerationPlan]:
        """取最近的规划记录（新 → 旧），用于「预估 vs 实际」对账。

        Args:
            db: PG 数据库会话。
            user_id: 只看某个用户；None 表示不限。
            limit: 最多取多少条。

        Returns:
            规划记录列表（新 → 旧）。
        """
        stmt = select(GenerationPlan).where(GenerationPlan.is_delete == 0)
        if user_id is not None:
            stmt = stmt.where(GenerationPlan.user_id == user_id)
        stmt = stmt.order_by(GenerationPlan.id.desc()).limit(limit)
        return list(db.scalars(stmt))

    @staticmethod
    def mark_outcome(
        db: Session,
        plan: GenerationPlan,
        *,
        actual_steps: int | None,
        actual_file_count: int | None,
        outcome_status: str,
    ) -> GenerationPlan:
        """回填「实际发生」的三个值（阶段 6 的生成循环结束时调用）。

        Args:
            db: PG 数据库会话。
            plan: 规划记录。
            actual_steps: 实际使用的工具调用步数。
            actual_file_count: 实际交付的文件数。
            outcome_status: 结果状态（success / failed）。

        Returns:
            更新后的对象。
        """
        plan.actual_steps = actual_steps
        plan.actual_file_count = actual_file_count
        plan.outcome_status = outcome_status
        plan.finished_at = datetime.now(timezone.utc)
        return GenerationPlanRepository.update(db, plan)
