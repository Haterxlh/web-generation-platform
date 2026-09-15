# app/agents/orchestrator.py —— 外层需求装配图：把各节点串成一条真实可跑的链路
#
# 职责（docs/agent_refactor_plan.md §3.2 / §3.8）：按固定顺序驱动各节点，并把每一步
# **上报给调用方**（service 用它推进数据库里的阶段，前端据此显示进度）。
#
# 本模块是**纯函数**：不碰数据库、不落盘、不读文件 —— 与 agents 层的既有约定一致
# （设计约定 §3.2：给它需求、还它 {文件名: 内容}）。落库与落盘由 service 统一做。
#
# 两段式嵌套：外层是这张"需求装配图"（固定顺序、结构化产物），内层才是真正的 agent
# （web-agent 的 ReAct 环）。刻意**不做多智能体自由对话** —— 贵且不可控。
#
# ⚠️ 三个刻意的取舍：
#
# 1. **MERGE 不单独上报阶段**：`AgentStage` 没有 MERGING，加它要同步改前端类型；
#    归并很快，这里并入 PLANNING 的 stage_detail（"正在归并需求与规划结构"）。
# 2. **循环中途不向用户提问**（2026-09-15 决策）：信息不足只在 ROUTING 处判 ——
#    要么停在 `clarifying` 等用户补充，要么一路跑到门禁。跑到一半才发现缺信息，
#    说明 ROUTING 没做好，应当回头加强它，而不是在循环里开逃生口。
# 3. **附件 digest 在上传时就已算好**（阶段 3）：这里只消费结果，不重复解析，
#    否则同一个文件会被反复理解、反复计费。

import logging
from collections.abc import Callable, Sequence

from pydantic import BaseModel, Field

from app.agents.common import ModelUsage
from app.agents.merge.requirement_merge import MergeResult, merge
from app.agents.plan.plan_agent import PlanResult, plan as run_plan
from app.agents.rag import need_rag
from app.agents.rag.retriever import RetrieverProvider, retrieve
from app.agents.router import intent_router
from app.agents.stages import AgentStage
from app.agents.state import FinalRequirement, RagResult, RequirementDigest, RequirementSlots
from app.agents.web import web_agent
from app.agents.web.web_agent import WebAgentResult

logger = logging.getLogger(__name__)


class OrchestratorResult(BaseModel):
    """一次完整流水线的产物（不含落盘/落库）。

    Attributes:
        status: ``success`` / ``clarifying`` / ``failed``。
        files: 产物文件（success 时才有）。
        usage: 全链路累计 token（router + merge + plan + web-agent）。
        requirement: 归并后的最终需求（供 service 写 ``generation_plan``）。
        plan_result: 规划结果（含预算与模型原始难度）。
        web_result: 内层循环的结果（含门禁、步数、trace）。
        rag: 检索结果（三态，供排查"要不要查私人资料"）。
        stages: 上报过的阶段序列（(stage, detail)，便于排查与测试）。
        warnings: 各节点带出的非致命问题（不静默降级）。
        error_message: ``failed`` 时的原因（面向用户）。
        ask_hint: ``clarifying`` 时给用户看的追问方向。
    """

    model_config = {"arbitrary_types_allowed": True}

    status: str = "failed"
    files: dict[str, str] = Field(default_factory=dict)
    usage: ModelUsage = Field(default_factory=ModelUsage)
    requirement: FinalRequirement | None = None
    plan_result: PlanResult | None = None
    web_result: WebAgentResult | None = None
    rag: RagResult | None = None
    stages: list[tuple[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_message: str = ""
    ask_hint: str = ""


def run(
    user_message: str,
    *,
    user_id: int,
    slots: RequirementSlots | None = None,
    draft_summary: str = "",
    digests: Sequence[tuple[str, RequirementDigest]] = (),
    attach_warnings: Sequence[str] = (),
    on_stage: Callable[[AgentStage, str], None] | None = None,
    on_plan: Callable[[PlanResult, FinalRequirement], None] | None = None,
    retriever: RetrieverProvider | None = None,
    web_model: object | None = None,
    thinking: bool = web_agent.DEFAULT_THINKING,
    router_chain: object | None = None,
    need_rag_chain: object | None = None,
    merge_chain: object | None = None,
    plan_chain: object | None = None,
) -> OrchestratorResult:
    """跑完一次生成链路（需求装配 → 规划 → 生成 → 门禁）。

    Args:
        user_message: 用户的需求原话（直达路径就是用户提交的那句话）。
        user_id: 用户 id（检索私人知识库时必须带上，硬约束 4）。
        slots: 会话里已累积的槽位（直达路径可为空）。
        draft_summary: 会话草稿摘要。
        digests: ``(来源标识, digest)``：上传时已算好的附件理解结果。
        attach_warnings: 附件解析阶段的警告（原样带到结果里）。
        on_stage: 阶段回调（service 用它推进数据库阶段；回调异常不影响生成）。
        on_plan: 规划完成后的回调（service 用它写 ``generation_plan`` 的**预估侧**）。
            签名是 ``(PlanResult, FinalRequirement)``：写库需要"计划"与"最终需求"两样东西，
            而它们是在不同节点产出的 —— 只给计划的话，写进去的需求字段就只能编一个。
        retriever: 可注入的检索 provider（测试用）。
        web_model: 可注入的、**已绑定工具**的内层模型（测试用）。
        thinking: 内层循环默认用思考模式还是非思考模式。
        router_chain / need_rag_chain / merge_chain / plan_chain: 可注入的节点链（测试用）。
            ⚠️ 四个链都必须可注入：只要漏一个，离线测试就会**真的去调模型**
            （既慢、又花钱、还不确定 —— 本项目所有节点级测试都依赖这个注入点）。

    Returns:
        `OrchestratorResult`。
    """
    stages: list[tuple[str, str]] = []
    warnings: list[str] = list(attach_warnings)
    usage = ModelUsage()

    def emit(stage: AgentStage, detail: str = "") -> None:
        stages.append((str(stage), detail))
        if on_stage is None:
            return
        try:
            on_stage(stage, detail)
        except Exception as error:  # noqa: BLE001 —— 阶段回调失败不该中断生成
            warnings.append(f"阶段回调失败（不影响生成）：{type(error).__name__}: {error}")

    # ---------- ⑧ ROUTING：完备度复核 ----------
    emit(AgentStage.ROUTING, "正在复核需求完备度")
    router_result = intent_router.route(
        user_message,
        draft_slots=slots,
        attachments=(),
        # 直达路径（用户已点"生成"）：意图本来就已知，路由失败时降级为 generate+ready
        on_failure_intent="generate",
        chain=router_chain,  # type: ignore[arg-type]
    )
    usage = usage + router_result.usage
    decision = router_result.decision
    if router_result.degraded:
        warnings.append(f"意图路由降级：{decision.reason}")

    effective_slots = (slots or RequirementSlots()).merged_with(decision.slots)
    if decision.readiness != "ready":
        # human-in-the-loop 暂停：任务停在 clarifying（**不是** failed）
        emit(AgentStage.CLARIFYING, decision.ask_hint or "信息不足，等待用户补充")
        return OrchestratorResult(
            status="clarifying",
            usage=usage,
            stages=stages,
            warnings=warnings,
            ask_hint=decision.ask_hint or "请再补充一些需求细节",
            error_message="",
        )

    # ---------- ⑨ DIGESTING：附件理解结果（上传时已算好） ----------
    if digests:
        emit(AgentStage.DIGESTING, f"已理解 {len(digests)} 份附件")

    # ---------- ⑩ RETRIEVING：判定 + 检索（一期为桩） ----------
    emit(AgentStage.RETRIEVING, "正在判断是否需要你的私人资料")
    need_result = need_rag.decide(
        user_message=user_message,
        slots=effective_slots,
        draft_summary=draft_summary,
        digests=digests,
        chain=need_rag_chain,  # type: ignore[arg-type]
    )
    usage = usage + need_result.usage
    warnings.extend(need_result.warnings)
    rag_result = retrieve(
        user_id,
        needs_retrieval=need_result.decision.need,
        query=need_result.decision.query,
        provider=retriever,
    )
    warnings.extend(rag_result.warnings)

    # ---------- ⑪ MERGE + ⑫ PLANNING（合并上报为一个阶段，见模块头注释） ----------
    emit(AgentStage.PLANNING, "正在归并需求并规划文件结构")
    merge_result: MergeResult = merge(
        user_message=user_message,
        slots=effective_slots,
        draft_summary=draft_summary,
        digests=digests,
        rag=rag_result,
        chain=merge_chain,  # type: ignore[arg-type]
    )
    usage = usage + merge_result.usage
    warnings.extend(merge_result.warnings)
    requirement = merge_result.requirement

    plan_result = run_plan(requirement, chain=plan_chain)  # type: ignore[arg-type]
    usage = usage + plan_result.usage
    warnings.extend(plan_result.warnings)

    if on_plan is not None:
        try:
            on_plan(plan_result, requirement)
        except Exception as error:  # noqa: BLE001 —— 记录规划失败不该中断生成
            warnings.append(f"规划入库回调失败（不影响生成）：{type(error).__name__}: {error}")

    # ---------- ⑬ GENERATING + ⑭ GATE ----------
    emit(
        AgentStage.GENERATING,
        f"开始生成（难度 {plan_result.plan.difficulty}，最多 {plan_result.budget.max_steps} 步）",
    )
    web_result = web_agent.generate(
        plan_result.plan,
        requirement,
        model=web_model,  # type: ignore[arg-type]
        thinking=thinking,
        max_steps=plan_result.budget.max_steps,
        max_output_tokens=plan_result.budget.max_output_tokens,
        on_step=lambda step, detail: emit(AgentStage.GENERATING, detail),
    )
    usage = usage + web_result.usage
    warnings.extend(web_result.warnings)

    if not web_result.gate_passed:
        # 门禁不过 = 交付不完整：**必须失败**，不能把半成品当成功（§3.8.6）
        missing_text = "、".join(web_result.missing)
        emit(AgentStage.FAILED, f"交付不完整，缺少：{missing_text}")
        return OrchestratorResult(
            status="failed",
            usage=usage,
            requirement=requirement,
            plan_result=plan_result,
            web_result=web_result,
            rag=rag_result,
            stages=stages,
            warnings=warnings,
            error_message=(
                f"生成的文件不完整（缺少：{missing_text}）；"
                f"已尝试 {web_result.rounds} 轮、共 {web_result.steps_used} 步"
            ),
        )

    emit(AgentStage.DONE, "已交付全部文件")
    return OrchestratorResult(
        status="success",
        files=web_result.files,
        usage=usage,
        requirement=requirement,
        plan_result=plan_result,
        web_result=web_result,
        rag=rag_result,
        stages=stages,
        warnings=warnings,
    )


__all__ = ["OrchestratorResult", "run"]
