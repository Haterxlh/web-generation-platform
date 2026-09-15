# app/core/redis_config.py —— Redis 配置：把 .env 的值转成 arq 需要的 RedisSettings
#
# Redis 在我们的架构里的定位（重要，别搞错）：
#   它**只是任务队列**，不是数据真源。任务的状态 / 阶段 / 进度 / 结果一律落 MySQL。
#   若把状态只放 Redis，一次 FLUSHDB、一次内存淘汰、或没开持久化的一次重启，
#   用户界面就会永远停在"排队中"且无从排查。
#   详见 docs/agent_refactor_plan.md §3.4。

from arq.connections import RedisSettings as ArqRedisSettings

from app.core.settings_base import AppSettings


class RedisSettings(AppSettings):
    """Redis 连接配置（arq 的任务队列就建在这个连接上）。"""

    redis_host: str = "127.0.0.1"
    redis_port: int = 6379
    redis_db: int = 0
    # 本地开发通常没有密码；留空表示不鉴权（arq 需要 None 而不是空字符串）
    redis_password: str | None = None


redis_settings = RedisSettings()  # 全局共用一份


def build_arq_redis_settings() -> ArqRedisSettings:
    """把项目配置转成 arq 的连接配置。

    为什么要这层转换：项目的配置类是 pydantic-settings（负责读 .env），
    而 arq 只认它自己的 RedisSettings —— 转换只写这一处，
    api 进程建池（arq_pool）与 worker 进程建 worker（worker.py）共用它，
    保证两边连的是同一个库。

    Returns:
        arq 的 RedisSettings。
    """
    return ArqRedisSettings(
        host=redis_settings.redis_host,
        port=redis_settings.redis_port,
        database=redis_settings.redis_db,
        # 空字符串要转成 None：arq/redis 认为 password="" 是"有空密码"，会鉴权失败
        password=redis_settings.redis_password or None,
    )
