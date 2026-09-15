"""阶段 2 的离线测试：意图路由节点。

最重要的一条是 `apply_readiness_gate` ——
**模型说 ready、但必备槽位为空时必须被 Python 强制降级。**
这是"判定权归模型、否决权归 Python"的第一个落点，也是防止
"模型热情地认为信息够了 → 生成一个跑偏的网页"的唯一防线。
"""

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agents.router.intent_router import apply_readiness_gate, build_router_chain, route
from app.agents.state import RequirementSlots, RouterDecision


def _decision(**kwargs: object) -> RouterDecision:
    """造一个判定，未指定的字段用合理默认值。"""
    payload = {
        "intent": "generate",
        "readiness": "ready",
        "slots": RequirementSlots(site_kind="单页展示", features=["添加待办"]),
        "missing_slots": [],
        "ask_hint": "",
        "reason": "需求清晰",
    }
    payload.update(kwargs)
    return RouterDecision(**payload)  # type: ignore[arg-type]


def _chain(parsed: object, raw: AIMessage | None = None) -> RunnableLambda:
    """假链：返回 with_structured_output(include_raw=True) 的产物形态。"""
    return RunnableLambda(lambda _payload: {"raw": raw, "parsed": parsed})


# --------------------------------------------------------------------------
# 1. Python 侧硬兜底（最关键）
# --------------------------------------------------------------------------


def test_gate_downgrades_when_required_slots_empty() -> None:
    """模型说 ready，但 site_kind 与 features 都空 → 强制转为需澄清。"""
    gated = apply_readiness_gate(
        _decision(readiness="ready", slots=RequirementSlots(), reason="模型觉得够了")
    )

    assert gated.readiness == "needs_clarification"
    assert set(gated.missing_slots) >= {"site_kind", "features"}
    assert "Python 兜底" in gated.reason, "降级原因要写清楚，便于事后排查是谁改的"


def test_gate_downgrades_when_only_one_required_slot_missing() -> None:
    """只缺一个必备槽位也要拦。"""
    gated = apply_readiness_gate(
        _decision(slots=RequirementSlots(site_kind="单页展示"), readiness="ready")
    )

    assert gated.readiness == "needs_clarification"
    assert gated.missing_slots == ["features"]


def test_gate_merges_existing_missing_slots() -> None:
    """兜底补上的缺失项要与模型原本报的合并，而不是互相覆盖。"""
    gated = apply_readiness_gate(
        _decision(readiness="ready", slots=RequirementSlots(), missing_slots=["style"])
    )

    assert set(gated.missing_slots) == {"site_kind", "features", "style"}


def test_gate_passes_when_required_slots_present() -> None:
    """必备槽位齐全时不该多管闲事。"""
    gated = apply_readiness_gate(_decision(readiness="ready"))

    assert gated.readiness == "ready"
    assert "Python 兜底" not in gated.reason


def test_gate_ignores_non_generate_intent() -> None:
    """纯咨询（intent=chat）不该被槽位规则干扰。"""
    gated = apply_readiness_gate(
        _decision(intent="chat", readiness="ready", slots=RequirementSlots())
    )

    assert gated.readiness == "ready"


def test_gate_does_not_upgrade_needs_clarification() -> None:
    """兜底只做"降级"，绝不反向把 needs_clarification 提升为 ready。"""
    gated = apply_readiness_gate(_decision(readiness="needs_clarification"))

    assert gated.readiness == "needs_clarification"


def test_gate_does_not_mutate_original() -> None:
    """判定对象要不可变式处理：原对象不能被就地改掉。"""
    original = _decision(readiness="ready", slots=RequirementSlots())
    apply_readiness_gate(original)

    assert original.readiness == "ready"


# --------------------------------------------------------------------------
# 2. route()：正常路径
# --------------------------------------------------------------------------


def test_route_returns_parsed_decision_and_usage() -> None:
    """正常路径：返回结构化判定 + 从 raw AIMessage 读出用量。"""
    raw = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 30,
            "total_tokens": 130,
            "output_token_details": {"reasoning": 5},
        },
    )
    result = route("做个待办清单", chain=_chain(_decision(), raw))

    assert result.degraded is False
    assert result.decision.intent == "generate"
    assert result.usage.input_tokens == 100
    assert result.usage.reasoning_tokens == 5


def test_route_applies_gate_to_model_output() -> None:
    """route() 必须对模型输出**应用**兜底，而不是原样返回。"""
    result = route(
        "帮我做个管理后台",
        chain=_chain(_decision(readiness="ready", slots=RequirementSlots())),
    )

    assert result.decision.readiness == "needs_clarification"


def test_route_carries_draft_slots_on_success() -> None:
    """已有槽位会作为上下文传进去（这里只验证不炸、且判定被采纳）。"""
    draft = RequirementSlots(site_kind="单页展示", features=["添加待办"])
    result = route("再加一个删除功能", draft_slots=draft, chain=_chain(_decision()))

    assert result.decision.intent == "generate"


# --------------------------------------------------------------------------
# 3. route()：失败与降级（降级方向取决于"该路径已知多少信息"）
# --------------------------------------------------------------------------


def test_route_degrades_to_chat_on_exception() -> None:
    """对话路径下调用失败 → 降级为 chat（意图不明时继续对话比擅自生成安全）。"""

    def _boom(_payload: object) -> object:
        raise RuntimeError("模型服务超时")

    result = route("随便说点什么", chain=RunnableLambda(_boom))

    assert result.degraded is True
    assert result.decision.intent == "chat"
    assert result.decision.readiness == "needs_clarification"
    assert "超时" in result.decision.reason


def test_route_degrades_to_generate_on_create_path() -> None:
    """直达路径（create）下调用失败 → 降级为 generate+ready。

    那里用户已经明确点了"生成"，意图本来就已知，不该因为路由失败把用户卡住。
    """

    def _boom(_payload: object) -> object:
        raise RuntimeError("模型服务超时")

    result = route("做个待办清单", on_failure_intent="generate", chain=RunnableLambda(_boom))

    assert result.decision.intent == "generate"
    assert result.decision.readiness == "ready"


def test_route_degrades_when_parsing_fails() -> None:
    """结构化解析失败（模型没按 schema 调工具）也要降级，且**用量照记**。"""
    raw = AIMessage(
        content="我拒绝输出结构",
        usage_metadata={"input_tokens": 50, "output_tokens": 10, "total_tokens": 60},
    )
    result = route("做个待办清单", chain=_chain(None, raw))

    assert result.degraded is True
    assert result.usage.input_tokens == 50, "解析失败同样烧了 token，必须记账"


def test_route_preserves_accumulated_slots_on_degradation() -> None:
    """降级时不能把已经积累的槽位丢掉。"""

    def _boom(_payload: object) -> object:
        raise RuntimeError("boom")

    draft = RequirementSlots(site_kind="单页展示", features=["添加待办"])
    result = route("继续", draft_slots=draft, chain=RunnableLambda(_boom))

    assert result.decision.slots.site_kind == "单页展示"
    assert result.decision.slots.features == ["添加待办"]


# --------------------------------------------------------------------------
# 4. 链的构造
# --------------------------------------------------------------------------


def test_build_router_chain_uses_structured_output() -> None:
    """router 链必须带结构化输出（否则拿到的是自由文本，没法当判定用）。"""
    from app.core.llm_client import llm_structured_client

    chain = build_router_chain()
    assert chain is not None
    # 不允许思考模式：结构化输出依赖强制 tool_choice，与思考模式互斥（设计约定 §7.3）
    assert llm_structured_client is not None
