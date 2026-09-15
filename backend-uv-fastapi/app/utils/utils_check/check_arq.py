# app/utils/utils_check/check_arq.py —— 开发期自检：Redis 队列与 arq worker 配置
#
# 运行（在 backend-uv-fastapi 下，必须用 -m 让 app 包可导入）：
#   uv run python -m app.utils.utils_check.check_arq
#   .venv\Scripts\python.exe -m app.utils.utils_check.check_arq
#
# 它检查三件事：
#   1. Redis 连得上吗（队列载体）
#   2. WorkerSettings 的成本护栏还在吗（max_tries=1 / keep_result=0 / job_timeout）
#   3. 入队 + 同 job_id 去重，行为是否符合预期
#
# ⚠️ 探测用的 job 投在**独立的队列名**（arq:probe）上：
# 真实 worker 只消费 arq:queue，所以这个探测既不会被执行、也不会污染真实队列，
# 检查完就把该队列删掉。

import asyncio
import sys
import uuid

import redis

from app.core.agent_config import agent_settings
from app.core.arq_pool import GENERATION_JOB_NAME
from app.core.redis_config import build_arq_redis_settings, redis_settings
from app.core.worker import WorkerSettings

# 探测专用队列名：与真实队列隔离，两边互不干扰
PROBE_QUEUE = "arq:probe"


def check_redis() -> bool:
    """检查 Redis 是否连得上，并打印版本。"""
    print("=" * 68)
    print("1) Redis 连通性")
    try:
        client = redis.Redis(
            host=redis_settings.redis_host,
            port=redis_settings.redis_port,
            db=redis_settings.redis_db,
            password=redis_settings.redis_password or None,
            socket_connect_timeout=3,
        )
        pong = client.ping()
        info = client.info()
        print(f"   PING   -> {pong}")
        print(f"   地址   -> {redis_settings.redis_host}:{redis_settings.redis_port} "
              f"db={redis_settings.redis_db}")
        print(f"   版本   -> {info.get('redis_version')}")
        print("   结论   -> 通过")
        return True
    except Exception as error:  # noqa: BLE001 —— 自检脚本要把失败原因原样打出来
        print(f"   失败   -> {type(error).__name__}: {error}")
        print("   处理   -> 确认 Redis 已启动（docker compose up -d redis）")
        return False


def check_worker_settings() -> bool:
    """打印并校验 worker 的成本护栏。"""
    print("=" * 68)
    print("2) WorkerSettings 成本护栏")
    settings = {
        "registered jobs": [f.name for f in WorkerSettings.functions],
        "max_jobs": WorkerSettings.max_jobs,
        "job_timeout": WorkerSettings.job_timeout,
        "max_tries": WorkerSettings.max_tries,
        "keep_result": WorkerSettings.keep_result,
        "health_check_interval": WorkerSettings.health_check_interval,
    }
    for key, value in settings.items():
        print(f"   {key:<22} -> {value}")

    problems: list[str] = []
    if settings["registered jobs"] != [GENERATION_JOB_NAME]:
        problems.append(f"注册的任务名应为 [{GENERATION_JOB_NAME}]")
    if WorkerSettings.max_tries != 1:
        problems.append("max_tries 必须为 1（LLM 按 token 计费，自动重试等于重复烧钱）")
    if WorkerSettings.keep_result != 0:
        problems.append("keep_result 应为 0（结果真源在 MySQL，不存 Redis）")

    if problems:
        print("   结论   -> 不通过：" + "；".join(problems))
        return False
    print("   结论   -> 通过")
    return True


async def check_enqueue_and_dedup() -> bool:
    """检查"能入队"与"同 job_id 不会重复入队"。"""
    print("=" * 68)
    print("3) 入队与 job_id 去重（探测队列，不影响真实队列）")

    client = redis.Redis(
        host=redis_settings.redis_host,
        port=redis_settings.redis_port,
        db=redis_settings.redis_db,
        password=redis_settings.redis_password or None,
        socket_connect_timeout=3,
    )
    # 清掉上一次的探测残留，让结果可重复
    client.delete(PROBE_QUEUE)

    import arq

    pool = await arq.create_pool(build_arq_redis_settings())
    try:
        probe_id = f"probe-{uuid.uuid4().hex}"
        probe_uuid = uuid.uuid4().hex  # 真实任务里这里会是真实 task_uuid

        first = await pool.enqueue_job(
            GENERATION_JOB_NAME, probe_uuid, _job_id=probe_id, _queue_name=PROBE_QUEUE
        )
        second = await pool.enqueue_job(
            GENERATION_JOB_NAME, probe_uuid, _job_id=probe_id, _queue_name=PROBE_QUEUE
        )

        print(f"   首次入队 -> {'成功（返回 Job）' if first is not None else '失败（返回 None）'}")
        print(f"   重复入队 -> {'返回 Job（去重未生效！）' if second is not None else 'None（去重生效）'}")

        ok = first is not None and second is None
        print(f"   结论   -> {'通过' if ok else '不通过'}")
        return ok
    finally:
        # 探测 job 不会被真实 worker 消费（队列名不同），检查完直接清掉
        client.delete(PROBE_QUEUE)
        await pool.aclose()


def main() -> int:
    """跑完全部检查，返回进程退出码（0=全通过）。"""
    print(f"环境：agent_max_jobs={agent_settings.agent_max_jobs} "
          f"job_timeout={agent_settings.agent_job_timeout_seconds}s "
          f"max_tries={agent_settings.agent_job_max_tries}")

    redis_ok = check_redis()
    settings_ok = check_worker_settings()
    if not redis_ok:
        # Redis 都连不上，入队检查没有意义
        print("=" * 68)
        print("Redis 不可用，跳过第 3 项检查")
        return 1

    enqueue_ok = asyncio.run(check_enqueue_and_dedup())

    print("=" * 68)
    passed = redis_ok and settings_ok and enqueue_ok
    print("汇总：", "全部通过" if passed else "存在失败项")
    if not passed:
        print("提示：真实 worker 是否在跑，用 `arq --check app.core.worker.WorkerSettings` 看")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
