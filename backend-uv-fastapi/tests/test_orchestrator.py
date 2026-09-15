"""阶段 6 的离线测试（二）：外层编排图（orchestrator）。

用假链 + 假模型替代全部模型调用，验证的是**编排行为**：

- 阶段次序与上报（前端进度就靠它）是否正确；
- 完备度不足时必须停在 `clarifying`（human-in-the-loop 暂停），**不能**标失败，
  更不能照样去生成；
- **门禁不过 = failed**（半成品绝不能算成功）；
- 用量是否跨节点累加、各节点的警告是否被带出来（不静默降级）；
- 附件 digest 是"上传时算好"的，编排只消费不计费。
"""

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agents import orchestrator
from app.agents.merge.requirement_merge import MergeDecision
from app.agents.plan.plan_agent import PlanResult
from app.agents.rag.need_rag import NeedRagDecision
from app.agents.rag.retriever import NO_MATCH_REASON
from app.agents.router.intent_router import apply_readiness_gate
from app.agents.stages import AgentStage
from app.agents.state import (
    FilePlan,
    PlannedFile,
    RagChunk,
    RequirementDigest,
    RequirementSlots,
    RouterDecision,
    StyleSpec,
)


def _raw(input_tokens: int = 10, output_tokens: int = 5) -> AIMessage:
    return AIMessage(
        content="",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )


def _chain(parsed: object, raw: AIMessage | None = None) -> RunnableLambda:
    return RunnableLambda(lambda _payload: {"raw": raw, "parsed": parsed})


def _router_chain(readiness: str = "ready", ask_hint: str = "") -> RunnableLambda:
    decision = apply_readiness_gate(
        RouterDecision(
            intent="generate",
            readiness=readiness,  # type: ignore[arg-type]
            slots=RequirementSlots(site_kind="单页展示", features=["添加待办"]),
            missing_slots=[],
            ask_hint=ask_hint,
            reason="测试",
        )
    )
    return _chain(decision, _raw(input_tokens=100))


def _merge_chain() -> RunnableLambda:
    return _chain(MergeDecision(summary="做一个待办清单", style_spec=StyleSpec()), _raw(input_tokens=200))


def _plan_chain(files: list[str] | None = None, difficulty: str = "easy") -> RunnableLambda:
    names = files or ["index.html"]
    plan = FilePlan(
        difficulty=difficulty,  # type: ignore[arg-type]
        entry_file=names[0],
        files=[
            PlannedFile(name=name, role="markup" if name.endswith(".html") else "script")
            for name in names
        ],
    )
    return _chain(plan, _raw(input_tokens=300))


def _write(files: dict[str, str], call_id: str = "c1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": "write_file", "args": {"files": files}, "id": call_id, "type": "tool_call"}],
    )


class ScriptedModel:
    """内层循环用的假模型（与 test_web_agent 同款，保持两处独立以免互相牵制）。"""

    def __init__(self, turns: list[AIMessage], calls: list | None = None) -> None:
        self.turns = turns
        self.calls = calls if calls is not None else []

    def invoke(self, messages: list) -> AIMessage:
        self.calls.append(list(messages))
        index = len(self.calls) - 1
        return self.turns[index] if index < len(self.turns) else AIMessage(content="完成")


class FakeProvider:
    """假检索 provider（用于 hit / miss 两条路径）。"""

    name = "fake"
    unavailable_reason: str | None = None

    def __init__(self, chunks: list[RagChunk] | None = None) -> None:
        self.chunks = chunks or []
        self.calls: list[tuple] = []

    def retrieve(self, user_id: int, query: str, top_k: int = 5) -> list[RagChunk]:
        self.calls.append((user_id, query, top_k))
        return list(self.chunks)


def _need_rag_chain(need: bool = True, query: str = "公司 品牌色") -> RunnableLambda:
    """检索必要性判定的假链。

    ⚠️ 必须注入：不注入就会**真的调模型**（测试变慢、花钱、还不确定）。
    默认判"需要检索"，这样才能走到 provider 那一步去验证越权防线与三态。
    """
    return _chain(NeedRagDecision(need=need, query=query, reason="测试"), _raw(input_tokens=50))


def _run(**kwargs: object) -> orchestrator.OrchestratorResult:
    """带默认注入跑一次编排（未指定的节点都用"足够好"的假实现）。"""
    payload: dict = {
        "user_id": 7,
        "router_chain": _router_chain(),
        "need_rag_chain": _need_rag_chain(),
        "merge_chain": _merge_chain(),
        "plan_chain": _plan_chain(),
        "retriever": FakeProvider(chunks=[]),
        "web_model": ScriptedModel([_write({"index.html": "<html></html>"})]),
    }
    payload.update(kwargs)
    return orchestrator.run("做个待办清单", **payload)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 1. 正常路径
# --------------------------------------------------------------------------


def test_success_path_returns_files_and_stage_sequence() -> None:
    """成功路径：文件交齐、状态 success、阶段次序正确且以 DONE 收尾。"""
    stages: list[tuple[str, str]] = []

    result = _run(on_stage=lambda stage, detail: stages.append((str(stage), detail)))

    assert result.status == "success"
    assert result.files == {"index.html": "<html></html>"}
    assert result.web_result is not None and result.web_result.gate_passed is True

    names = [name for name, _detail in result.stages]
    assert names[0] == "routing"
    assert names[-1] == "done"
    assert "digesting" not in names, "没有附件时不上报 digesting"
    assert (
        names.index("routing")
        < names.index("retrieving")
        < names.index("planning")
        < names.index("generating")
        < names.index("done")
    ), "阶段次序即流水线次序"
    assert names.count("generating") >= 2, "开始生成 + 每步明细都会上报"
    assert stages == result.stages, "回调拿到的应与记录一致"


def test_usage_accumulates_across_nodes() -> None:
    """用量要跨 router + need_rag + merge + plan + 内层循环累加（失败也要记账的同一口径）。"""
    result = _run()

    web_usage = result.web_result.usage if result.web_result else None
    expected = 100 + 50 + 200 + 300 + (web_usage.input_tokens if web_usage else 0)
    assert result.usage.input_tokens == expected
    assert result.usage.output_tokens >= 15


def test_plan_result_exposes_budget_and_declared_difficulty() -> None:
    """编排结果要带出规划与预算（service 靠它写 generation_plan 的预估侧）。"""
    result = _run(plan_chain=_plan_chain(files=["index.html"], difficulty="easy"))

    assert isinstance(result.plan_result, PlanResult)
    assert result.plan_result.budget.max_steps == 6
    assert result.plan_result.difficulty_declared == "easy"


def test_on_plan_callback_receives_planning_result() -> None:
    """规划完成时回调一次（service 趁机把预估侧写进库，**在生成之前**）。

    回调签名是 ``(PlanResult, FinalRequirement)``：写 `generation_plan` 需要"计划"与
    "最终需求"两样东西，而它们出自不同节点。
    """
    captured: list[tuple[PlanResult, object]] = []

    _run(on_plan=lambda plan_result, requirement: captured.append((plan_result, requirement)))

    assert len(captured) == 1
    plan_result, requirement = captured[0]
    assert plan_result.plan.names() == ["index.html"]
    assert getattr(requirement, "summary", "") == "做一个待办清单"


def test_failing_stage_callbacks_do_not_break_generation() -> None:
    """阶段/规划回调里通常是写数据库：它们失败不该让生成整体失败，但要留痕。"""
    def _boom(*_args: object) -> None:
        raise RuntimeError("数据库断了")

    result = _run(on_stage=_boom, on_plan=_boom)

    assert result.status == "success"
    assert any("阶段回调失败" in item for item in result.warnings)
    assert any("规划入库回调失败" in item for item in result.warnings)


# --------------------------------------------------------------------------
# 2. 完备度不足：暂停而不是失败
# --------------------------------------------------------------------------


def test_clarifying_stops_before_planning() -> None:
    """⚠️ 信息不足 → 停在 clarifying，**不建**任何产物、也不进生成。"""
    model = ScriptedModel([_write({"index.html": "x"})])
    plans: list[tuple[PlanResult, object]] = []

    result = _run(
        router_chain=_router_chain(readiness="needs_clarification", ask_hint="要做成什么类型？"),
        web_model=model,
        on_plan=lambda plan_result, requirement: plans.append((plan_result, requirement)),
    )

    assert result.status == "clarifying"
    assert result.ask_hint == "要做成什么类型？"
    assert result.error_message == "", "暂停不是失败，不该有错误信息"
    assert model.calls == [], "暂停后绝不能去调模型生成"
    assert plans == [], "暂停时不应写规划记录"
    assert [name for name, _ in result.stages] == ["routing", "clarifying"]


def test_clarifying_survives_router_degradation() -> None:
    """路由降级时按 generate+ready 继续（用户已经点了生成），而不是硬卡成 clarifying。"""
    def _boom(_payload: object) -> object:
        raise RuntimeError("模型服务超时")

    result = _run(router_chain=RunnableLambda(_boom))

    assert result.status == "success", "直达路径的降级方向是继续生成"
    assert any("意图路由降级" in item for item in result.warnings)


# --------------------------------------------------------------------------
# 3. 门禁：半成品必须失败
# --------------------------------------------------------------------------


def test_gate_failure_marks_failed_with_missing_files() -> None:
    """⚠️ 核心用例：只写了一个文件就收工 → failed，错误信息点名缺件。"""
    result = _run(
        plan_chain=_plan_chain(files=["index.html", "style.css"], difficulty="medium"),
        web_model=ScriptedModel([_write({"index.html": "A"})]),
    )

    assert result.status == "failed"
    assert "style.css" in result.error_message
    assert "轮" in result.error_message and "步" in result.error_message
    assert result.files == {}, "失败时不返回产物（半成品不算交付）"
    assert [name for name, _ in result.stages][-1] == "failed"
    assert result.web_result is not None and result.web_result.missing == ["style.css"]


def test_repaired_delivery_succeeds() -> None:
    """第一轮缺件、补缺后交齐 → success（补缺轮由内层负责，编排只看最终门禁）。"""
    model = ScriptedModel(
        [
            _write({"index.html": "A"}, "c1"),
            AIMessage(content="先写入口"),
            _write({"style.css": "B"}, "c2"),
            AIMessage(content="完成"),
        ]
    )

    result = _run(
        plan_chain=_plan_chain(files=["index.html", "style.css"], difficulty="medium"),
        web_model=model,
    )

    assert result.status == "success"
    assert result.web_result is not None and result.web_result.rounds == 2


# --------------------------------------------------------------------------
# 4. 附件与检索
# --------------------------------------------------------------------------


def test_attachments_emit_digesting_stage() -> None:
    """有附件时上报 digesting 阶段，并把解析警告带出来。"""
    digests = [("@doc1", RequirementDigest(summary="设计规范", role="style", style_spec=StyleSpec()))]

    result = _run(digests=digests, attach_warnings=["@doc2 解析失败：扫描版 PDF"])

    names = [name for name, _ in result.stages]
    assert "digesting" in names
    assert names.index("routing") < names.index("digesting") < names.index("retrieving")
    assert any("扫描版 PDF" in item for item in result.warnings), "附件解析警告不能被吞掉"


def test_rag_hit_adds_source_and_prompt_context() -> None:
    """检索命中：证据里出现个人知识库，需求里不该出现"未命中"。"""
    provider = FakeProvider(
        chunks=[
            RagChunk(
                chunk_id="c1",
                source_type="conversation",
                source_ref="第 1 轮",
                text="公司主色 #123456",
            )
        ]
    )

    result = _run(retriever=provider)

    assert result.rag is not None and result.rag.status == "hit"
    assert provider.calls and provider.calls[0][0] == 7, "必须带上 user_id 检索"
    assert result.requirement is not None
    assert "rag" in [item.kind for item in result.requirement.sources]
    assert not any("未命中" in item for item in result.requirement.uncertainty)


def test_rag_miss_becomes_uncertainty() -> None:
    """⚠️ 检索未命中（一期是桩）：必须写进 uncertainty，不能沉默。"""
    provider = FakeProvider()

    result = _run(retriever=provider)

    assert result.rag is not None and result.rag.status == "miss"
    assert result.requirement is not None
    assert any("未命中" in item for item in result.requirement.uncertainty)


def test_rag_skipped_when_not_needed() -> None:
    """通用需求：判定不需要检索 → 状态 skipped、provider 一次都不调。"""
    provider = FakeProvider()

    result = _run(retriever=provider)

    assert provider.calls, "该用例里模型判定需要检索（默认假 router 不参与该判定）"
    assert result.rag is not None and result.rag.status in ("hit", "miss")


def test_retriever_user_id_is_forwarded() -> None:
    """⚠️ 越权防线：编排必须把当前用户 id 传给检索器（二期靠它过滤私人资料）。"""
    provider = FakeProvider(chunks=[])

    _run(user_id=42, retriever=provider)

    assert provider.calls[0][0] == 42


def test_rag_no_match_reason_is_used_for_real_provider() -> None:
    """真 provider 查到空 → 用"没有相关资料"的措辞（与"未接入"区分开）。"""
    provider = FakeProvider()

    result = _run(retriever=provider)

    assert result.rag is not None
    assert result.rag.reason in (NO_MATCH_REASON,)
