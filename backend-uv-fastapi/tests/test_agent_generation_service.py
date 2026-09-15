"""阶段 6 的离线测试（三）：agent 模式的落库编排（AgentGenerationService）。

这一层是"纯编排结果"与"真实副作用"之间的桥，因此测的是**状态机与两个库的写入**：

- `success` / `clarifying` / `failed` 三种状态的处理**必须不同**：
  澄清是"暂停等人"（任务保持 running、不写 duration、不算失败），
  门禁不过才是失败（写 error_msg 与用量）；
- **实际值的回填**：`generation_plan` 的 actual_steps / actual_file_count / outcome_status
  就是"预估 vs 实际"对账的另一半，漏了它阶段 5 建的那张表就只剩一半信息；
- **附件按会话取回**，解析失败的附件要变成警告而不是静默丢弃。
"""

import json

import pytest
from fastapi import HTTPException

import app.services.agent_generation_service as ags
from app.agents import orchestrator
from app.agents.common import ModelUsage
from app.agents.plan.plan_agent import PlanResult
from app.agents.stages import AgentStage
from app.agents.state import FinalRequirement, RequirementSlots
from app.agents.web.web_agent import WebAgentResult
from app.agents.common import budget_for
from app.agents.state import FilePlan, PlannedFile
from app.models.agent import AgentSession, GenerationPlan, GenerationSource
from app.models.generation_task import GenerationTask
from app.services.agent_generation_service import AgentGenerationService


# --------------------------------------------------------------------------
# 测试替身
# --------------------------------------------------------------------------


class FakeSessionRepo:
    store: dict[str, AgentSession] = {}

    @classmethod
    def reset(cls) -> None:
        cls.store = {}

    @classmethod
    def get_by_uuid(cls, db: object, session_uuid: str) -> AgentSession | None:
        return cls.store.get(session_uuid)


class FakeSourceRepo:
    rows: list[GenerationSource] = []

    @classmethod
    def reset(cls) -> None:
        cls.rows = []

    @classmethod
    def list_by_session(cls, db: object, session_id: int) -> list[GenerationSource]:
        return [row for row in cls.rows if row.session_id == session_id and row.is_delete == 0]


class FakePlanRepo:
    created: list[GenerationPlan] = []
    outcomes: list[dict] = []

    @classmethod
    def reset(cls) -> None:
        cls.created = []
        cls.outcomes = []

    @classmethod
    def create(cls, db: object, plan: GenerationPlan) -> GenerationPlan:
        plan.id = len(cls.created) + 1
        cls.created.append(plan)
        return plan

    @classmethod
    def mark_outcome(cls, db: object, plan: GenerationPlan, **kwargs: object) -> GenerationPlan:
        cls.outcomes.append({"plan": plan, **kwargs})
        return plan


class FakePg:
    """假的 PG 会话（本层只把它透传给 repository）。"""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _session(session_uuid: str = "sess1", user_id: int = 1) -> AgentSession:
    session = AgentSession(session_uuid=session_uuid, user_id=user_id, status="active")
    session.id = 1
    return session


def _source(alias: str = "@doc1", **kwargs: object) -> GenerationSource:
    payload: dict = {
        "source_uuid": f"uuid-{alias}",
        "user_id": 1,
        "session_id": 1,
        "alias": alias,
        "display_name": "需求说明.md",
        "role": "content",
        "parse_status": "success",
        "digest": {"summary": "一份需求说明", "role": "content"},
        "is_delete": 0,
    }
    payload.update(kwargs)
    return GenerationSource(**payload)  # type: ignore[arg-type]


def _task(**kwargs: object) -> GenerationTask:
    payload: dict = {
        "task_uuid": "task1",
        "user_id": 1,
        "prompt": "做一个待办清单",
        "gen_type": "agent",
        "session_uuid": "sess1",
        "status": "running",
    }
    payload.update(kwargs)
    task = GenerationTask(**payload)  # type: ignore[arg-type]
    task.id = 1
    return task


PLAN = FilePlan(
    difficulty="medium",
    entry_file="index.html",
    files=[
        PlannedFile(name="index.html", role="markup"),
        PlannedFile(name="style.css", role="style"),
    ],
)

REQUIREMENT = FinalRequirement(summary="做一个待办清单", slots=RequirementSlots(site_kind="单页展示"))


def _plan_result() -> PlanResult:
    return PlanResult(
        plan=PLAN,
        difficulty_declared="easy",
        budget=budget_for("medium"),
        warnings=["模型声明难度为 easy，但计划交付 2 个文件，已按 medium 上调预算"],
    )


def _web_result(steps: int = 5, files: dict[str, str] | None = None) -> WebAgentResult:
    files = files if files is not None else {"index.html": "A", "style.css": "B"}
    return WebAgentResult(
        files=files,
        missing=[],
        gate_passed=True,
        steps_used=steps,
        rounds=1,
        stop_reason="done",
        usage=ModelUsage(input_tokens=10, output_tokens=5),
        trace_jsonl='{"step": 1, "detail": "write_file"}',
    )


def _result(status: str = "success", **kwargs: object) -> orchestrator.OrchestratorResult:
    payload: dict = {
        "status": status,
        "usage": ModelUsage(input_tokens=500, output_tokens=200, reasoning_tokens=30),
        "requirement": REQUIREMENT,
        "plan_result": _plan_result(),
        "web_result": _web_result(),
    }
    if status == "success":
        payload["files"] = {"index.html": "A", "style.css": "B"}
    if status == "failed":
        payload["files"] = {}
        payload["error_message"] = "生成的文件不完整（缺少：style.css）；已尝试 3 轮、共 6 步"
        payload["web_result"] = WebAgentResult(
            files={"index.html": "A"},
            missing=["style.css"],
            gate_passed=False,
            steps_used=6,
            rounds=3,
            stop_reason="done",
            usage=ModelUsage(input_tokens=10, output_tokens=5),
            trace_jsonl='{"step": 1}',
        )
    if status == "clarifying":
        payload["files"] = {}
        payload["ask_hint"] = "请说明要做成什么类型的页面"
        payload["web_result"] = None
        payload["plan_result"] = None
        payload["requirement"] = None
    payload.update(kwargs)
    return orchestrator.OrchestratorResult(**payload)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _fakes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """把所有外部依赖换成替身：两个库、文件落盘、阶段写入、编排本身。"""
    FakeSessionRepo.reset()
    FakeSourceRepo.reset()
    FakePlanRepo.reset()
    FakeSessionRepo.store["sess1"] = _session()

    monkeypatch.setattr(ags, "PgSessionLocal", FakePg)
    monkeypatch.setattr(ags, "AgentSessionRepository", FakeSessionRepo)
    monkeypatch.setattr(ags, "GenerationSourceRepository", FakeSourceRepo)
    monkeypatch.setattr(ags, "GenerationPlanRepository", FakePlanRepo)

    from app.services import generation_service

    monkeypatch.setattr(
        generation_service.GenerationService,
        "_set_stage",
        staticmethod(lambda db, task, stage, detail=None: _record_stage(task, stage, detail)),
    )
    monkeypatch.setattr(
        generation_service.GenerationService,
        "_fail",
        staticmethod(
            lambda db, task, message, started, usage=None: _record_fail(task, message, usage)
        ),
    )
    # 落盘改成真的写临时目录（既验证路径拼接，又不污染产物目录）
    monkeypatch.setattr(
        ags, "write_files", lambda user_id, task_uuid, files: f"{user_id}/{task_uuid}"
    )
    monkeypatch.setattr(
        ags,
        "write_debug_trace",
        lambda user_id, task_uuid, jsonl: _record_trace(user_id, task_uuid, jsonl),
    )
    # 诊断文件也要拦下来：否则测试会把 _debug_meta.json 写进真实产物目录
    monkeypatch.setattr(
        ags,
        "write_debug_meta",
        lambda user_id, task_uuid, payload: _record_meta(user_id, task_uuid, payload),
    )
    _STAGES.clear()
    _FAILS.clear()
    _TRACES.clear()
    _METAS.clear()


_STAGES: list[tuple[str, str | None, str]] = []
_FAILS: list[tuple[str, str, object]] = []
_TRACES: list[str] = []
_METAS: list[dict] = []


def _record_meta(user_id: int, task_uuid: str, payload: dict | None) -> str | None:
    if payload:
        _METAS.append(payload)
    return None


def _record_stage(task: GenerationTask, stage: AgentStage, detail: str | None) -> None:
    _STAGES.append((task.task_uuid, stage.value, detail))


def _record_fail(task: GenerationTask, message: str, usage: object) -> None:
    _FAILS.append((task.task_uuid, message, usage))
    task.status = "failed"
    task.error_msg = message[:1000]


def _record_trace(user_id: int, task_uuid: str, jsonl: str | None) -> str | None:
    if jsonl:
        _TRACES.append(jsonl)
    return None


def _fake_run(result: orchestrator.OrchestratorResult):
    """假编排：返回给定结果，并**像真编排一样触发 on_plan 回调**。

    ⚠️ 必须触发回调：`generation_plan` 的预估侧正是通过它写入的，
    只返回结果的话，这一层的写库逻辑就完全没被测到。
    """

    def _run(*_args: object, **kwargs: object) -> orchestrator.OrchestratorResult:
        on_plan = kwargs.get("on_plan")
        if callable(on_plan) and result.plan_result is not None and result.requirement is not None:
            on_plan(result.plan_result, result.requirement)
        return result

    return _run


# --------------------------------------------------------------------------
# 1. 成功路径
# --------------------------------------------------------------------------


def test_success_writes_artifacts_and_finishes_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """成功：落盘 + 写 file_list/用量/耗时 + 阶段 DONE，并把实际值回填到规划记录。"""
    monkeypatch.setattr(orchestrator, "run", _fake_run(_result("success")))
    task = _task()

    AgentGenerationService.execute(db=object(), task=task, started=0.0)

    assert task.status == "success"
    assert task.result_dir == "1/task1"
    assert json.loads(task.file_list or "[]") == ["index.html", "style.css"]
    assert (task.input_tokens, task.output_tokens, task.reasoning_tokens) == (500, 200, 30)
    assert task.duration_ms is not None
    assert ("task1", "done", "已交付全部文件") in _STAGES
    assert _TRACES, "trace 要落盘（Agent 的失败常常藏在第 N 步）"


def test_success_backfills_actual_values_into_plan_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ 核心用例：生成结束后把**实际值**写回 generation_plan（预估 vs 实际对账的另一半）。"""
    monkeypatch.setattr(orchestrator, "run", _fake_run(_result("success")))
    task = _task()

    AgentGenerationService.execute(db=object(), task=task, started=0.0)

    assert len(FakePlanRepo.created) == 1, "规划一完成就写预估侧"
    assert len(FakePlanRepo.outcomes) == 1
    outcome = FakePlanRepo.outcomes[0]
    assert outcome["actual_steps"] == 5
    assert outcome["actual_file_count"] == 2
    assert outcome["outcome_status"] == "success"


def test_plan_row_records_predicted_side(monkeypatch: pytest.MonkeyPatch) -> None:
    """预估侧要写全：模型原始难度、复核后难度、计划文件数、步数与 token 预算、警告。"""
    monkeypatch.setattr(orchestrator, "run", _fake_run(_result("success")))

    AgentGenerationService.execute(db=object(), task=_task(), started=0.0)

    row = FakePlanRepo.created[0]
    assert row.difficulty == "medium"
    assert row.difficulty_declared == "easy"
    assert row.planned_file_count == 2
    assert row.budget_steps == 12
    assert row.budget_output_tokens == 40_000
    assert row.final_requirement["summary"] == "做一个待办清单"
    assert row.file_plan["entry_file"] == "index.html"
    assert row.validation_warnings, "难度上调的警告要留着，便于优化提示词"


# --------------------------------------------------------------------------
# 2. 澄清：暂停而不是失败
# --------------------------------------------------------------------------


def test_clarifying_pauses_without_failing(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ 澄清是 human-in-the-loop 暂停：任务保持 running、不算失败、不写 duration。"""
    monkeypatch.setattr(orchestrator, "run", _fake_run(_result("clarifying")))
    task = _task()

    AgentGenerationService.execute(db=object(), task=task, started=0.0)

    assert task.status == "running", "暂停不是失败"
    assert task.error_msg is None
    assert task.duration_ms is None, "任务还没结束，不该有耗时"
    assert task.result_dir is None
    assert task.input_tokens == 500, "这次判定确实花了钱，用量照记"
    assert ("task1", "clarifying", "请说明要做成什么类型的页面") in _STAGES
    assert FakePlanRepo.created == [], "没走到规划就不该有规划记录"
    assert FakePlanRepo.outcomes == [], "更不该回填实际值"


# --------------------------------------------------------------------------
# 3. 失败：半成品必须失败
# --------------------------------------------------------------------------


def test_failed_records_error_message_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ 门禁不过 → 走 _fail，错误信息点名缺件，用量与实际值都要留下。"""
    monkeypatch.setattr(orchestrator, "run", _fake_run(_result("failed")))
    task = _task()

    AgentGenerationService.execute(db=object(), task=task, started=0.0)

    assert _FAILS and "style.css" in _FAILS[0][1]
    assert task.status == "failed"
    assert _TRACES, "失败时 trace 更要落盘"
    outcome = FakePlanRepo.outcomes[0]
    assert outcome["outcome_status"] == "failed"
    assert outcome["actual_steps"] == 6
    assert outcome["actual_file_count"] == 0, "失败时不返回产物，实际交付数为 0"


def test_failed_writes_structured_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ 失败必须留下**结构化诊断**（2026-09-15 的教训）。

    真实事故：某次生成只写出 2 个文件就失败，用户看到的是"生成的文件不完整（缺少 index.html、data.js）"，
    而真正的原因（某一轮模型调用异常）只存在于 `warnings` 里 —— 既没落盘也没入库，
    只能靠重放整条流水线去猜（重放还不一定复现）。所以这里断言它一定被写下来。
    """
    failed = _result("failed")
    failed.warnings = ["第 2 步模型调用失败（已重试 2 次）：TimeoutError: 模型服务超时"]
    monkeypatch.setattr(orchestrator, "run", _fake_run(failed))
    task = _task()

    AgentGenerationService.execute(db=object(), task=task, started=0.0)

    assert _METAS, "失败时要写 _debug_meta.json"
    meta = _METAS[0]
    assert meta["status"] == "failed"
    assert meta["rounds"] == 3, "跑了几轮要记下来（1 轮就失败 vs 补缺 3 轮完全不同）"
    assert meta["steps_used"] == 6
    assert meta["missing"] == ["style.css"]
    assert any("模型调用失败" in item for item in meta["warnings"])
    assert meta["usage"]["output_tokens"] == 200


def test_unexpected_exception_marks_failed_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    """意外异常必须先落终态再上抛（否则任务永远停在 running，只能等僵尸回收）。"""

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("编排崩了")

    monkeypatch.setattr(orchestrator, "run", _boom)
    task = _task()

    with pytest.raises(RuntimeError):
        AgentGenerationService.execute(db=object(), task=task, started=0.0)

    assert _FAILS and "编排崩了" in _FAILS[0][1]


# --------------------------------------------------------------------------
# 4. 附件按会话取回
# --------------------------------------------------------------------------


def test_sources_are_loaded_from_session() -> None:
    """成功解析的附件要变成 digests；解析失败的变成警告（不静默丢弃）。"""
    FakeSourceRepo.rows = [
        _source("@doc1"),
        _source("@doc2", parse_status="failed", digest=None, parse_error="扫描版 PDF"),
    ]
    session = FakeSessionRepo.store["sess1"]

    digests, warnings = AgentGenerationService._load_sources(FakePg(), _task(), session)

    assert [alias for alias, _digest in digests] == ["@doc1"]
    assert any("扫描版 PDF" in item for item in warnings)


def test_sources_are_empty_without_session() -> None:
    """不用会话（纯文本需求）时不取附件、也不产生警告。"""
    digests, warnings = AgentGenerationService._load_sources(
        FakePg(), _task(session_uuid=None), None
    )

    assert digests == []
    assert warnings == []


def test_foreign_session_yields_warning_not_data() -> None:
    """⚠️ 越权防线：会话不属于当前用户时**一条附件都不能读**。"""
    FakeSessionRepo.store["sess1"] = _session(user_id=999)
    FakeSourceRepo.rows = [_source("@doc1")]

    # _load_session 会把"不属于自己"挡成 None（模拟 worker 侧排队期间会话换主）
    session = AgentGenerationService._load_session(FakePg(), _task())

    assert session is None
    digests, warnings = AgentGenerationService._load_sources(FakePg(), _task(), session)

    assert digests == []
    assert any("不属于当前用户" in item for item in warnings)


def test_broken_digest_payload_is_skipped() -> None:
    """库里历史数据不合法时跳过该附件并记警告，不能拖垮整次生成。"""
    FakeSourceRepo.rows = [_source("@doc1", digest={"role": "不存在的角色"})]
    session = FakeSessionRepo.store["sess1"]

    digests, warnings = AgentGenerationService._load_sources(FakePg(), _task(), session)

    assert digests == []
    assert any("无法还原" in item for item in warnings)


# --------------------------------------------------------------------------
# 5. 会话需求草稿 → worker 侧判定（2026-09-15 新增）
# --------------------------------------------------------------------------


def test_execute_forwards_session_draft_to_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ 核心用例：会话里**用户已确认的草稿**必须传给 orchestrator 的 slots / draft_summary。

    背景（真实 bug）：worker 的 ROUTING 拿不到会话历史，若只给一句 prompt，
    它会脱离"用户在界面上确认过的槽位"重新判一遍完备度 ——
    于是出现"确认卡片全绿、任务却停在 clarifying"的自相矛盾。
    这两个参数同时是 ⑩ need_rag 与 ⑪ merge 的输入，此前在 agent 路径上一直是空的。
    """
    FakeSessionRepo.store["sess1"].draft_requirement = {
        "slots": {
            "site_kind": "单页展示",
            "features": ["四季主题动态切换"],
            "style": "沉浸式、氛围感",
        },
        "summary": "类型是单页展示；功能包含四季主题动态切换；风格是沉浸式、氛围感",
    }
    captured: dict[str, object] = {}

    def _capture(*_args: object, **kwargs: object) -> orchestrator.OrchestratorResult:
        captured.update(kwargs)
        return _result("success")

    monkeypatch.setattr(orchestrator, "run", _capture)

    AgentGenerationService.execute(db=object(), task=_task(), started=0.0)

    slots = captured["slots"]
    assert slots is not None and slots.site_kind == "单页展示"
    assert slots.features == ["四季主题动态切换"]
    assert "四季主题动态切换" in str(captured["draft_summary"])


def test_execute_without_draft_passes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """会话还没有草稿时传 None（提示词据此渲染"这是第一轮"），而不是一个空槽位对象。"""
    captured: dict[str, object] = {}

    def _capture(*_args: object, **kwargs: object) -> orchestrator.OrchestratorResult:
        captured.update(kwargs)
        return _result("success")

    monkeypatch.setattr(orchestrator, "run", _capture)

    AgentGenerationService.execute(db=object(), task=_task(), started=0.0)

    assert captured["slots"] is None
    assert captured["draft_summary"] == ""


def test_broken_draft_becomes_warning_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """库里草稿不合法（历史脏数据）→ 按"没有草稿"处理并留痕，绝不拖垮生成。"""
    FakeSessionRepo.store["sess1"].draft_requirement = {"slots": {"need_persistence": "不是布尔"}}
    captured: dict[str, object] = {}

    def _capture(*_args: object, **kwargs: object) -> orchestrator.OrchestratorResult:
        captured.update(kwargs)
        return _result("success")

    monkeypatch.setattr(orchestrator, "run", _capture)

    AgentGenerationService.execute(db=object(), task=_task(), started=0.0)

    assert captured["slots"] is None
    assert any("草稿无法解析" in item for item in captured["attach_warnings"])


# --------------------------------------------------------------------------
# 6. 会话归属校验（建任务之前）
# --------------------------------------------------------------------------


def test_validate_session_accepts_owner() -> None:
    """自己的会话 → 放行。"""
    AgentGenerationService.validate_session(1, "sess1")


def test_validate_session_rejects_missing_and_foreign() -> None:
    """不存在与别人的会话一律 404（不泄露"这个 uuid 存在"）。"""
    with pytest.raises(HTTPException) as excinfo:
        AgentGenerationService.validate_session(1, "nope")
    assert excinfo.value.status_code == 404

    FakeSessionRepo.store["sess1"] = _session(user_id=999)
    with pytest.raises(HTTPException) as excinfo:
        AgentGenerationService.validate_session(1, "sess1")
    assert excinfo.value.status_code == 404


def test_validate_session_skips_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """不传会话（纯文本需求）时**连库都不该连**。"""

    def _explode() -> object:
        raise AssertionError("不传会话时不该连库")

    monkeypatch.setattr(ags, "PgSessionLocal", _explode)

    AgentGenerationService.validate_session(1, None)
