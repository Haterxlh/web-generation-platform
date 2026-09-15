# app/core/arq_pool.py —— arq 连接池：全局一份，由 FastAPI 的 lifespan 负责建立与释放
#
# 为什么需要它：
#   1) api 进程要往队列里投任务（POST /api/generation/create），而建池是**异步**的，
#      只能在事件循环里做，不能塞进同步的 service 构造过程；
#   2) 池建一次就够，每请求建一次既慢又浪费连接。
#
# ⚠️ 这里只负责"投递"。任务真正由谁执行，见 app/core/worker.py（独立进程）。

import logging

from arq import create_pool
from arq.connections import ArqRedis

from app.core.redis_config import build_arq_redis_settings

logger = logging.getLogger(__name__)

# 队列里的任务名。
# 放在这里而不是 worker.py：service 要拿这个名字投递任务，而 worker.py 反过来要 import
# service 才能真正执行 —— 常量留在这一层（既不 import service 也不 import worker），
# 就能避免 service ↔ worker 的循环 import。
GENERATION_JOB_NAME = "run_generation"

# 全局连接池。刻意用模块级变量：它本来就该"进程内一份"，与 mysql_db.py 里的 engine 同一个套路。
_pool: ArqRedis | None = None


async def init_pool() -> ArqRedis:
    """建立全局 arq 连接池（幂等，可重试）。

    为什么要"可重试"而不是"只在启动时建一次"：
    若应用启动时 Redis 恰好没起来，一次性初始化会导致pool建立失败；
    有了重试，Redis 恢复后下一次投递就能自愈。

    Returns:
        已建好的连接池。
    """
    global _pool
    if _pool is None:
        _pool = await create_pool(build_arq_redis_settings())
        logger.info("arq 连接池已建立")
    return _pool


async def close_pool() -> None:
    """释放全局连接池（应用关闭时调用，幂等）。"""
    global _pool
    if _pool is not None:
        # redis-py 5.x 起用 aclose()（close() 已废弃）
        await _pool.aclose()
        _pool = None
        logger.info("arq 连接池已关闭")
