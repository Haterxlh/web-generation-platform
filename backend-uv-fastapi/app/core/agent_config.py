# app/core/agent_config.py —— Agent 异步执行的配置：从 .env 读取
# 为什么放 core：与 llm_config.py / mysql_config.py 同类，属于"跨层基础组件"
# （worker 进程、service 层、api 层都要读它）

from app.core.settings_base import AppSettings


class AgentSettings(AppSettings):
    """Agent 异步执行相关的配置（并发 / 超时 / 重试 / 僵尸宽限）。

    字段名与 .env 里的变量名对应（不区分大小写）；
    全部给了默认值，所以 .env 不配也能跑。
    """

    # arq worker 同时执行的任务数。
    # LLM 调用是 IO 密集型，但每个任务都很吃模型配额，默认 2 是"能并行又不至于打满配额"的保守值。
    agent_max_jobs: int = 2

    # 单个任务的最长执行时间（秒）。
    # 实测复杂需求单次生成已 157 秒；后续叠加意图识别 / 文档解析 / 规划 / 多轮工具调用
    # 会涨到 3~5 分钟，所以给到 900 秒（15 分钟）。
    agent_job_timeout_seconds: int = 900

    # ⚠️ 必须保持 1（arq 默认是 5）。
    # arq 采用"悲观执行"：任务在成功/失败之前不会离开队列，worker 中途关闭时任务会在
    # 重启后被重跑，max_tries 就是重跑上限。
    # 我们的任务是 LLM 调用、按 token 计费 —— 自动重试 5 次等于烧 5 次钱。
    # 宁可标记 failed 让用户手动重试，也不自动重跑。
    # 详见 docs/agent_refactor_plan.md §5 硬约束 17。
    agent_job_max_tries: int = 1

    # 僵尸回收宽限期（秒）：worker 启动时，把 updateTime 早于「现在 - 宽限期」的
    # running 任务判定为孤儿并标记失败。
    # 为什么要留宽限而不是"一律回收"：多 worker 场景下，B 启动时 A 可能正在正常跑任务，
    # 不设宽限就会误杀 A 的在途任务。
    agent_zombie_grace_seconds: int = 300

    # 建议前端轮询进度的间隔（毫秒）。
    # 放在后端而不是前端，是为了让"轮询节奏"只有一个定义处（前端直接读接口返回值）。
    agent_poll_interval_ms: int = 1500


agent_settings = AgentSettings()  # 全局共用一份
