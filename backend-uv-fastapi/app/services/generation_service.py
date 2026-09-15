# app/services/generation_service.py —— 业务层：生成任务的"规则中枢"
# 职责：建任务 → 入队 →（worker 侧）调 agents 生成 → 落盘 → 落库（状态与阶段流转）
# 不写 SQL（交 repository）、不写 prompt（交 agents）、不碰 HTTP（交 api）
# 调用链（设计约定 §3.3）：api → 本层 → {repositories, agents, utils}
#
# ⚠️ 本文件同时被两个**进程**使用，读代码时务必区分：
#   - FastAPI 进程：create()      —— 建任务 + 投队列，异步、立即返回
#   - arq worker 进程：execute_pipeline() —— 真正跑生成，同步阻塞、可能跑几分钟
#   两者不能共用数据库 Session（SQLAlchemy Session 非线程安全），各自开各自的。

import json
import logging
import time
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agents.common import GenerationFailedError, ModelUsage
from app.agents.multi_file_graph import generate_multi_file
from app.agents.single_html_flow import generate_single_html
from app.agents.stages import STAGE_PROGRESS, AgentStage, stage_text
from app.core.agent_config import agent_settings
from app.core.arq_pool import GENERATION_JOB_NAME, init_pool
from app.core.mysql_db import MysqlSessionLocal
from app.core.storage_config import storage_settings
from app.models.generation_task import GenerationTask
from app.repositories.generation_repository import GenerationTaskRepository
from app.schemas.generation_schemas import (
    GenerateAcceptedResponse,
    GenerateRequest,
    GenerationListResponse,
    GenerationTaskResponse,
)
from app.utils.weg_gen.file_writer import write_debug_raw, write_files

logger = logging.getLogger(__name__)

# 生成类型 → agents 层函数（**simple 模式**：一次调用直接出产物）。
# 阶段 6 新增的 "agent" 模式不走这里 —— 它需要 db/task（要写阶段、写规划记录、回填实际值），
# 因此由 `agent_generation_service` 单独处理，见 execute_pipeline 里的分支。
# 测试正是靠替换这个字典来注入假生成器，从而完全离线跑通全流程。
_GENERATORS = {
    "single": generate_single_html,
    "multi": generate_multi_file,
}

# 需要走 Agent 流水线的生成类型
AGENT_GEN_TYPE = "agent"


class GenerationService:
    """生成相关的业务操作集合。"""

    # ==================== FastAPI 进程侧 ====================

    @staticmethod
    async def create(db: Session, user_id: int, req: GenerateRequest) -> GenerateAcceptedResponse:
        """创建任务并投进队列，**立即返回**（真正的生成由 worker 进程异步执行）。

        ⚠️ 这里是"提交即返回"，接口不再阻塞到模型写完。
        原因：实测复杂需求单次生成已 157 秒；后续叠加意图识别 / 文档解析 / 规划 / 多轮工具调用
        会涨到 3~5 分钟，同步等待必然导致前端超时。
        进度改由前端轮询 ``GET /api/generation/{task_uuid}`` 获取。

        Args:
            db: 数据库会话（由 FastAPI 依赖注入）。
            user_id: 当前登录用户 id。
            req: 生成请求（需求 + 类型 + 可选来源会话）。

        Returns:
            202 响应的内容：任务标识 + 初始阶段 + 轮询地址。

        Raises:
            HTTPException: 生成类型未实现 → 501；来源会话不属于当前用户 → 404；
                任务队列不可用 → 503。
        """
        is_agent = req.gen_type == AGENT_GEN_TYPE
        generator = _GENERATORS.get(req.gen_type)
        if generator is None and not is_agent:
            raise HTTPException(status_code=501, detail=f"生成类型 {req.gen_type} 尚未实现")

        if is_agent:
            # ⚠️ 越权防线：必须在建任务之前校验会话归属。
            # 不校验的话，别人传一个不属于自己的 session_uuid，生成时就会把**别人的附件 digest**
            # 读进提示词 —— 等于把私人资料喂给了当前用户。
            # 局部 import：agent_generation_service 反向依赖本模块（要复用 _set_stage / _fail），
            # 模块级互相 import 会成环。
            from app.services.agent_generation_service import AgentGenerationService

            AgentGenerationService.validate_session(user_id, req.session_uuid)

        started = time.perf_counter()

        # 1) 先建任务：task_uuid 在内存里生成，目录名因此不依赖数据库自增 id（设计约定 §4）
        task = GenerationTask(
            task_uuid=uuid.uuid4().hex,
            user_id=user_id,
            prompt=req.prompt,
            gen_type=req.gen_type,
            session_uuid=req.session_uuid if is_agent else None,
            status="running",
            stage=AgentStage.QUEUED.value,
            progress=STAGE_PROGRESS[AgentStage.QUEUED],
        )
        task = GenerationTaskRepository.create(db, task)

        # 2) 入队。_job_id 用 task_uuid：arq 用 Redis 事务保证同一个 id 不会被投递两次
        try:
            # init_pool() 幂等：即使应用启动时 Redis 没起来，这里也会重新尝试连接
            pool = await init_pool()
            job = await pool.enqueue_job(
                GENERATION_JOB_NAME, task.task_uuid, _job_id=task.task_uuid
            )
        except Exception as error:
            # Redis 不可用：任务已经落库，却永远不会有人消费它。
            # 必须当场标失败，否则前端会一直轮询"排队中"直到天荒地老。
            GenerationService._fail(db, task, f"任务入队失败：{error}", started)
            raise HTTPException(status_code=503, detail="任务队列不可用，请稍后重试") from error

        if job is None:
            # 队列里已存在同 job_id 的任务（正常不会发生：task_uuid 是刚生成的 uuid4）。
            # 不视为错误 —— 接口语义仍然成立：用户拿到的就是那个正在排队的任务。
            logger.warning("任务 %s 已存在于队列中，跳过重复投递", task.task_uuid)

        return GenerateAcceptedResponse(
            task_uuid=task.task_uuid,
            status=task.status,
            stage=task.stage,
            stage_text=stage_text(task.stage),
            progress=task.progress,
            poll_url=f"/api/generation/{task.task_uuid}",
            poll_interval_ms=agent_settings.agent_poll_interval_ms,
        )

    @staticmethod
    def get_task(db: Session, user_id: int, task_uuid: str) -> GenerationTaskResponse:
        """查单个任务详情（只能查自己的）。

        Args:
            db: 数据库会话。
            user_id: 当前登录用户 id。
            task_uuid: 任务唯一标识。

        Returns:
            任务详情（含阶段与进度，前端靠它轮询）。

        Raises:
            HTTPException: 任务不存在或不属于当前用户，一律 404。
        """
        task = GenerationTaskRepository.get_by_task_uuid(db, task_uuid)
        # "不存在"和"不是你的"故意返回同一个结果：不泄露"这个 uuid 存在但不属于你"
        if task is None or task.user_id != user_id:
            raise HTTPException(status_code=404, detail="任务不存在")
        return GenerationService._to_response(task)

    @staticmethod
    def list_tasks(
        db: Session, user_id: int, page: int = 1, page_size: int = 20
    ) -> GenerationListResponse:
        """分页查"我的生成历史"。

        Args:
            db: 数据库会话。
            user_id: 当前登录用户 id。
            page: 页码，从 1 开始。
            page_size: 每页条数。

        Returns:
            总数 + 当前页数据。
        """
        total = GenerationTaskRepository.count_by_user(db, user_id)
        tasks = GenerationTaskRepository.list_by_user(
            db, user_id, offset=(page - 1) * page_size, limit=page_size
        )
        return GenerationListResponse(
            total=total,
            items=[GenerationService._to_response(task) for task in tasks],
        )

    # ==================== arq worker 进程侧 ====================

    @staticmethod
    def execute_pipeline(task_uuid: str) -> None:
        """worker 入口：跑完一次生成的完整流程，并全程推进阶段。

        ⚠️ 与 create() 的三条关键区别（每条都对应一个真实的坑）：
        1. **运行在另一个进程**：不能复用 FastAPI 请求注入的 db Session，
           这里必须自己开一个（SQLAlchemy Session 非线程安全）。
        2. **是同步阻塞函数**：worker 侧用 ``asyncio.to_thread`` 调它，
           否则会阻塞 worker 的事件循环，同一 worker 上的其它任务全部卡住。
        3. **失败必须落成终态**：不能让异常悄悄溜走，否则任务会永远停在 running，
           只能等僵尸回收来擦屁股。

        Args:
            task_uuid: 任务唯一标识（队列里只传这一个字符串，不传 payload）。
        """
        db = MysqlSessionLocal()
        started = time.perf_counter()
        try:
            task = GenerationTaskRepository.get_by_task_uuid(db, task_uuid)
            if task is None:
                # 任务被删了（逻辑删除）或 uuid 有误：没有任何可写的东西，直接收工
                logger.warning("任务 %s 不存在或已删除，跳过执行", task_uuid)
                return

            if task.gen_type == AGENT_GEN_TYPE:
                # Agent 流水线：自己推进阶段（routing → … → done/failed）、写规划记录、
                # 回填实际值。它需要 db 与 task，所以不走 _GENERATORS 那条"只传 prompt"的路。
                from app.services.agent_generation_service import AgentGenerationService

                AgentGenerationService.execute(db, task, started)
                return

            generator = _GENERATORS.get(task.gen_type)
            if generator is None:
                GenerationService._fail(db, task, f"生成类型 {task.gen_type} 尚未实现", started)
                return

            # 阶段 0 的流水线只有一步，所以直接进 GENERATING。
            # 阶段 2~6 会在这里依次插入 routing → digesting → retrieving → planning，
            # 每个节点都调一次 _set_stage，前端就能看到进度一格格往前走。
            GenerationService._set_stage(db, task, AgentStage.GENERATING)

            try:
                result = generator(task.prompt)
            except GenerationFailedError as error:
                # 可预期的失败：先把模型原文落盘（排查不必再烧一次 token 复现），再记用量
                write_debug_raw(task.user_id, task.task_uuid, error.raw_output)
                GenerationService._fail(db, task, str(error), started, usage=error.usage)
                return
            except Exception as error:
                # 意外失败（网络 / 鉴权 / 程序 bug）：先把终态写进库，再上抛。
                # 上抛是为了让 arq 也把它计入 j_failed（`arq --check` 与 worker 日志能看见）；
                # max_tries=1，所以上抛不会导致重复烧 token。
                GenerationService._fail(db, task, f"生成失败：{error}", started)
                raise

            # 成功：落盘 + 落库（落盘由 service 统一做，agents 层只返回字典 —— 设计约定 §3.2）
            task.result_dir = write_files(task.user_id, task.task_uuid, result.files)
            task.file_list = json.dumps(sorted(result.files), ensure_ascii=False)
            task.input_tokens = result.usage.input_tokens
            task.output_tokens = result.usage.output_tokens
            task.reasoning_tokens = result.usage.reasoning_tokens
            task.duration_ms = int((time.perf_counter() - started) * 1000)
            task.status = "success"
            GenerationService._set_stage(db, task, AgentStage.DONE)
        finally:
            db.close()

    @staticmethod
    def recover_zombies(db: Session, grace_seconds: int) -> int:
        """回收僵尸任务：把"早已停止推进却仍标记为 running"的任务改成 failed。

        为什么有了 Redis 队列还需要它：
        队列只保证"**排队中**的任务不丢"。但一个任务可能已经不在队列里 ——
        worker 被强杀、`_expires` 过期、或进程崩溃 —— 却始终没写终态。
        它会永远占着"进行中"，用户的历史列表里就会挂着一排假进度。

        Args:
            db: 数据库会话。
            grace_seconds: 宽限期（秒）。updateTime 早于「现在 - 宽限期」才判定为孤儿
                （多 worker 场景下，B 启动时 A 可能正在正常跑任务，不设宽限会误杀）。
                另外会**跳过 `stage='clarifying'`**：那是"正在等用户补充信息"的暂停态，
                它没有在跑，但也不是孤儿 —— 用户思考多久都不该被回收成 failed。

        Returns:
            被回收的任务条数（0 表示没有僵尸）。
        """
        deadline = datetime.now() - timedelta(seconds=grace_seconds)
        zombies = GenerationTaskRepository.list_stale_running(
            db, deadline, exclude_stages=(AgentStage.CLARIFYING.value,)
        )
        now = datetime.now()
        for task in zombies:
            task.status = "failed"
            task.error_msg = "任务超时或执行进程中断，已自动标记为失败"
            task.stage = AgentStage.FAILED.value
            # 进度刻意保留：知道"死在 70%"比归零更有排查价值
            task.update_time = now
        if zombies:
            db.commit()
            logger.warning("僵尸任务回收：%d 条 running 任务已标记为 failed", len(zombies))
        return len(zombies)

    # ==================== 内部工具 ====================

    @staticmethod
    def _set_stage(
        db: Session,
        task: GenerationTask,
        stage: AgentStage,
        detail: str | None = None,
    ) -> None:
        """推进任务阶段并落库（阶段 / 进度 / 明细 / 更新时间 一起写）。

        ⚠️ 为什么必须**显式**写 update_time：
        本表 DDL 里 ``updateTime`` 只有 ``DEFAULT CURRENT_TIMESTAMP``，**没有 ON UPDATE 子句**
        （SQLAlchemy 的 server_onupdate 不会出现在 UPDATE 语句里），数据库并不会自动刷新它。
        而僵尸回收恰恰以 updateTime 判断"这条任务多久没动过了"——
        所以它必须由这里主动推进，这个字段同时承担了"最后心跳"的职责。

        Args:
            db: 数据库会话。
            task: 任务对象。
            stage: 要推进到的阶段。
            detail: 给用户看的一句话明细；None 表示不改动原有明细。
        """
        task.stage = stage.value
        if stage is not AgentStage.FAILED:
            task.progress = STAGE_PROGRESS.get(stage, task.progress)
        # 用 .get() 而不是 []：FAILED 与 CLARIFYING 不在进度表里，此时**保持原进度** ——
        # 失败保留"死在第几 %"、澄清保留"暂停在第几 %"，都比归零有信息量
        if detail is not None:
            task.stage_detail = detail[:255]
        task.update_time = datetime.now()
        GenerationTaskRepository.update(db, task)

    @staticmethod
    def _fail(
        db: Session,
        task: GenerationTask,
        message: str,
        started: float,
        usage: ModelUsage | None = None,
    ) -> None:
        """把任务置为失败终态并落库（**失败同样要记 token 用量**）。

        Args:
            db: 数据库会话。
            task: 任务对象。
            message: 失败原因（面向用户，截断到 1000 字）。
            started: 计时起点（time.perf_counter() 的返回值）。
            usage: 本次消耗的 token 用量；意外失败通常没有。
        """
        task.status = "failed"
        task.error_msg = message[:1000]
        task.duration_ms = int((time.perf_counter() - started) * 1000)
        if usage is not None:
            task.input_tokens = usage.input_tokens
            task.output_tokens = usage.output_tokens
            task.reasoning_tokens = usage.reasoning_tokens
        # 顺序要紧：先把 status / error_msg 写进对象，再由 _set_stage 统一 commit，
        # 否则 commit 只带走阶段字段，失败原因会丢。
        GenerationService._set_stage(db, task, AgentStage.FAILED, detail=message)

    @staticmethod
    def _to_response(task: GenerationTask) -> GenerationTaskResponse:
        """ORM 对象 → 响应模型，并补上派生字段 preview_url 与 stage_text。

        Args:
            task: 任务 ORM 对象。

        Returns:
            响应模型。
        """
        response = GenerationTaskResponse.model_validate(task)
        if task.result_dir:
            # 预览 URL 的拼法只在后端这一处定义，前端不重复实现
            response.preview_url = f"{storage_settings.preview_prefix}/{task.result_dir}/index.html"
        # 阶段 → 中文文案
        response.stage_text = stage_text(task.stage)
        return response
