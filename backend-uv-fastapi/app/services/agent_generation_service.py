# app/services/agent_generation_service.py —— agent 模式的生成编排（阶段 6 起）
#
# 职责：把 `agents/orchestrator.py` 的**纯函数**结果落成真实世界的副作用 ——
# 推进任务阶段、写 `generation_plan`（预估侧）、回填实际值、落盘产物与 trace、写任务终态。
#
# ⚠️ 两个库同时被使用，读代码时务必分清：
#   - MySQL（`db`）：任务生命周期（状态 / 阶段 / 进度 / 用量 / 产物目录）
#   - PG（`pg`）：Agent 域（附件的 digest、规划记录与"预估 vs 实际"对账）
#   跨库不 JOIN，只靠 `task_uuid` / `session_uuid` 在**本层**组装。
#
# ⚠️ 本模块运行在 **worker 进程**里：不能复用 FastAPI 请求注入的 Session，
# 两个库的 Session 都由这里自己开（SQLAlchemy Session 非线程安全）。

import json
import logging
import time
import uuid

from sqlalchemy.orm import Session

from app.agents import orchestrator
from app.agents.plan.plan_agent import PlanResult
from app.agents.stages import AgentStage
from app.agents.state import FinalRequirement, RequirementDigest
from app.core.pg_db import PgSessionLocal
from app.models.agent import AgentSession, GenerationPlan, GenerationSource
from app.models.generation_task import GenerationTask
from app.repositories.agent import (
    AgentSessionRepository,
    GenerationPlanRepository,
    GenerationSourceRepository,
)
from app.services.generation_service import GenerationService
from app.utils.weg_gen.file_writer import write_debug_trace, write_files

logger = logging.getLogger(__name__)


class AgentGenerationService:
    """agent 模式：把编排结果落成阶段推进 + 落盘 + 落库。"""

    @staticmethod
    def validate_session(user_id: int, session_uuid: str | None) -> None:
        """校验会话归属（**越权防线**）。

        ⚠️ 必须在建任务之前校验：不校验的话，别人传一个不属于自己的 `session_uuid`，
        生成时就会把**别人的附件 digest** 读进提示词 —— 那等于把私人资料喂给了当前用户。

        Args:
            user_id: 当前登录用户 id。
            session_uuid: 会话标识；None 表示不带附件，直接通过。

        Raises:
            HTTPException: 会话不存在或不属于当前用户 → 404。
        """
        if not session_uuid:
            return

        from fastapi import HTTPException

        pg = PgSessionLocal()
        try:
            session = AgentSessionRepository.get_by_uuid(pg, session_uuid)
            if session is None or session.user_id != user_id:
                raise HTTPException(status_code=404, detail="会话不存在")
        finally:
            pg.close()

    @staticmethod
    def execute(db: Session, task: GenerationTask, started: float) -> None:
        """跑完 agent 模式的生成（worker 侧，同步阻塞）。

        Args:
            db: MySQL 会话（任务生命周期）。
            task: 任务对象。
            started: 计时起点（``time.perf_counter()``）。
        """
        pg = PgSessionLocal()
        plan_row: GenerationPlan | None = None
        try:
            digests, attach_warnings = AgentGenerationService._load_sources(pg, task)

            def _on_stage(stage: AgentStage, detail: str) -> None:
                # 阶段推进走既有 _set_stage：它会同时刷新 updateTime（僵尸回收靠它判断心跳）
                GenerationService._set_stage(db, task, stage, detail=detail or None)

            def _on_plan(plan_result: PlanResult, requirement: FinalRequirement) -> None:
                # 规划一完成就写预估侧：这样任务若在生成途中挂掉，
                # 库里仍留着"当时打算交哪些文件、预算给到多少"，排查不必重跑
                nonlocal plan_row
                plan_row = AgentGenerationService._save_plan(pg, task, plan_result, requirement)

            result = orchestrator.run(
                task.prompt,
                user_id=task.user_id,
                digests=digests,
                attach_warnings=attach_warnings,
                on_stage=_on_stage,
                on_plan=_on_plan,
            )

            # trace 与产物都要落盘（trace 是调试附属物，不进 file_list）
            if result.web_result is not None:
                write_debug_trace(task.user_id, task.task_uuid, result.web_result.trace_jsonl)

            AgentGenerationService._finish(db, task, started, result)
            if plan_row is not None:
                AgentGenerationService._record_outcome(pg, plan_row, result)
        except Exception as error:  # noqa: BLE001 —— 意外失败必须落成终态，否则任务永远 running
            logger.exception("任务 %s 的 agent 流水线异常", task.task_uuid)
            GenerationService._fail(db, task, f"生成失败：{error}", started)
            raise
        finally:
            pg.close()

    # ==================== 内部工具 ====================

    @staticmethod
    def _load_sources(
        pg: Session, task: GenerationTask
    ) -> tuple[list[tuple[str, RequirementDigest]], list[str]]:
        """按会话取回附件 digest（**上传时已算好，这里只消费不再计费**）。

        Args:
            pg: PG 会话。
            task: 任务对象（用它的 session_uuid / user_id）。

        Returns:
            (digests, 警告)：``(别名, digest)`` 列表与解析失败等非致命问题。
        """
        if not task.session_uuid:
            return [], []

        session: AgentSession | None = AgentSessionRepository.get_by_uuid(pg, task.session_uuid)
        if session is None or session.user_id != task.user_id:
            # 建任务时已校验过归属；这里再挡一次，避免会话被删/换主后仍然读数据
            return [], ["来源会话不存在或不属于当前用户，已忽略附件"]

        warnings: list[str] = []
        digests: list[tuple[str, RequirementDigest]] = []
        for source in GenerationSourceRepository.list_by_session(pg, session.id):
            if source.parse_status != "success" or not source.digest:
                warnings.append(
                    f"{source.alias}（{source.display_name or '未命名'}）未能用于生成："
                    f"{source.parse_error or '解析未完成'}"
                )
                continue
            try:
                digests.append((source.alias, RequirementDigest.model_validate(source.digest)))
            except Exception as error:  # noqa: BLE001 —— 库里历史数据不合法时不能拖垮整次生成
                warnings.append(f"{source.alias} 的解析结果无法还原，已跳过（{error}）")
        return digests, warnings

    @staticmethod
    def _save_plan(
        pg: Session,
        task: GenerationTask,
        plan_result: PlanResult,
        requirement: FinalRequirement,
    ) -> GenerationPlan:
        """把规划的**预估侧**写进 generation_plan（实际侧由生成结束后回填）。"""
        plan = plan_result.plan
        row = GenerationPlan(
            plan_uuid=uuid.uuid4().hex,
            user_id=task.user_id,
            task_uuid=task.task_uuid,
            session_id=None,
            final_requirement=requirement.model_dump(),
            file_plan=plan.model_dump(),
            difficulty=plan.difficulty,
            difficulty_declared=plan_result.difficulty_declared,
            planned_file_count=len(plan.files),
            budget_steps=plan_result.budget.max_steps,
            budget_output_tokens=plan_result.budget.max_output_tokens,
            validation_warnings=list(plan_result.warnings),
        )
        return GenerationPlanRepository.create(pg, row)

    @staticmethod
    def _record_outcome(pg: Session, row: GenerationPlan, result: object) -> None:
        """回填**实际侧**（实际步数 / 文件数 / 结果）—— 这是"预估 vs 实际"对账的另一半。"""
        web_result = getattr(result, "web_result", None)
        files = getattr(result, "files", {}) or {}
        GenerationPlanRepository.mark_outcome(
            pg,
            row,
            actual_steps=getattr(web_result, "steps_used", None),
            actual_file_count=len(files),
            outcome_status=str(getattr(result, "status", "failed")),
        )

    @staticmethod
    def _finish(db: Session, task: GenerationTask, started: float, result: object) -> None:
        """按编排结果写任务终态（三种状态各自的处理**不同**）。"""
        status = str(getattr(result, "status", "failed"))
        usage = getattr(result, "usage", None)

        if status == "success":
            files = getattr(result, "files", {}) or {}
            task.result_dir = write_files(task.user_id, task.task_uuid, files)
            task.file_list = json.dumps(sorted(files), ensure_ascii=False)
            task.status = "success"
            task.error_msg = None
            AgentGenerationService._write_usage(task, usage)
            task.duration_ms = int((time.perf_counter() - started) * 1000)
            GenerationService._set_stage(db, task, AgentStage.DONE, detail="已交付全部文件")
            return

        if status == "clarifying":
            # human-in-the-loop 暂停：**不是失败**，任务保持 running 等用户补充。
            # 用量照记（这一次判定确实花了钱），但不写 duration（任务还没结束）。
            AgentGenerationService._write_usage(task, usage)
            GenerationService._set_stage(
                db,
                task,
                AgentStage.CLARIFYING,
                detail=str(getattr(result, "ask_hint", "")) or "信息不足，等待用户补充",
            )
            return

        # failed：半成品绝不能算成功；用量与失败原文都要留下
        message = str(getattr(result, "error_message", "")) or "生成失败"
        web_result = getattr(result, "web_result", None)
        if web_result is not None:
            write_debug_trace(task.user_id, task.task_uuid, getattr(web_result, "trace_jsonl", None))
        GenerationService._fail(db, task, message, started, usage=usage)

    @staticmethod
    def _write_usage(task: GenerationTask, usage: object) -> None:
        """把累计用量写进任务（未接线或降级时按 0 处理）。"""
        task.input_tokens = getattr(usage, "input_tokens", 0) or 0
        task.output_tokens = getattr(usage, "output_tokens", 0) or 0
        task.reasoning_tokens = getattr(usage, "reasoning_tokens", 0) or 0
