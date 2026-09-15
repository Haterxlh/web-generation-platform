"""阶段 3 的离线测试（六）：需求归并节点（requirement_merge）。

这一节点是"冲突消解"的唯一落点，所以用例集中在四件**Python 必须在场**的事上：

- **来源证据（sources）由 Python 生成**：漏一条 = 某条需求没有出处，事后无法追责；
- **风格结构化取值由 Python 汇总**：模型转述的色值只会失真（"看起来像 #1e293b"）；
- **`use_document_style=false` 时必须真的不注入令牌**：否则用户"不要文档配色"的话被静默忽略；
- **槽位只增不减**：模型可以在文档里发现新信息，但不能把用户说过的清空。
"""

import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agents.common import ModelUsage
from app.agents.merge.requirement_merge import (
    MergeDecision,
    build_merge_chain,
    merge,
)
from app.agents.state import (
    FinalRequirement,
    RagChunk,
    RagResult,
    RequirementDigest,
    RequirementSlots,
    StyleSpec,
)
from app.utils.weg_gen.prompt_loader import load_prompt


# --------------------------------------------------------------------------
# 测试替身
# --------------------------------------------------------------------------


def _raw(input_tokens: int = 50, output_tokens: int = 20) -> AIMessage:
    """带用量元数据的 AIMessage。"""
    return AIMessage(
        content="",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    )


def _chain(parsed: object, raw: AIMessage | None = None) -> RunnableLambda:
    """假链：返回 with_structured_output(include_raw=True) 的产物形态。"""
    return RunnableLambda(lambda _payload: {"raw": raw, "parsed": parsed})


def _boom_chain(message: str = "模型服务超时") -> RunnableLambda:
    """调用即抛异常的假链。"""

    def _boom(_payload: object) -> object:
        raise RuntimeError(message)

    return RunnableLambda(_boom)


def _digest(
    *,
    role: str = "content",
    tokens: dict | None = None,
    content_points: list[str] | None = None,
    constraints: list[str] | None = None,
    open_questions: list[str] | None = None,
    notes: str = "",
) -> RequirementDigest:
    """造一份文档理解结果。"""
    return RequirementDigest(
        summary="一份文档",
        role=role,  # type: ignore[arg-type]
        content_points=content_points or [],
        constraints=constraints or [],
        open_questions=open_questions or [],
        style_spec=StyleSpec.from_design_tokens(tokens, notes=notes),
    )


def _rag_hit(*, text: str = "公司主色 #123456", query: str = "品牌色") -> RagResult:
    """造一个命中结果。"""
    return RagResult(
        status="hit",
        query=query,
        chunks=[
            RagChunk(
                chunk_id="c1",
                source_type="conversation",
                source_ref="第 2 轮",
                text=text,
                score=0.91,
            )
        ],
    )


def _decision(**kwargs: object) -> MergeDecision:
    """造一个模型判定，未指定字段用合理默认值。"""
    payload: dict = {
        "summary": "做一个极简白底的待办清单",
        "slots": RequirementSlots(site_kind="单页展示", features=["添加待办"]),
        "content_points": [],
        "style_spec": StyleSpec(notes="极简白底"),
        "constraints": [],
        "uncertainty": [],
        "use_document_style": True,
    }
    payload.update(kwargs)
    return MergeDecision(**payload)


# --------------------------------------------------------------------------
# 1. 来源证据（Python 生成的事实）
# --------------------------------------------------------------------------


def test_sources_record_all_four_kinds() -> None:
    """四个来源都要留下证据，顺序按优先级（user → chat → doc → rag）。"""
    result = merge(
        user_message="做个待办清单，参考 @doc1",
        slots=RequirementSlots(site_kind="单页展示"),
        draft_summary="用户想要一个待办工具",
        digests=[("@doc1", _digest(content_points=["标题：我的待办"]))],
        rag=_rag_hit(text="公司主色 #123456"),
        chain=_chain(_decision()),
    )

    assert [item.kind for item in result.requirement.sources] == ["user", "chat", "doc", "rag"]
    assert result.requirement.sources[0].ref == "做个待办清单，参考 @doc1"
    assert result.requirement.sources[2].ref == "@doc1"
    assert "检索命中 1 段" in result.requirement.sources[3].note


def test_sources_skip_empty_inputs() -> None:
    """没有对话、没有文档、没有检索时，证据清单里也不该出现空条目。"""
    result = merge(user_message="做个计算器", chain=_chain(_decision()))

    assert [item.kind for item in result.requirement.sources] == ["user"]


def test_long_user_message_is_clipped_in_evidence() -> None:
    """用户原话可能几百字，证据里只留摘要片段（够追溯即可）。"""
    result = merge(user_message="需求：" + "很长的描述" * 40, chain=_chain(_decision()))

    ref = result.requirement.sources[0].ref
    assert len(ref) <= 61, "证据文本要截断，否则会把 generation_plan 撑大"
    assert ref.endswith("…")


def test_rag_source_only_when_hit() -> None:
    """只有 hit 才留下"个人知识库"证据：miss 是**什么都没拿到**，写成出处就是捏造。"""
    without = merge(user_message="做个页面", chain=_chain(_decision()))
    missed = merge(
        user_message="做个页面",
        rag=RagResult(status="miss", query="品牌色", reason="没有相关资料"),
        chain=_chain(_decision()),
    )
    hit = merge(
        user_message="做个页面", rag=_rag_hit(text="主色 #123456"), chain=_chain(_decision())
    )

    assert "rag" not in [item.kind for item in without.requirement.sources]
    assert "rag" not in [item.kind for item in missed.requirement.sources]
    assert "rag" in [item.kind for item in hit.requirement.sources]


# --------------------------------------------------------------------------
# 2. 风格：事实由 Python 汇总，意图由模型写
# --------------------------------------------------------------------------


def test_style_tokens_are_aggregated_from_digests() -> None:
    """多份文档的令牌要合并：配色去重保序、布局计数相加。"""
    first = _digest(tokens={"colors": ["#0f172a", "#4f46e5"], "font_families": ["Inter"]})
    second = _digest(
        tokens={"colors": ["#4f46e5", "#e2e8f0"], "layout": {"display:grid": 1, "@media": 1}}
    )

    result = merge(
        user_message="参考这两份规范",
        digests=[("@doc1", first), ("@doc2", second)],
        chain=_chain(_decision(style_spec=StyleSpec(notes="深色底 + 大圆角"))),
    )

    spec = result.requirement.style_spec
    assert spec.colors == ["#0f172a", "#4f46e5", "#e2e8f0"], "去重且保序"
    assert spec.font_families == ["Inter"]
    assert spec.layout == {"display:grid": 1, "@media": 1}
    assert spec.notes == "深色底 + 大圆角"


def test_model_supplied_color_values_are_discarded() -> None:
    """⚠️ 模型自己填的色值必须丢弃 —— 风格复刻里"接近但不对"等于错。"""
    result = merge(
        user_message="用 @doc1 的配色",
        digests=[("@doc1", _digest(role="style", tokens={"colors": ["#0f172a"]}))],
        chain=_chain(
            _decision(style_spec=StyleSpec(colors=["#1e293b"], notes="深色科技感"))
        ),
    )

    assert result.requirement.style_spec.colors == ["#0f172a"]
    assert result.requirement.style_spec.notes == "深色科技感"


def test_use_document_style_false_really_drops_tokens() -> None:
    """⚠️ 用户说"不要文档风格"时，令牌**必须真的不注入**，并留下警告。"""
    result = merge(
        user_message="不要用文档的配色，我要极简白底",
        digests=[("@doc1", _digest(role="style", tokens={"colors": ["#0f172a"]}))],
        chain=_chain(
            _decision(
                style_spec=StyleSpec(notes="极简白底"), use_document_style=False
            )
        ),
    )

    assert result.requirement.style_spec.colors == []
    assert result.requirement.style_spec.notes == "极简白底"
    assert any("忽略文档风格" in item for item in result.warnings)


def test_no_digests_means_no_style_tokens() -> None:
    """没有文档时不该凭空出现令牌（只有模型写的 notes）。"""
    result = merge(user_message="做个待办清单", chain=_chain(_decision()))

    assert result.requirement.style_spec.colors == []
    assert result.requirement.style_spec.notes == "极简白底"


# --------------------------------------------------------------------------
# 3. 槽位：只增不减
# --------------------------------------------------------------------------


def test_slots_are_never_cleared_by_empty_model_output() -> None:
    """⚠️ 模型返回空槽位时，用户已经说过的槽位必须保住。"""
    given = RequirementSlots(site_kind="单页展示", features=["添加待办"], style="极简白底")

    result = merge(
        user_message="继续",
        slots=given,
        chain=_chain(_decision(slots=RequirementSlots())),
    )

    assert result.requirement.slots.site_kind == "单页展示"
    assert result.requirement.slots.features == ["添加待办"]
    assert result.requirement.slots.style == "极简白底"


def test_model_can_add_new_slots_from_documents() -> None:
    """模型从文档里发现的新信息（如需持久化）可以补进槽位。"""
    result = merge(
        user_message="按文档做",
        slots=RequirementSlots(site_kind="单页展示", features=["添加待办"]),
        chain=_chain(
            _decision(
                slots=RequirementSlots(need_persistence=True, audience="内部员工")
            )
        ),
    )

    slots = result.requirement.slots
    assert slots.need_persistence is True
    assert slots.audience == "内部员工"
    assert slots.site_kind == "单页展示", "旧槽位仍在"


# --------------------------------------------------------------------------
# 4. 不确定项：文档没说清的点不能丢
# --------------------------------------------------------------------------


def test_digest_open_questions_reach_uncertainty() -> None:
    """⚠️ 文档里的"待确认"必须进 uncertainty —— 沉默会让下游编一个出来。"""
    result = merge(
        user_message="按文档做",
        digests=[("@doc1", _digest(open_questions=["是否需要多语言？"]))],
        chain=_chain(_decision(uncertainty=["是否要登录功能？"])),
    )

    assert "是否要登录功能？" in result.requirement.uncertainty
    assert "@doc1：是否需要多语言？" in result.requirement.uncertainty


def test_uncertainty_is_deduped() -> None:
    """模型与文档给出同一个问题时只保留一条。"""
    result = merge(
        user_message="按文档做",
        digests=[("@doc1", _digest(open_questions=["是否需要登录？"]))],
        chain=_chain(_decision(uncertainty=["是否需要登录？"])),
    )

    assert result.requirement.uncertainty.count("是否需要登录？") == 1
    assert "@doc1：是否需要登录？" in result.requirement.uncertainty


# --------------------------------------------------------------------------
# 5. 降级路径
# --------------------------------------------------------------------------


def test_model_failure_falls_back_to_python_merge() -> None:
    """调用失败 → Python 直接合并，且**明说**这次归并没有经过模型。"""
    result = merge(
        user_message="做个待办清单",
        slots=RequirementSlots(site_kind="单页展示"),
        digests=[
            (
                "@doc1",
                _digest(
                    tokens={"colors": ["#0f172a"]},
                    content_points=["标题：我的待办"],
                    constraints=["必须响应式"],
                    open_questions=["要不要登录？"],
                ),
            )
        ],
        chain=_boom_chain("模型服务超时"),
    )

    requirement = result.requirement
    assert result.degraded is True
    assert "降级" in requirement.summary
    assert requirement.content_points == ["标题：我的待办"]
    assert requirement.constraints == ["必须响应式"]
    assert requirement.style_spec.colors == ["#0f172a"], "降级也不能丢风格事实"
    assert requirement.slots.site_kind == "单页展示"
    assert any("未经过模型整合" in item for item in requirement.uncertainty)
    assert any("超时" in item for item in result.warnings)


def test_structured_parse_failure_still_records_usage() -> None:
    """⚠️ 结构化解析失败也要记账（失败同样烧了 token）。"""
    result = merge(
        user_message="做个待办清单",
        chain=_chain(None, _raw(input_tokens=66, output_tokens=11)),
    )

    assert result.degraded is True
    assert result.usage.input_tokens == 66
    assert "结构化解析失败" in result.warnings[0]


def test_success_records_usage() -> None:
    """成功路径的用量从 raw AIMessage 读出。"""
    result = merge(user_message="做个页面", chain=_chain(_decision(), _raw(input_tokens=120)))

    assert result.degraded is False
    assert result.usage.input_tokens == 120


def test_degraded_merge_without_documents() -> None:
    """没有文档时降级也要能跑（需求只剩用户原话与槽位）。"""
    result = merge(
        user_message="做个计算器",
        slots=RequirementSlots(site_kind="表单工具"),
        chain=_boom_chain(),
    )

    assert result.requirement.content_points == []
    assert result.requirement.slots.site_kind == "表单工具"
    assert result.requirement.sources[0].kind == "user"


# --------------------------------------------------------------------------
# 6. 消息装配与契约
# --------------------------------------------------------------------------


def test_merge_message_orders_sources_by_priority() -> None:
    """装配出来的用户消息里，四个来源必须按优先级排列，并带上用户原话与文档内容。

    ⚠️ 只检查**用户消息**（payload 的最后一条），不是把系统提示词也拼进来：
    系统提示词里为了解释规则同样会提到"来源 4"，拼在一起会让"位置顺序"这个断言失去意义。
    """
    seen: list[str] = []

    def _record(payload: list) -> dict:
        seen.append(payload[-1].content)
        return {"raw": _raw(), "parsed": _decision()}

    merge(
        user_message="不要用文档配色",
        slots=RequirementSlots(site_kind="单页展示"),
        draft_summary="用户想做个官网",
        digests=[("@doc1", _digest(tokens={"colors": ["#0f172a"]}))],
        chain=RunnableLambda(_record),
    )

    text = seen[0]
    assert (
        text.index("来源 1")
        < text.index("来源 2")
        < text.index("来源 3")
        < text.index("来源 4")
    ), "来源顺序即优先级，模型要能一眼看出谁压谁"
    assert "不要用文档配色" in text
    assert "@doc1" in text and "#0f172a" in text


def test_merge_message_marks_absent_documents() -> None:
    """没有文档、也没做检索判定时都要显式写明，否则模型会以为漏读了。"""
    seen: list[str] = []

    def _record(payload: list) -> dict:
        seen.append(payload[-1].content)
        return {"raw": _raw(), "parsed": _decision()}

    merge(user_message="做个页面", chain=RunnableLambda(_record))

    assert "（本轮没有文档）" in seen[0]
    assert "（本次未做检索判定）" in seen[0]


def test_final_requirement_json_serializable() -> None:
    """最终需求要写进 PG 的 ``generation_plan``（阶段 5），必须可序列化。"""
    result = merge(
        user_message="做个待办清单",
        digests=[("@doc1", _digest(tokens={"colors": ["#0f172a"]}))],
        chain=_chain(_decision()),
    )

    assert isinstance(result.requirement, FinalRequirement)
    payload = json.dumps(result.requirement.model_dump(), ensure_ascii=False)

    assert "#0f172a" in payload
    assert json.loads(payload)["sources"][0]["kind"] == "user"


def test_final_requirement_prompt_text_sections() -> None:
    """给下游的渲染文本要包含概述/槽位/约束/不确定项；空风格段不出现。"""
    requirement = FinalRequirement(
        summary="做一个待办清单",
        slots=RequirementSlots(site_kind="单页展示"),
        constraints=["必须响应式"],
        uncertainty=["是否需要登录？"],
    )

    rendered = requirement.as_prompt_text()

    assert "【需求概述】" in rendered
    assert "【槽位】" in rendered
    assert "【硬约束】" in rendered
    assert "【不确定项" in rendered
    assert "【风格规范】" not in rendered, "没有风格信息时不要假装有"


def test_build_merge_chain_uses_structured_output() -> None:
    """链必须带结构化输出。"""
    assert build_merge_chain() is not None


def test_prompt_loads_and_states_priority() -> None:
    """提示词必须能加载，且写明优先级与"不猜风格取值"。"""
    content = load_prompt("requirement_merge_system")

    assert "优先级" in content
    assert "use_document_style" in content
    assert "不要填" in content, "要明确禁止模型填结构化风格取值"


def test_model_usage_type_is_stable() -> None:
    """降级路径的用量必须是 ModelUsage（前端与落库都按它的字段读）。"""
    result = merge(user_message="x", chain=_boom_chain())

    assert isinstance(result.usage, ModelUsage)


# --------------------------------------------------------------------------
# 7. RAG 三态：hit / miss / skipped 在 merge 里必须**三种不同**的待遇
# --------------------------------------------------------------------------


def test_rag_miss_becomes_user_facing_uncertainty() -> None:
    """⚠️ 核心用例：miss（需要资料但没查到）必须变成**面向用户、可执行**的不确定项。"""
    result = merge(
        user_message="按我们公司的品牌色做个官网",
        rag=RagResult(
            status="miss", query="公司 品牌色", reason="个人知识库检索尚未接入（二期）"
        ),
        chain=_chain(_decision()),
    )

    uncertainty = result.requirement.uncertainty
    assert any("个人知识库未命中" in item for item in uncertainty)
    assert any("检索尚未接入" in item for item in uncertainty), "原因要带上，不能只说没命中"
    assert any("上传对应文件" in item for item in uncertainty), "要给用户一个可执行的动作"


def test_rag_skipped_adds_nothing() -> None:
    """⚠️ skipped **什么都不加**：判定本就不需要私人资料，提示只会变成噪音。"""
    result = merge(
        user_message="做个计算器",
        rag=RagResult(status="skipped", query="", reason="本次需求不需要私人知识库"),
        chain=_chain(_decision()),
    )

    assert result.requirement.uncertainty == []
    assert "rag" not in [item.kind for item in result.requirement.sources]


def test_rag_hit_adds_source_but_no_uncertainty() -> None:
    """hit：留下出处证据，但不该产生"未命中"这类不确定项。"""
    result = merge(
        user_message="按我们的品牌色来做",
        rag=_rag_hit(text="公司主色 #123456"),
        chain=_chain(_decision()),
    )

    assert "rag" in [item.kind for item in result.requirement.sources]
    assert not any("未命中" in item for item in result.requirement.uncertainty)


def test_rag_none_is_treated_as_no_retrieval() -> None:
    """未接线时（rag=None）不应凭空产生 rag 证据或不确定项。"""
    result = merge(user_message="做个页面", chain=_chain(_decision()))

    assert "rag" not in [item.kind for item in result.requirement.sources]
    assert result.requirement.uncertainty == []


def test_rag_states_are_rendered_differently_in_prompt() -> None:
    """⚠️ 提示词里三态必须**说清楚**：空串会被模型理解成"用户资料里没有"，然后开编。"""
    seen: list[str] = []

    def _record(payload: list) -> dict:
        seen.append("\n".join(message.content for message in payload))
        return {"raw": _raw(), "parsed": _decision()}

    chain = RunnableLambda(_record)

    merge(user_message="按我们品牌色做", rag=_rag_hit(text="主色 #123456"), chain=chain)
    merge(
        user_message="按我们品牌色做",
        rag=RagResult(status="miss", query="品牌色", reason="检索尚未接入"),
        chain=chain,
    )
    merge(user_message="做个计算器", rag=RagResult(status="skipped"), chain=chain)

    assert "命中片段" in seen[0] and "#123456" in seen[0]
    assert "未命中" in seen[1]
    assert "不要据此编造" in seen[1], "未命中时要明确禁止模型编造用户的事实"
    assert "不需要私人资料" in seen[2]


def test_rag_warnings_are_forwarded() -> None:
    """检索阶段产生的警告（如"检索能力不可用"）要带到调用方眼前。"""
    result = merge(
        user_message="按我们品牌色做",
        rag=RagResult(
            status="miss",
            query="品牌色",
            reason="检索尚未接入",
            warnings=["检索能力不可用（provider=stub）：尚未接入"],
        ),
        chain=_chain(_decision()),
    )

    assert any("检索能力不可用" in item for item in result.warnings)


def test_degraded_merge_still_reports_rag_miss() -> None:
    """归并降级也不能把"知识库没查到"丢掉 —— 那是最需要用户知道的信息之一。"""
    result = merge(
        user_message="按我们公司规范做",
        rag=RagResult(status="miss", query="公司 规范", reason="检索尚未接入"),
        chain=_boom_chain(),
    )

    assert result.degraded is True
    assert any("个人知识库未命中" in item for item in result.requirement.uncertainty)


def test_merge_message_marks_absent_rag_decision_when_none() -> None:
    """没做检索判定时要写明"未做检索判定"，不要让模型以为"查过了、没有"。"""
    seen: list[str] = []

    def _record(payload: list) -> dict:
        seen.append("\n".join(message.content for message in payload))
        return {"raw": _raw(), "parsed": _decision()}

    merge(user_message="做个页面", chain=RunnableLambda(_record))

    assert "本次未做检索判定" in seen[0]
