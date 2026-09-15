"""阶段 4 的离线测试（一）：检索必要性判定（need_rag）。

这一节点的价值不在"能调模型"，而在两处 Python 兜底 —— 它们都对应真实会出事的场景：

- **`need=True` 却没给检索词**：放行会让二期拿着空串去检索，结果必然是"没查到"，
  而用户看到的是"你的资料里没有" —— 这是**静默失败**，必须由 Python 补一个可用的 query；
- **判定调用失败**：降级方向必须显式且可配置（默认判"不需要"），
  否则一次超时会立刻变成一条"知识库未命中"的告警，把用户引向错误的排查方向。
"""

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agents.rag.need_rag import (
    MAX_QUERY_CHARS,
    NeedRagDecision,
    build_need_rag_chain,
    decide,
)
from app.agents.state import RequirementDigest, RequirementSlots
from app.utils.weg_gen.prompt_loader import load_prompt


def _raw(input_tokens: int = 40, output_tokens: int = 12) -> AIMessage:
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
    def _boom(_payload: object) -> object:
        raise RuntimeError(message)

    return RunnableLambda(_boom)


def _digest(summary: str = "公司品牌规范") -> RequirementDigest:
    return RequirementDigest(summary=summary, role="style")


SLOTS = RequirementSlots(
    site_kind="企业官网", features=["产品介绍", "联系方式"], style="蓝色主色", audience="客户"
)


# --------------------------------------------------------------------------
# 1. 正常路径
# --------------------------------------------------------------------------


def test_need_true_passes_through_with_query() -> None:
    """模型判定"需要检索"时，检索词与理由原样透传，用量记账。"""
    result = decide(
        user_message="按我们公司的品牌色做个官网",
        slots=SLOTS,
        chain=_chain(
            NeedRagDecision(need=True, query="公司 品牌色 规范", reason="依赖公司品牌色"),
            _raw(input_tokens=120),
        ),
    )

    assert result.decision.need is True
    assert result.decision.query == "公司 品牌色 规范"
    assert result.decision.reason == "依赖公司品牌色"
    assert result.usage.input_tokens == 120
    assert result.degraded is False
    assert result.warnings == []


def test_need_false_keeps_query_empty() -> None:
    """判定"不需要"时不折腾检索词（避免下游误以为要查）。"""
    result = decide(
        user_message="做个待办清单",
        chain=_chain(NeedRagDecision(need=False, query="", reason="通用需求")),
    )

    assert result.decision.need is False
    assert result.decision.query == ""
    assert result.warnings == []


def test_query_whitespace_is_normalized() -> None:
    """检索词要做空白归一：换行/多空格会让关键词串变形。"""
    result = decide(
        user_message="按公司规范做",
        chain=_chain(NeedRagDecision(need=True, query="  公司   品牌色 \n 规范 ")),
    )

    assert result.decision.query == "公司 品牌色 规范"


# --------------------------------------------------------------------------
# 2. Python 兜底：缺检索词
# --------------------------------------------------------------------------


def test_missing_query_is_filled_from_slots() -> None:
    """⚠️ 核心用例：模型说"要查"却没给检索词 → Python 必须补一个，并留下警告。"""
    result = decide(
        user_message="按公司规范做个官网",
        slots=SLOTS,
        chain=_chain(NeedRagDecision(need=True, query="   ", reason="依赖公司资料")),
    )

    assert result.decision.need is True
    assert result.decision.query, "空检索词会让二期检索必然空转（静默失败）"
    assert "企业官网" in result.decision.query
    assert "产品介绍" in result.decision.query
    assert any("兜底生成" in item for item in result.warnings), "兜底要留痕，便于归因检索质量"


def test_missing_query_without_slots_falls_back_to_user_message() -> None:
    """没有槽位时退化成用用户原话兜底（不理想，但比空串强）。"""
    result = decide(
        user_message="按我们公司的规范来",
        chain=_chain(NeedRagDecision(need=True, query="")),
    )

    assert result.decision.query == "按我们公司的规范来"


def test_fallback_query_is_clipped() -> None:
    """兜底检索词要限长：检索词是关键词串，不是整段需求。"""
    long_slots = RequirementSlots(
        site_kind="官网", features=["甲" * 80, "乙" * 80, "丙" * 80], style="丁" * 80
    )

    result = decide(
        user_message="x",
        slots=long_slots,
        chain=_chain(NeedRagDecision(need=True, query="")),
    )

    assert len(result.decision.query) <= MAX_QUERY_CHARS


def test_need_false_does_not_get_a_query() -> None:
    """判定"不需要"时**不**做兜底：没有检索就不该有检索词。"""
    result = decide(
        user_message="做个计算器",
        slots=SLOTS,
        chain=_chain(NeedRagDecision(need=False, query="")),
    )

    assert result.decision.query == ""
    assert "兜底生成" not in " ".join(result.warnings)


# --------------------------------------------------------------------------
# 3. 降级：方向显式且可配置
# --------------------------------------------------------------------------


def test_failure_defaults_to_not_needing_retrieval() -> None:
    """⚠️ 调用失败默认判"不需要"：判定失败却报"需要"会误导用户（见模块头注释）。"""
    result = decide(
        user_message="按公司规范做",
        slots=SLOTS,
        chain=_boom_chain("模型服务超时"),
    )

    assert result.degraded is True
    assert result.decision.need is False
    assert result.decision.query == ""
    assert "超时" in result.decision.reason
    assert any("降级" in item for item in result.warnings)


def test_failure_direction_is_configurable() -> None:
    """二期若想"宁可多查一次"，改一个参数即可（降级方向是一处显式决策）。"""
    result = decide(
        user_message="按公司规范做",
        slots=SLOTS,
        on_failure_need=True,
        chain=_boom_chain(),
    )

    assert result.decision.need is True
    assert result.decision.query, "既然判成需要，检索词也要兜底补上"
    assert "企业官网" in result.decision.query


def test_structured_parse_failure_records_usage() -> None:
    """⚠️ 结构化解析失败也要记账（失败同样烧了 token）。"""
    result = decide(
        user_message="按公司规范做",
        chain=_chain(None, _raw(input_tokens=77, output_tokens=9)),
    )

    assert result.degraded is True
    assert result.usage.input_tokens == 77
    assert any("结构化解析失败" in item for item in result.warnings)


# --------------------------------------------------------------------------
# 4. 消息装配与提示词
# --------------------------------------------------------------------------


def test_message_includes_message_slots_and_documents() -> None:
    """判定要看得到用户原话、槽位与"已经提供了什么资料"。"""
    seen: list[str] = []

    def _record(payload: list) -> dict:
        seen.append(payload[-1].content)
        return {"raw": _raw(), "parsed": NeedRagDecision(need=False)}

    decide(
        user_message="按我们公司的品牌色做",
        slots=SLOTS,
        draft_summary="用户想要企业官网",
        digests=[("@doc1", _digest("公司品牌规范 v2"))],
        chain=RunnableLambda(_record),
    )

    text = seen[0]
    assert "按我们公司的品牌色做" in text
    assert "企业官网" in text
    assert "@doc1" in text and "公司品牌规范 v2" in text


def test_message_marks_absent_documents() -> None:
    """没有文档时要写明，否则模型可能以为用户已经提供过资料。"""
    seen: list[str] = []

    def _record(payload: list) -> dict:
        seen.append(payload[-1].content)
        return {"raw": _raw(), "parsed": NeedRagDecision(need=False)}

    decide(user_message="随便做个页面", chain=RunnableLambda(_record))

    assert "（本轮没有上传文档）" in seen[0]


def test_prompt_loads_and_states_query_rules() -> None:
    """提示词要能加载，并写明 L1/L2 与"检索词是关键词不是问句"。"""
    content = load_prompt("need_rag_system")

    assert "L1" in content and "L2" in content
    assert "关键词" in content
    assert "不要过度判定" in content, "要防止把通用需求判成需要私人资料"


def test_build_chain_uses_structured_output() -> None:
    """链必须带结构化输出。"""
    assert build_need_rag_chain() is not None
