# app/agents/router/intent_router.py —— 意图路由节点
#
# 职责（docs/agent_refactor_plan.md §3.2.1）：每轮判断 intent + readiness + slots。
#
# 两条关键设计：
#
# 1. **完全交给 AI 判定**，不做"关键词命中就跳过大模型"的规则前置（2026-09-15 决策）。
#    规则判据难维护，且遇到反讽 / 隐含意图必然判错；统一走结构化输出后，
#    "判定口径"就只有一处。
#
# 2. **判定权归模型，否决权归 Python**：模型说 ready、但必备槽位为空时，
#    由 `apply_readiness_gate()` 强制降级为 needs_clarification。
#    模型很可能"热情地"认为信息够了然后放行，生成一个跑偏的网页 ——
#    而"site_kind 与 features 都空就绝不放行"是一条不需要判断力的硬规则。

import logging
from collections.abc import Sequence
from typing import Literal

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable

from app.agents.common import ModelUsage
from app.agents.state import RequirementSlots, RouterDecision, RouterResult
from app.core.llm_client import llm_structured_client
from app.utils.agent.alias import AliasTarget, render_target
from app.utils.weg_gen.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

PROMPT_NAME = "intent_router_system"


def build_router_chain(model: Runnable | None = None) -> Runnable:
    """构造 router 链：结构化输出 + 保留原始 AIMessage（token 用量在它身上）。

    Args:
        model: 可注入的模型（测试用；默认用全局 `llm_structured_client`）。
            结构化输出依赖强制 tool_choice，与思考模式互斥（设计约定 §7.3），
            所以这里必须是**非思考**客户端。

    Returns:
        输入 `list[BaseMessage]` → 输出 `{"raw": AIMessage, "parsed": RouterDecision|None}`。
    """
    return (model or llm_structured_client).with_structured_output(
        RouterDecision, include_raw=True
    )


# 模块级只建一次（链本身无状态，可反复 invoke）
_ROUTER = build_router_chain()


def apply_readiness_gate(decision: RouterDecision) -> RouterDecision:
    """Python 侧硬兜底：模型说 ready，但必备槽位为空 → 强制降级为需澄清。

    这是"节点可以交给模型、否决权必须在 Python 侧"这条原则的第一个落点
    （阶段 0 的完成门禁是同一个思路的另一处应用）。

    Args:
        decision: 模型给出的判定。

    Returns:
        修正后的判定（不修改原对象）。
    """
    if decision.intent != "generate":
        return decision

    missing_required = decision.slots.missing_required()
    if decision.readiness == "ready" and missing_required:
        merged_missing = sorted({*decision.missing_slots, *missing_required})
        return decision.model_copy(
            update={
                "readiness": "needs_clarification",
                "missing_slots": merged_missing,
                "reason": (
                    f"{decision.reason}"
                    f"｜Python 兜底：必备槽位 {missing_required} 为空，强制转为需澄清"
                ),
            }
        )
    return decision


def _degraded_decision(
    slots: RequirementSlots | None,
    intent: Literal["chat", "generate"],
    reason: str,
) -> RouterDecision:
    """调用失败时的降级判定。

    ⚠️ 降级方向**取决于该路径已知多少信息**：

    - **对话路径（默认 chat）**：用户意图不明时，继续对话永远比擅自生成安全；
    - **直达路径**（`create`，用户已经明确点了"生成"）：那里意图本来就已知，
      所以降级为 `generate + ready`、直接往下走才合理，不必卡住用户。

    Args:
        slots: 当前已累积的槽位（降级时原样带下去，不丢信息）。
        intent: 降级后的意图。
        reason: 降级原因。

    Returns:
        降级后的判定。
    """
    carried = slots or RequirementSlots()
    return RouterDecision(
        intent=intent,
        readiness="ready" if intent == "generate" else "needs_clarification",
        slots=carried,
        missing_slots=carried.missing(),
        ask_hint="",
        reason=reason,
    )


def _build_router_message(
    message: str,
    history: Sequence[tuple[str, str]],
    draft_slots: RequirementSlots | None,
    attachments: Sequence[AliasTarget],
) -> str:
    """拼出这次判定要用的用户消息。

    Args:
        message: 用户最新一句话。
        history: 最近对话，元素为 (role, content)。
        draft_slots: 当前已抽取的槽位。
        attachments: 本轮附件。

    Returns:
        用户消息文本。
    """
    parts: list[str] = []

    if history:
        lines = [f"{role}：{content}" for role, content in history]
        parts.append("## 最近的对话\n" + "\n".join(lines))

    if attachments:
        parts.append(
            "## 本轮附件\n" + "\n".join(render_target(item) for item in attachments)
        )

    if draft_slots is None:
        parts.append("## 当前已抽取的槽位\n（这是第一轮，还没有任何槽位）")
    else:
        parts.append("## 当前已抽取的槽位\n" + draft_slots.model_dump_json())

    # 最新一句话放最后：离输出最近的指令权重最高
    parts.append("## 用户最新一句话\n" + message)
    return "\n\n".join(parts)


def route(
    message: str,
    *,
    history: Sequence[tuple[str, str]] = (),
    draft_slots: RequirementSlots | None = None,
    attachments: Sequence[AliasTarget] = (),
    on_failure_intent: Literal["chat", "generate"] = "chat",
    chain: Runnable | None = None,
) -> RouterResult:
    """对一轮对话做意图 / 完备度 / 槽位判定。

    Args:
        message: 用户最新一句话。
        history: 最近对话（(role, content) 列表；由 service 按 token 预算裁好）。
        draft_slots: 当前已抽取的槽位（跨轮累积的旧值）。
        attachments: 本轮附件。
        on_failure_intent: 调用失败时降级成什么意图（见 `_degraded_decision`）。
        chain: 可注入的链（测试用）。

    Returns:
        `RouterResult`（判定 + 是否降级 + token 用量）。
    """
    router_chain = chain or _ROUTER
    payload = [
        SystemMessage(content=load_prompt(PROMPT_NAME)),
        HumanMessage(
            content=_build_router_message(message, history, draft_slots, attachments)
        ),
    ]

    try:
        result = router_chain.invoke(payload)
    except Exception as error:  # noqa: BLE001 —— 路由失败不能拖垮整轮对话
        logger.warning("intent_router 调用失败，走降级路径：%s", error)
        # 降级原因里带上异常信息（截断）：reason 只进日志与排查，不返回给前端，
        # 所以宁可写详细一点 —— 只有类型名的话，"超时"和"鉴权失败"看起来一模一样
        detail = f"{type(error).__name__}: {error}"[:200]
        return RouterResult(
            decision=_degraded_decision(
                draft_slots, on_failure_intent, f"router 调用失败：{detail}"
            ),
            degraded=True,
        )

    raw = result.get("raw")
    usage = ModelUsage.from_message(raw) if raw is not None else ModelUsage()

    decision = result.get("parsed")
    if decision is None:
        # 结构化解析失败：模型没按 schema 调工具（用量照记，失败也烧了 token）
        return RouterResult(
            decision=_degraded_decision(
                draft_slots, on_failure_intent, "router 结构化解析失败"
            ),
            degraded=True,
            usage=usage,
        )

    return RouterResult(decision=apply_readiness_gate(decision), degraded=False, usage=usage)
