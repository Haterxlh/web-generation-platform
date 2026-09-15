# app/core/worker.py —— arq worker：Agent 流水线的**执行进程**
#
# 为什么是独立进程，而不是 FastAPI 里的后台任务（BackgroundTasks）：
#   1) **进程重启不丢任务** —— 队列在 Redis 里，API 崩了任务还在；
#   2) 长任务（几分钟）不会占用 API 的事件循环与内存；
#   3) 可水平扩展 —— 多开几个 worker，arq 用 Redis 保证同一个 job 只被一个 worker 取走。
#
# 启动（开发期 API 与 worker 是两个进程，都要跑）：
#   arq app.core.worker.WorkerSettings
#
# ⚠️ 关键配置见 WorkerSettings 的逐条注释，尤其是 max_tries=1 的理由。

import asyncio
import logging

from arq.worker import func

from app.core.agent_config import agent_settings
from app.core.arq_pool import GENERATION_JOB_NAME
from app.core.mysql_db import MysqlSessionLocal
from app.core.redis_config import build_arq_redis_settings
from app.services.generation_service import GenerationService

logger = logging.getLogger(__name__)


async def run_generation(ctx: dict, task_uuid: str) -> None:
    """执行一次生成流水线（队列里的任务体）。

    ⚠️ 两点必须注意：

    1. **只传 task_uuid，不传 payload**：arq 默认用 pickle 序列化 job。
       队列里放一个字符串既避开反序列化风险，也让任务体保持最小 ——
       真正的需求 / 参数由 worker 按 task_uuid 从数据库读。
    2. **必须丢进线程池**：``execute_pipeline`` 是同步阻塞的（LangChain 的 ``.invoke``
       是同步调用，一次可能跑几分钟）。直接在 async 函数里调用会阻塞本 worker 的
       事件循环，同一 worker 上其它任务会全部卡住。

    Args:
        ctx: arq 的上下文对象（本任务暂未使用，但作为位置参数不能省）。
        task_uuid: 任务唯一标识。
    """
    await asyncio.to_thread(GenerationService.execute_pipeline, task_uuid)


async def on_startup(ctx: dict) -> None:
    """worker 启动钩子：回收僵尸任务。

    为什么放在 worker 启动、而不是 API 启动：
    只有真正执行任务的进程才知道"我刚启动，之前那批 running 没人管了"。
    """
    db = MysqlSessionLocal()
    try:
        GenerationService.recover_zombies(db, agent_settings.agent_zombie_grace_seconds)
    finally:
        db.close()


async def on_shutdown(ctx: dict) -> None:
    """worker 关闭钩子（目前只记日志，便于确认重启时机）。"""
    logger.info("arq worker 正在退出")


class WorkerSettings:
    """arq CLI 的入口配置：``arq app.core.worker.WorkerSettings``。"""

    # 用 func(...) 显式命名，保证"投递用的名字"与"这里注册的名字"永远一致
    # （投递侧用的常量定义在 app/core/arq_pool.py）
    functions = [
        func(
            run_generation,
            name=GENERATION_JOB_NAME,
            max_tries=agent_settings.agent_job_max_tries,
        )
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = build_arq_redis_settings()

    # 同一时刻最多并行跑几个任务
    max_jobs = agent_settings.agent_max_jobs
    # 单个任务的最长执行时间（超时会被 arq 取消）
    job_timeout = agent_settings.agent_job_timeout_seconds
    # ⚠️ 保持 1（arq 默认是 5）：arq 采用"悲观执行" —— 任务在成功/失败前不会离开队列，
    # worker 中途关闭时任务会在重启后被重跑，max_tries 就是重跑上限。
    # 我们的任务是 LLM 调用、按 token 计费，自动重试 5 次等于烧 5 次钱。
    # 宁可标 failed 让用户手动重试，也不自动重跑。详见 docs/agent_refactor_plan.md §5 硬约束 17。
    max_tries = agent_settings.agent_job_max_tries
    # 结果不存 Redis：任务状态的**真源在 MySQL**（见 docs/agent_refactor_plan.md §3.4）
    keep_result = 0
    # 健康检查心跳：arq --check app.core.worker.WorkerSettings 可据此判断 worker 是否活着
    health_check_interval = 30
