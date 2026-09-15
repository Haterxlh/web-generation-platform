"""阶段 0 的离线测试：异步任务骨架（排队 / 阶段推进 / 失败终态 / 僵尸回收 / 成本护栏）。

全程**不连 Redis、不连 MySQL、不调模型**：
数据库与生成器都用假对象替换（monkeypatch），因此可以在 CI 里稳定跑。

之所以要覆盖到这些点，是因为它们各自对应一个真实会烧钱、或会让用户卡住的故障：
- 队列不可用时任务必须当场标 failed，否则前端永远轮询"排队中"；
- 失败的任务同样已经烧了 token，必须记账；
- max_tries 必须保持 1，否则一次崩溃自动重试 5 次 = 烧 5 次钱。
"""

import asyncio
import json
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

import app.services.generation_service as gs
from app.agents.common import GenerationFailedError, GenerationResult, ModelUsage
from app.agents.stages import AgentStage, stage_text
from app.core.agent_config import agent_settings
from app.core.worker import WorkerSettings
from app.models.generation_task import GenerationTask
from app.schemas.generation_schemas import GenerateRequest


# --------------------------------------------------------------------------
# 测试替身（fake）：只实现被测代码真正会用到的那几个方法
# --------------------------------------------------------------------------


class FakeDb:
    """假数据库会话：只记录 add / commit / refresh / close 被调用过。"""

    def __init__(self) -> None:
        self.commits = 0
        self.closed = False

    def add(self, obj: object) -> None:
        return None

    def commit(self) -> None:
        self.commits += 1

    def refresh(self, obj: object) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakeDbHolder:
    """让 fixture 与用例共享同一个 FakeDb 实例。"""

    db = FakeDb()


class FakeRepo:
    """假 repository：把"库里有什么"整个交给测试控制。"""

    task: GenerationTask | None = None
    stale: list[GenerationTask] = []
    updated: list[GenerationTask] = []
    created: list[GenerationTask] = []

    @classmethod
    def reset(cls) -> None:
        cls.task = None
        cls.stale = []
        cls.updated = []
        cls.created = []

    @staticmethod
    def create(db: FakeDb, task: GenerationTask) -> GenerationTask:
        task.id = 1  # 模拟入库后拿到自增主键（repository.update 会校验 id 非空）
        FakeRepo.created.append(task)
        return task

    @staticmethod
    def update(db: FakeDb, task: GenerationTask) -> GenerationTask:
        FakeRepo.updated.append(task)
        return task

    @staticmethod
    def get_by_task_uuid(db: FakeDb, task_uuid: str) -> GenerationTask | None:
        if FakeRepo.task is not None and FakeRepo.task.task_uuid == task_uuid:
            return FakeRepo.task
        return None

    @staticmethod
    def list_stale_running(db: FakeDb, deadline: datetime) -> list[GenerationTask]:
        return list(FakeRepo.stale)


def make_task(gen_type: str = "single", **kwargs: object) -> GenerationTask:
    """造一个未入库的任务对象（阶段字段带默认值）。"""
    return GenerationTask(
        task_uuid="a" * 32,
        user_id=7,
        prompt="做一个待办清单页面",
        gen_type=gen_type,
        status="running",
        stage=AgentStage.QUEUED.value,
        progress=0,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _patch_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 service 模块里的 DB 与落盘依赖整体换成假的（每个用例重置一次）。"""
    FakeRepo.reset()
    FakeDbHolder.db = FakeDb()
    monkeypatch.setattr(gs, "GenerationTaskRepository", FakeRepo)
    monkeypatch.setattr(gs, "MysqlSessionLocal", lambda: FakeDbHolder.db)
    # 落盘换成假函数：单测不该真的往 generated/ 写文件
    monkeypatch.setattr(
        gs, "write_files", lambda user_id, task_uuid, files: f"{user_id}/{task_uuid}"
    )
    monkeypatch.setattr(gs, "write_debug_raw", lambda user_id, task_uuid, text: None)


def _ok_generator(prompt: str) -> GenerationResult:
    return GenerationResult(
        files={"index.html": "<html></html>"},
        usage=ModelUsage(input_tokens=10, output_tokens=20, reasoning_tokens=5),
    )


def _async_value(value: object):
    """返回一个"立即产出 value"的协程 —— 用来替换 init_pool()。"""

    async def _coro() -> object:
        return value

    return _coro()


class FakeJob:
    """arq enqueue_job 的返回值替身。"""

    job_id = "fake-job"


class FakePool:
    """假 arq 连接池；enqueue_error 非空时模拟 Redis 不可用。"""

    def __init__(self, enqueue_error: Exception | None = None, returns: object = None) -> None:
        self.enqueue_error = enqueue_error
        self.returns = FakeJob() if returns is None else returns
        self.calls: list[tuple[str, tuple, dict]] = []

    async def enqueue_job(self, name: str, *args: object, **kwargs: object) -> object:
        self.calls.append((name, args, kwargs))
        if self.enqueue_error is not None:
            raise self.enqueue_error
        return self.returns


# --------------------------------------------------------------------------
# 1. 阶段定义本身
# --------------------------------------------------------------------------


def test_stage_text_maps_known_stage() -> None:
    """已知阶段应翻成中文文案。"""
    assert stage_text("generating") == "正在生成网页"
    assert stage_text("queued") == "排队中"


def test_stage_text_is_safe_for_unknown_value() -> None:
    """脏数据不能把接口搞 500 —— 未知值与空值都要安全返回。"""
    assert stage_text("bogus") == "bogus"
    assert stage_text(None) == ""


# --------------------------------------------------------------------------
# 2. 阶段推进（_set_stage）
# --------------------------------------------------------------------------


def test_set_stage_updates_progress_and_heartbeat() -> None:
    """推进阶段要同时写 阶段 / 进度 / 明细 / updateTime 四样。

    updateTime 尤其重要：本表 DDL 没有 ON UPDATE 子句，
    而僵尸回收靠它判断"多久没动过" —— 不主动写就等于心跳永远停在创建那一刻。
    """
    task = make_task()
    before = task.update_time

    gs.GenerationService._set_stage(
        FakeDbHolder.db, task, AgentStage.GENERATING, detail="正在写 index.html"
    )

    assert task.stage == AgentStage.GENERATING.value
    assert task.progress == 70
    assert task.stage_detail == "正在写 index.html"
    assert task.update_time is not None
    assert before is None or task.update_time >= before


def test_set_stage_failed_keeps_progress() -> None:
    """失败时**保留**进度、不归零 —— 知道"死在 70%"比归零更有排查价值。"""
    task = make_task()
    gs.GenerationService._set_stage(FakeDbHolder.db, task, AgentStage.GENERATING)
    assert task.progress == 70

    gs.GenerationService._set_stage(
        FakeDbHolder.db, task, AgentStage.FAILED, detail="模型输出被截断"
    )

    assert task.stage == AgentStage.FAILED.value
    assert task.progress == 70, "失败不应该把已完成的进度清掉"
    assert task.stage_detail == "模型输出被截断"


# --------------------------------------------------------------------------
# 3. worker 侧执行（execute_pipeline）
# --------------------------------------------------------------------------


def test_execute_pipeline_success_writes_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功路径：阶段走到 done、结果与用量落库、session 被关掉。"""
    task = make_task()
    FakeRepo.task = task
    monkeypatch.setitem(gs._GENERATORS, "single", _ok_generator)

    gs.GenerationService.execute_pipeline(task.task_uuid)

    assert task.status == "success"
    assert task.stage == AgentStage.DONE.value
    assert task.progress == 100
    assert json.loads(task.file_list or "[]") == ["index.html"]
    assert (task.input_tokens, task.output_tokens, task.reasoning_tokens) == (10, 20, 5)
    assert task.duration_ms is not None
    assert FakeDbHolder.db.closed is True, "worker 侧必须自己关 session"


def test_execute_pipeline_records_expected_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """可预期的失败：标 failed + 记原因 + **仍然记录 token 用量**，且不向上抛。"""

    def _failing(prompt: str) -> GenerationResult:
        raise GenerationFailedError(
            "模型输出被 max_tokens 截断",
            ModelUsage(input_tokens=100, output_tokens=200, reasoning_tokens=150),
            raw_output="半截 html",
        )

    task = make_task()
    FakeRepo.task = task
    monkeypatch.setitem(gs._GENERATORS, "single", _failing)

    gs.GenerationService.execute_pipeline(task.task_uuid)  # 不应抛异常

    assert task.status == "failed"
    assert task.stage == AgentStage.FAILED.value
    assert "截断" in (task.error_msg or "")
    assert task.input_tokens == 100, "失败的任务同样烧了 token，必须记账"
    assert task.reasoning_tokens == 150


def test_execute_pipeline_reraises_unexpected_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """意外异常：先落 failed 终态，再上抛给 arq（便于 j_failed 统计与日志排查）。"""

    def _boom(prompt: str) -> GenerationResult:
        raise RuntimeError("连接模型服务超时")

    task = make_task()
    FakeRepo.task = task
    monkeypatch.setitem(gs._GENERATORS, "single", _boom)

    with pytest.raises(RuntimeError, match="超时"):
        gs.GenerationService.execute_pipeline(task.task_uuid)

    assert task.status == "failed", "上抛之前必须先写终态，否则任务会永远停在 running"
    assert task.stage == AgentStage.FAILED.value
    assert "超时" in (task.error_msg or "")


def test_execute_pipeline_marks_unknown_gen_type_failed() -> None:
    """库里存着未实现的生成类型时，也要收成 failed 终态，不能静默返回。"""
    task = make_task(gen_type="legacy")
    FakeRepo.task = task

    gs.GenerationService.execute_pipeline(task.task_uuid)

    assert task.status == "failed"
    assert task.stage == AgentStage.FAILED.value


def test_execute_pipeline_skips_missing_task() -> None:
    """任务不存在（已删除 / uuid 有误）：直接返回，不写库，但 session 仍要关。"""
    FakeRepo.task = None
    gs.GenerationService.execute_pipeline("nonexistent")

    assert FakeRepo.updated == []
    assert FakeDbHolder.db.closed is True


# --------------------------------------------------------------------------
# 4. 僵尸回收
# --------------------------------------------------------------------------


def test_recover_zombies_marks_stale_running_failed() -> None:
    """僵尸任务应被标 failed，且进度保留、updateTime 被刷新。"""
    zombie = make_task()
    zombie.progress = 70
    FakeRepo.stale = [zombie]

    count = gs.GenerationService.recover_zombies(FakeDbHolder.db, grace_seconds=300)

    assert count == 1
    assert zombie.status == "failed"
    assert zombie.stage == AgentStage.FAILED.value
    assert zombie.progress == 70
    assert "中断" in (zombie.error_msg or "")


def test_recover_zombies_noop_when_none() -> None:
    """没有僵尸时不应产生任何写操作。"""
    FakeRepo.stale = []
    count = gs.GenerationService.recover_zombies(FakeDbHolder.db, grace_seconds=300)

    assert count == 0
    assert FakeDbHolder.db.commits == 0


# --------------------------------------------------------------------------
# 5. 提交入口（create）：排队 / 去重 / 队列不可用
# --------------------------------------------------------------------------


def test_create_returns_accepted_and_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    """提交后应立刻返回 202 内容，并以 task_uuid 作为 job_id 投进队列。"""
    pool = FakePool()
    monkeypatch.setattr(gs, "init_pool", lambda: _async_value(pool))

    response = asyncio.run(
        gs.GenerationService.create(
            FakeDbHolder.db, 7, GenerateRequest(prompt="做一个待办清单", gen_type="single")
        )
    )

    assert response.status == "running"
    assert response.stage == AgentStage.QUEUED.value
    assert response.stage_text == "排队中"
    assert response.progress == 0
    assert response.poll_url == f"/api/generation/{response.task_uuid}"
    assert response.poll_interval_ms == agent_settings.agent_poll_interval_ms

    name, args, kwargs = pool.calls[0]
    assert name == "run_generation"
    assert args == (response.task_uuid,), "队列里只传 task_uuid，不传 payload"
    assert kwargs["_job_id"] == response.task_uuid, "_job_id 用于入队去重"


def test_create_marks_failed_when_queue_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis 不可用：任务必须当场标 failed + 返回 503，不能让它永远"排队中"。"""
    pool = FakePool(enqueue_error=ConnectionError("redis 连接被拒绝"))
    monkeypatch.setattr(gs, "init_pool", lambda: _async_value(pool))

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            gs.GenerationService.create(
                FakeDbHolder.db, 7, GenerateRequest(prompt="做一个待办清单", gen_type="single")
            )
        )

    assert excinfo.value.status_code == 503
    created = FakeRepo.created[-1]
    assert created.status == "failed"
    assert created.stage == AgentStage.FAILED.value
    assert "入队失败" in (created.error_msg or "")


def test_create_rejects_unimplemented_gen_type() -> None:
    """未实现的生成类型应在建任务之前就被挡掉（501），不留垃圾记录。"""
    # model_construct 绕过 Pydantic 的 Literal 校验，模拟"库里/旧客户端传来的脏值"
    dirty = GenerateRequest.model_construct(prompt="随便什么", gen_type="nope")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(gs.GenerationService.create(FakeDbHolder.db, 7, dirty))

    assert excinfo.value.status_code == 501
    assert FakeRepo.created == [], "类型不支持时不该建任务"


# --------------------------------------------------------------------------
# 6. 成本护栏（回归保护：这几条被改动会直接烧钱）
# --------------------------------------------------------------------------


def test_worker_settings_guardrails() -> None:
    """worker 的关键配置必须守死：重试次数、结果保留、超时、任务名。"""
    assert WorkerSettings.max_tries == 1, (
        "arq 默认 max_tries=5 且采用悲观执行（worker 重启会重跑）；"
        "LLM 按 token 计费，自动重试等于重复烧钱 —— 必须保持 1"
    )
    assert WorkerSettings.keep_result == 0, "结果不存 Redis，状态真源是 MySQL"
    assert WorkerSettings.job_timeout == agent_settings.agent_job_timeout_seconds
    assert [f.name for f in WorkerSettings.functions] == ["run_generation"]


def test_zombie_grace_is_positive() -> None:
    """宽限期必须为正：多 worker 场景下 0 宽限会误杀正在跑的任务。"""
    assert agent_settings.agent_zombie_grace_seconds > 0


def test_stale_deadline_is_in_the_past() -> None:
    """防御性用例：宽限期换算出的 deadline 一定落在过去。"""
    deadline = datetime.now() - timedelta(seconds=agent_settings.agent_zombie_grace_seconds)

    assert deadline < datetime.now()
