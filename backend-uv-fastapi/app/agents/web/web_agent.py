# app/agents/web/web_agent.py —— 内层 ReAct 环：模型 ⇄ 工具，逐个把文件写进虚拟文件系统
#
# 这是整个改造里**唯一真正的 agent**（有循环、有工具调用），其余节点都是"结构化输出节点"
# （docs/agent_refactor_plan.md §3.2.1）。因此它的验收方式也不同：不看单次输出，而看**循环行为** ——
# 会不会自主调工具、会不会自己拆解、工具报错后会不会改正，以及门禁能不能拦住"只写 1 个文件就宣布完成"。
#
# 五条铁律（每条都对应一个真实踩过的坑）：
#
# 1. **每次生成新建 store + 新建图**。store 若是模块级全局变量，两个用户同时生成时
#    B 会读到 A 的文件、甚至覆盖 A 的产物 —— 这是本次改造里最隐蔽也最危险的 bug（§3.7）。
# 2. **完成判定权在 Python 侧**：门禁 = `store.missing(plan 清单)`。
#    所以刻意**不提供** finish / done 工具：模型说"我写完了"不算数。
# 3. **工具永不抛异常**（由 tools.py 保证）：失败原因作为字符串回给模型，这才是感知闭环。
# 4. **刹车必须真的接上难度**：步数与输出 token 都来自 `StageBudget`，
#    否则 difficulty 只是个装饰品（跑到一半被掐断，用户看到的是"生成失败"）。
# 5. **无进展检测**：连续若干轮 store 快照没变化（模型在反复读文件）就停，
#    免得"读→改→再读"把预算耗光（硬约束 10）。

import json
import logging
import time
from collections.abc import Callable, Sequence
from typing import Annotated, Any, TypedDict

from langchain.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from app.agents.common import ModelUsage, StageBudget, budget_for
from app.agents.state import FilePlan, FinalRequirement
from app.agents.web.tools import build_agent_tools
from app.core.llm_client import llm_client, llm_no_thinking_client
from app.utils.weg_gen.file_store import FileStore
from app.utils.weg_gen.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

PROMPT_NAME = "web_agent_system"

# 连续多少轮 store 没变化就判定"没有进展"
NO_PROGRESS_LIMIT = 3

# 门禁不通过时最多补缺几轮（第 1 轮 + 2 轮补缺）
MAX_REPAIR_ROUNDS = 2

# 单轮模型调用的重试次数（不含首次）。
# ⚠️ 为什么必须重试：一次生成要调模型 3~6 轮，每轮都把整段历史重发一遍（实测单次生成
# 输入 token 可达 10 万+）。**任何一次**网络抖动 / 5xx / 超时都会命中这里 ——
# 若把它当致命错误，就等于"跑了 100 秒、文件都写好一半，因为一次抖动全废"
# （2026-09-15 真实发生：第 2 步调用失败 → 门禁判缺件 → 用户看到"生成的文件不完整"）。
# 重试很便宜（只重发那一轮），且 LLM 调用无副作用，可以放心重试。
MODEL_CALL_RETRIES = 2

# 重试之间的退避基数（秒）：第 n 次重试前等 n * 该值
RETRY_BACKOFF_SECONDS = 1.5

# 默认客户端：**思考模式**（方案 §2.1：思考模式可自然调用工具，且循环把预算压力摊薄到多次调用）。
# 该默认值由自检数据确认，可将 `thinking=False` 切到非思考客户端。
DEFAULT_THINKING = True

# 会**阻止补缺轮**的刹车原因：预算类刹车再补一轮只会撞同一面墙。
# ⚠️ 刻意**不含 "error"**：模型调用失败是外部抖动，值得再给一轮机会。
_BUDGET_STOP_REASONS = frozenset({"max_steps", "token_budget", "no_progress"})

# 因"模型调用失败"而额外允许的补缺轮数。
# 给 1 轮而不是用满 max_repair_rounds：抖动值得再试一次，但**模型整体不可用**时
# 连开 2 轮补缺只会把 3 次重试 × 2 轮的等待时间白白烧掉（每次都要真实等待与计费）。
MAX_ERROR_REPAIR_ROUNDS = 1


class WebAgentResult(BaseModel):
    """一次生成（含补缺轮）的结果。

    Attributes:
        files: 虚拟文件系统的最终快照（**不落盘** —— 落盘由 service 统一做）。
        missing: 门禁判据：交付清单里还没写的文件。
        gate_passed: 门禁是否通过（= missing 为空）。
        extra_files: 模型额外交付的、清单之外的文件（保留但不计入交付）。
        steps_used: 实际使用的模型轮数（= 步数）。
        rounds: 实际跑了几轮（1 表示一次通过，>1 表示补缺过）。
        stop_reason: 循环为什么停下：done / max_steps / token_budget / no_progress / error。
        degraded: 是否因为刹车或异常而提前结束。
        usage: 累计 token 用量（含补缺轮）。
        warnings: 非致命问题（触到刹车、多写了文件、回调失败……）。
        trace_jsonl: 工具调用的逐轮记录（JSON Lines，落 `_debug_trace.jsonl`）。
    """

    model_config = {"arbitrary_types_allowed": True}

    files: dict[str, str] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    gate_passed: bool = False
    extra_files: list[str] = Field(default_factory=list)
    steps_used: int = 0
    rounds: int = 0
    stop_reason: str = "done"
    degraded: bool = False
    usage: ModelUsage = Field(default_factory=ModelUsage)
    warnings: list[str] = Field(default_factory=list)
    trace_jsonl: str = ""


class _LoopState(TypedDict):
    """图的状态：只有消息列表会跨节点累积（预算与计数放在闭包里，见 generate）。"""

    messages: Annotated[list[AnyMessage], add_messages]


def build_web_agent_model(thinking: bool = DEFAULT_THINKING) -> Runnable:
    """取 web-agent 用的基础客户端（**未绑定工具**，绑定在 generate 里按请求做）。

    Args:
        thinking: True 用思考模式客户端，False 用非思考客户端。

    Returns:
        基础聊天模型（可 `bind_tools`）。
    """
    return llm_client if thinking else llm_no_thinking_client


def generate(
    plan: FilePlan,
    requirement: FinalRequirement | None = None,
    *,
    model: Runnable | None = None,
    tool_wrapper: Callable[[FileStore, list[Any]], list[Any]] | None = None,
    thinking: bool = DEFAULT_THINKING,
    max_steps: int | None = None,
    max_output_tokens: int | None = None,
    max_repair_rounds: int = MAX_REPAIR_ROUNDS,
    retry_backoff_seconds: float = RETRY_BACKOFF_SECONDS,
    on_step: Callable[[int, str], None] | None = None,
) -> WebAgentResult:
    """按交付清单生成文件（模型自主调用工具），并在门禁不通过时定向补缺。

    Args:
        plan: 交付清单（阶段 5 产出；门禁只认它声明的文件）。
        requirement: 最终需求（渲染进提示词，供模型遵循功能与风格）。
        model: 可注入的**已绑定工具**的模型（测试用）。为 None 时按 `thinking` 取全局客户端并绑定工具。
        tool_wrapper: 工具集包装器 ``(store, tools) -> tools``，用于**故障注入**。
            自检要验证"工具报错后模型能否改正"，就得有一个"第一次调用必失败"的 write_file ——
            靠等模型自己犯错是等不到的。
            ⚠️ 之所以做成包装器而不是直接注入 tools：工具必须绑定**本次请求的 store**
            （铁律 1），外部自己 `build_agent_tools(另一个 store)` 传进来会让产物写进那个游魂 store，
            本轮生成的 store 始终是空的 —— 门禁于是永远不过，而现象看起来像"模型不会写文件"。
        thinking: 默认客户端选择（仅当 `model` 为 None 时生效）。
        max_steps: 模型轮数上限；默认取 `plan.difficulty` 的预算。
        max_output_tokens: 输出 token 预算；默认取 `plan.difficulty` 的预算。
        max_repair_rounds: 门禁不通过时最多补缺几轮。
        retry_backoff_seconds: 模型调用失败后重试的退避基数（测试传 0 以免真等）。
        on_step: 每步回调（用于推进阶段 / 打日志）；回调异常不影响生成。

    Returns:
        `WebAgentResult`。
    """
    budget: StageBudget = budget_for(plan.difficulty)
    step_limit = max_steps if max_steps is not None else budget.max_steps
    token_limit = max_output_tokens if max_output_tokens is not None else budget.max_output_tokens

    # ⚠️ store / trace 都必须 **per-request** 构造（铁律 1）
    store = FileStore()
    base_tools = build_agent_tools(store)
    active_tools = tool_wrapper(store, base_tools) if tool_wrapper is not None else base_tools
    trace = _TraceCollector()
    active_model = model or build_web_agent_model(thinking).bind_tools(active_tools)

    counters: dict[str, Any] = {
        "steps": 0,
        "usage": ModelUsage(),
        "no_progress": 0,
        # None = "还没跑过第一轮"：首轮不去比较快照，否则空工作区会被当成"没有进展"
        "snapshot": None,
    }
    warnings: list[str] = []
    stop_reason = "done"
    degraded = False

    def _snapshot() -> str:
        """当前工作区的指纹（文件名 + 内容），用于无进展检测。"""
        return "|".join(f"{name}:{len(store.get(name) or '')}" for name in store.names())

    def _agent_node(state: _LoopState) -> dict[str, list[AnyMessage]]:
        """模型节点：刹车检查 → 调用模型 → 记账。"""
        nonlocal stop_reason, degraded
        current = _snapshot()
        if counters["snapshot"] is not None and current == counters["snapshot"]:
            counters["no_progress"] += 1
        else:
            counters["no_progress"] = 0
        counters["snapshot"] = current

        if counters["steps"] >= step_limit:
            stop_reason = "max_steps"
            degraded = True
            return {"messages": [AIMessage(content="[预算] 步数已达上限，收工。", id="budget-stop")]}
        if counters["usage"].output_tokens >= token_limit:
            stop_reason = "token_budget"
            degraded = True
            return {"messages": [AIMessage(content="[预算] 输出 token 已达上限，收工。", id="budget-stop")]}
        if counters["no_progress"] >= NO_PROGRESS_LIMIT:
            stop_reason = "no_progress"
            degraded = True
            return {"messages": [AIMessage(content="[预算] 连续多轮没有进展，收工。", id="budget-stop")]}

        counters["steps"] += 1
        response = None
        for attempt in range(MODEL_CALL_RETRIES + 1):
            try:
                response = active_model.invoke(state["messages"])
                break
            except Exception as error:  # noqa: BLE001 —— 单轮调用失败不该毁掉已有产物
                if attempt >= MODEL_CALL_RETRIES:
                    stop_reason = "error"
                    degraded = True
                    warnings.append(
                        f"第 {counters['steps']} 步模型调用失败（已重试 {MODEL_CALL_RETRIES} 次）："
                        f"{type(error).__name__}: {error}"
                    )
                    return {"messages": [AIMessage(content=f"[错误] {error}", id="model-error")]}
                # 退避后重试：抖动是常态，把整次生成废掉才是真损失
                warnings.append(
                    f"第 {counters['steps']} 步模型调用失败，准备第 {attempt + 1} 次重试："
                    f"{type(error).__name__}: {error}"
                )
                if retry_backoff_seconds > 0:
                    time.sleep(retry_backoff_seconds * (attempt + 1))

        if response is None:  # pragma: no cover —— 上面的循环必然要么 return 要么赋值
            stop_reason = "error"
            degraded = True
            return {"messages": [AIMessage(content="[错误] 模型未返回结果", id="model-error")]}

        counters["usage"] = counters["usage"] + ModelUsage.from_message(response)
        detail = _describe_turn(response, counters["steps"])
        _notify(on_step, counters["steps"], detail, warnings)
        trace.add(counters["steps"], detail, response)
        return {"messages": [response]}

    def _should_continue(state: _LoopState) -> str:
        """条件边：还有工具调用就继续，否则收工。"""
        last = state["messages"][-1] if state["messages"] else None
        if stop_reason != "done":
            return END
        if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
            return "tools"
        return END

    builder = StateGraph(_LoopState)
    builder.add_node("agent", _agent_node)
    builder.add_node("tools", ToolNode(active_tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    graph = builder.compile()

    messages: list[AnyMessage] = _build_messages(plan, requirement)
    rounds = 0
    error_repairs = 0
    # 补缺轮：门禁不过就把"还缺哪些文件"作为指令再进一次循环（同一 store、同一预算）
    for round_index in range(max_repair_rounds + 1):
        rounds = round_index + 1
        # recursion_limit 是兜底：即使计数逻辑失效，也不会无限循环
        graph.invoke({"messages": messages}, config={"recursion_limit": 2 * (step_limit + 2) + 2})

        missing = store.missing(plan.names())
        if not missing:
            break
        if round_index >= max_repair_rounds or stop_reason in _BUDGET_STOP_REASONS:
            break
        if stop_reason == "error":
            error_repairs += 1
            if error_repairs > MAX_ERROR_REPAIR_ROUNDS:
                break
        if counters["steps"] >= step_limit or counters["usage"].output_tokens >= token_limit:
            break
        warnings.append(f"第 {rounds} 轮未交付完整，开始补缺：缺 {missing}")
        messages = _build_messages(plan, requirement, repair_missing=missing)
        counters["no_progress"] = 0
        # ⚠️ 必须把刹车原因清回 done：`_should_continue` 一看到 stop_reason != "done" 就直接 END，
        # 上一轮因模型调用失败留下的 "error" 会让新的一轮在第一个节点就退出（补缺等于没补）
        stop_reason = "done"

    missing = store.missing(plan.names())
    extra = [name for name in store.names() if name not in set(plan.names())]
    if extra:
        warnings.append("模型额外交付了清单外的文件（已保留，但不计入交付）：" + "、".join(extra))

    return WebAgentResult(
        files=store.snapshot(),
        missing=missing,
        gate_passed=not missing,
        extra_files=extra,
        steps_used=counters["steps"],
        rounds=rounds,
        stop_reason=stop_reason,
        degraded=degraded,
        usage=counters["usage"],
        warnings=warnings,
        trace_jsonl=trace.to_jsonl(),
    )


class _TraceCollector:
    """极简的逐轮记录器：只记"第几步做了什么、调了哪些工具"，**不记文件正文**。

    为什么单独一个类而不是复用 AgentTrace：`AgentTrace` 记的是**工具调用**（由 tools.py 填充），
    这里要记的是**模型轮次**（含没调工具的那一轮）。两者互补，合起来才看得出循环行为。
    """

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    def add(self, step: int, detail: str, response: AIMessage) -> None:
        """记录一轮。"""
        self._items.append(
            {
                "step": step,
                "detail": detail,
                "tool_calls": [
                    {"name": call.get("name"), "args": _brief_args(call.get("args"))}
                    for call in (getattr(response, "tool_calls", None) or [])
                ],
                "content_chars": len(str(response.content or "")),
            }
        )

    def to_jsonl(self) -> str:
        """渲染成 JSON Lines（一行一轮，可直接 tail）。"""
        return "\n".join(json.dumps(item, ensure_ascii=False) for item in self._items)


def _brief_args(args: Any) -> Any:
    """压缩工具参数：只留文件名，丢掉正文（否则几万字的 HTML 会把 trace 撑爆）。"""
    if not isinstance(args, dict):
        return args
    files = args.get("files")
    if isinstance(files, dict):
        return {"files": list(files)}
    return {key: value for key, value in args.items() if key != "content"}


def _describe_turn(response: AIMessage, step: int) -> str:
    """把一轮模型输出压成一句人话（进 trace 与阶段明细）。"""
    calls = getattr(response, "tool_calls", None) or []
    if not calls:
        return f"第 {step} 步：模型未调用工具（认为已收工）"
    parts: list[str] = []
    for call in calls:
        args = _brief_args(call.get("args"))
        if isinstance(args, dict) and "files" in args:
            parts.append(f"{call.get('name')} → {'、'.join(args['files'])}")
        else:
            parts.append(str(call.get("name")))
    return f"第 {step} 步：" + "；".join(parts)


def _notify(
    on_step: Callable[[int, str], None] | None, step: int, detail: str, warnings: list[str]
) -> None:
    """安全地调用外部回调（阶段推进用）。

    ⚠️ 回调里通常是**写数据库**（推进阶段）。它失败不该让生成整体失败，
    但也不能静默吞掉 —— 记进 warnings，调用方能看到。
    """
    if on_step is None:
        return
    try:
        on_step(step, detail)
    except Exception as error:  # noqa: BLE001
        warnings.append(f"步数回调失败（不影响生成）：{type(error).__name__}: {error}")


def _build_messages(
    plan: FilePlan,
    requirement: FinalRequirement | None,
    repair_missing: Sequence[str] | None = None,
) -> list[AnyMessage]:
    """装配循环的初始消息：系统提示词（角色 + 工具语义）+ 用户消息（清单 + 需求）。"""
    parts: list[str] = ["## 交付清单（缺一不可）\n" + plan.as_prompt_text()]
    if requirement is not None:
        parts.append("## 最终需求\n" + requirement.as_prompt_text())
    if repair_missing:
        parts.append(
            "## ⚠️ 补缺指令\n上一轮还缺以下文件，请**补齐它们**（其余文件已写好，不必重写）：\n"
            + "\n".join(f"- {name}" for name in repair_missing)
        )
    parts.append("## 开始\n请调用 `write_file` 交付上述文件；全部写完就停止调用工具。")
    return [
        SystemMessage(content=load_prompt(PROMPT_NAME)),
        HumanMessage(content="\n\n".join(parts)),
    ]
