"""阶段 5 的离线测试（一）：难度 → 预算（StageBudget）。

这组用例守的是一条很容易被当成"配置表"而忽略的约定：
**difficulty 必须真的改变 Harness 的行为**（步数、token、文件数上限），
否则它只是个装饰品 —— 模型说"这个需求很难"，而循环仍然只给 6 步，
结果是跑到一半被掐断，用户看到的是"生成失败"。

另外还守住"只能上调、不能下调"这条不对称规则：
低报难度会让任务半路失败，高报只是多花一点钱。
"""

import pytest

from app.agents.common import (
    BUDGET_BY_DIFFICULTY,
    DEFAULT_DIFFICULTY,
    DIFFICULTY_ORDER,
    StageBudget,
    budget_for,
    difficulty_for_file_count,
    difficulty_rank,
    resolve_difficulty,
)


# --------------------------------------------------------------------------
# 1. 档位数值本身即契约
# --------------------------------------------------------------------------


def test_budget_values_are_the_agreed_ones() -> None:
    """三个档位的数值是与人确认过的约定，改它要同时改预算告警与文档。"""
    easy, medium, hard = (BUDGET_BY_DIFFICULTY[name] for name in DIFFICULTY_ORDER)

    assert (easy.max_steps, easy.max_output_tokens, easy.max_files) == (6, 12_000, 1)
    assert (medium.max_steps, medium.max_output_tokens, medium.max_files) == (12, 40_000, 4)
    assert (hard.max_steps, hard.max_output_tokens, hard.max_files) == (20, 80_000, 8)


def test_budget_increases_monotonically() -> None:
    """难度越高，步数 / token / 文件数上限都必须更大（否则档位失去意义）。"""
    budgets = [BUDGET_BY_DIFFICULTY[name] for name in DIFFICULTY_ORDER]

    assert [item.max_steps for item in budgets] == sorted(item.max_steps for item in budgets)
    assert [item.max_output_tokens for item in budgets] == sorted(
        item.max_output_tokens for item in budgets
    )
    assert [item.max_files for item in budgets] == sorted(item.max_files for item in budgets)


def test_budget_is_frozen() -> None:
    """预算是不可变对象：运行期被就地改写会让"同一难度的行为"变得不可复现。"""
    with pytest.raises(Exception):
        BUDGET_BY_DIFFICULTY["easy"].max_steps = 99  # type: ignore[misc]


@pytest.mark.parametrize("value", ["easy", "EASY", " Easy "])
def test_budget_lookup_is_case_insensitive(value: str) -> None:
    """难度名的比对要去空白、忽略大小写（模型输出不会总是规范）。"""
    assert budget_for(value).difficulty == "easy"


@pytest.mark.parametrize("value", [None, "", "unknown", "very hard"])
def test_unknown_difficulty_falls_back_to_medium(value: str | None) -> None:
    """无法识别的难度退到默认档位 —— 宁可多给预算，也不要因为一个拼错的字符串卡死。"""
    budget = budget_for(value)

    assert isinstance(budget, StageBudget)
    assert budget.difficulty == DEFAULT_DIFFICULTY


def test_difficulty_rank_order() -> None:
    """序号用于比较高低：easy < medium < hard。"""
    assert difficulty_rank("easy") < difficulty_rank("medium") < difficulty_rank("hard")
    assert difficulty_rank("bogus") == difficulty_rank(DEFAULT_DIFFICULTY)


# --------------------------------------------------------------------------
# 2. 文件数 → 难度下限
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("file_count", "expected"),
    [(0, "easy"), (1, "easy"), (2, "medium"), (4, "medium"), (5, "hard"), (8, "hard"), (20, "hard")],
)
def test_difficulty_for_file_count_boundaries(file_count: int, expected: str) -> None:
    """阈值从预算表派生（不另写一套 if/else），因此边界与 max_files 完全一致。"""
    assert difficulty_for_file_count(file_count) == expected


# --------------------------------------------------------------------------
# 3. 复核规则：只能上调
# --------------------------------------------------------------------------


def test_consistent_declaration_keeps_declared_difficulty() -> None:
    """模型判得与文件数匹配时不改动、也不产生噪音警告。"""
    assert resolve_difficulty("easy", 1) == ("easy", None)
    assert resolve_difficulty("medium", 3) == ("medium", None)
    assert resolve_difficulty("hard", 6) == ("hard", None)


def test_low_declaration_is_escalated() -> None:
    """⚠️ 核心用例：声明 easy 却要交 4 个文件 → 必须上调（否则预算不够，任务半路失败）。"""
    difficulty, reason = resolve_difficulty("easy", 4)

    assert difficulty == "medium"
    assert reason and "上调" in reason
    assert "4 个文件" in reason


def test_escalation_can_jump_two_levels() -> None:
    """声明 easy 却要 6 个文件 → 直接跳到 hard（按文件数一次性算到位）。"""
    difficulty, reason = resolve_difficulty("easy", 6)

    assert difficulty == "hard"
    assert reason is not None


def test_high_declaration_is_never_downgraded() -> None:
    """声明 hard 但只交 1 个文件 → 保持不变（高报只是多给预算，低报才会失败）。"""
    assert resolve_difficulty("hard", 1) == ("hard", None)


def test_missing_declaration_uses_file_count() -> None:
    """模型没给难度时按文件数推算，并说明原因（便于优化提示词）。"""
    difficulty, reason = resolve_difficulty(None, 1)

    assert difficulty == "easy"
    assert reason and "推算" in reason


def test_invalid_declaration_uses_file_count() -> None:
    """模型编了个第四档难度时不硬套默认档，而是按文件数推算并留痕。"""
    difficulty, reason = resolve_difficulty("nightmare", 5)

    assert difficulty == "hard"
    assert reason and "无效" in reason


def test_escalation_reason_mentions_both_values() -> None:
    """上调原因里要同时有"模型说了什么"与"最终是什么"，否则事后无法判断是谁改的。"""
    _difficulty, reason = resolve_difficulty("medium", 9)

    assert reason is not None
    assert "medium" in reason and "hard" in reason
