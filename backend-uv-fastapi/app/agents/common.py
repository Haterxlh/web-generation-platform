# app/agents/common.py —— agents 层内部公用的小工具
# 为什么单独一个模块：截断检查被"单文件"和"多文件"两条流程共用，
# 放这里可以避免 agents 包内互相 import 私有函数（multi_file_graph ← single_html_flow 是很糟的依赖方向）

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain_core.messages import AIMessage

@dataclass
class ModelUsage:
    """一次（或多次）模型调用的 token 用量。

    为什么单独记录 reasoning：思考模式下 reasoning token **计入 output**，
    实测一次单文件生成里 reasoning 占掉了 75% 的预算 —— 不单独看，
    就不知道为什么"页面写到一半被截断"。

    Attributes:
        input_tokens: 输入 token 数。
        output_tokens: 输出 token 数（含 reasoning）。
        reasoning_tokens: 其中用于"思考"的部分。
    """

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @classmethod
    def from_message(cls, message: AIMessage) -> "ModelUsage":
        """从模型返回的 AIMessage 里读出用量（缺字段时按 0 处理）。

        Args:
            message: 模型返回的 AIMessage。

        Returns:
            用量对象。
        """
        usage = getattr(message, "usage_metadata", None) or {}
        details = usage.get("output_token_details") or {}
        return cls(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            reasoning_tokens=int(details.get("reasoning") or 0),
        )

    def __add__(self, other: "ModelUsage") -> "ModelUsage":
        """累加多次调用的用量（一次生成可能包含 plan + generate + 重试）。"""
        if not isinstance(other, ModelUsage):
            return NotImplemented
        return ModelUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )


@dataclass
class GenerationResult:
    """agents 层的统一返回值：文件 + 这次生成消耗的 token。

    Attributes:
        files: {文件名: 内容}。
        usage: 本次生成（含重试）累计的 token 用量。
    """

    files: dict[str, str]
    usage: ModelUsage


class GenerationFailedError(RuntimeError):
    """生成失败（可预期的原因），并携带本次消耗的 token 用量与模型原文。

    为什么异常要带用量：**失败的任务同样烧了 token**，记账不能只记成功的。
    为什么异常要带原文：失败时原文若不落盘，排查就只能"再烧一次 token 复现"——
    这是最贵的调试方式（2026-09-14 多文件失败复盘）。
    """

    def __init__(
        self,
        message: str,
        usage: ModelUsage | None = None,
        raw_output: str | None = None,
    ) -> None:
        super().__init__(message)
        self.usage = usage or ModelUsage()
        self.raw_output = raw_output


# DeepSeek 在 max_tokens 用尽时给出的 finish_reason
TRUNCATED_FINISH_REASON = "length"


class GenerationTruncatedError(GenerationFailedError):
    """模型输出被 max_tokens 截断，产物不完整。"""


def _ensure_complete_html(html: str) -> None:
    """确认模型给的是"完整页面"，而不是写到一半的残片。

    为什么必须有这道检查：抽取器对"没收尾的围栏"是容错的（流式场景需要），
    所以被 max_tokens 截断的输出也能被"成功"抽出来 —— 只能在这里补一道结构检查。

    Args:
        html: 抽取出来的 HTML 内容。

    Raises:
        GenerationTruncatedError: 缺少 </html>，判定为输出不完整。
    """
    if "</html>" not in html.lower():
        raise GenerationTruncatedError(
            "生成的 HTML 不完整（缺少 </html>），输出可能被截断；请调大 LLM_MAX_TOKENS"
        )


def ensure_not_truncated(message: AIMessage) -> None:
    """输出被截断时立刻报错，别把"半截网页"当成成功。

    思考模式下 reasoning token 也计入 max_tokens，所以"写到一半没配额了"是真实会发生的。
    缺 finish_reason 时（流式场景等）不拦，宁可放过也不错杀。

    Args:
        message: 模型返回的 AIMessage。

    Raises:
        GenerationTruncatedError: finish_reason 为 length。
    """
    finish_reason = (message.response_metadata or {}).get("finish_reason")
    if finish_reason == TRUNCATED_FINISH_REASON:
        raise GenerationTruncatedError(
            "模型输出被 max_tokens 截断，页面不完整；请调大 .env 的 LLM_MAX_TOKENS 或把需求拆小"
        )


# ==================== 难度 → 预算（阶段 5） ====================


# 难度档位的顺序：**只允许往上调**，不允许降级（见 resolve_difficulty）
DIFFICULTY_ORDER: tuple[str, ...] = ("easy", "medium", "hard")

# 未知难度的兜底档位：宁可多给一点预算，也不要因为一个拼错的字符串把任务卡死
DEFAULT_DIFFICULTY = "medium"


@dataclass(frozen=True)
class StageBudget:
    """一个难度档位的**执行预算**（阶段 5 起由 plan-agent 的 difficulty 决定）。

    为什么 difficulty 必须映射到这几个数字：否则它只是个装饰品 ——
    模型说"这个需求很难"，而 Harness 仍然只给 6 步、12k token，结果是循环跑到一半被掐断，
    用户看到的却是"生成失败"。**难度必须真的改变 Harness 的行为**，这条才算落地。

    Attributes:
        difficulty: 档位名（easy / medium / hard）。
        max_steps: web-agent 循环的**工具调用步数上限**（防死循环，硬约束 10）。
        max_output_tokens: 该任务允许的输出 token 预算（含重试），用于刹车与告警。
        max_files: 该档位允许交付的文件数上限（用于难度复核，见 resolve_difficulty）。
    """

    difficulty: str
    max_steps: int
    max_output_tokens: int
    max_files: int


# ⚠️ 这份表是**唯一的数值真源**：档位阈值（几个文件算 medium）与预算都由它派生，
# 不另写一套 if/else —— 两处规则一旦漂移，就会出现"难度判成 hard 但预算按 easy 给"。
BUDGET_BY_DIFFICULTY: dict[str, StageBudget] = {
    # 单文件（原 single_html_flow 的成功经验：一次调用、产物完整）
    "easy": StageBudget(difficulty="easy", max_steps=6, max_output_tokens=12_000, max_files=1),
    # 经典三件套 html + css + js：够写、也够一次补缺
    "medium": StageBudget(difficulty="medium", max_steps=12, max_output_tokens=40_000, max_files=4),
    # 多页面 / 带数据 / 需要多轮定向补缺
    "hard": StageBudget(difficulty="hard", max_steps=20, max_output_tokens=80_000, max_files=8),
}


def budget_for(difficulty: str | None) -> StageBudget:
    """取某个难度的预算。

    Args:
        difficulty: 难度名；None 或无法识别时返回默认档位。

    Returns:
        对应的 `StageBudget`（未知难度返回 medium）。
    """
    return BUDGET_BY_DIFFICULTY.get(
        (difficulty or "").strip().lower(), BUDGET_BY_DIFFICULTY[DEFAULT_DIFFICULTY]
    )


def difficulty_rank(difficulty: str | None) -> int:
    """难度的序号（用于比较"谁更高"）。

    Args:
        difficulty: 难度名。

    Returns:
        序号；无法识别时返回默认档位的序号。
    """
    key = (difficulty or "").strip().lower()
    if key not in DIFFICULTY_ORDER:
        key = DEFAULT_DIFFICULTY
    return DIFFICULTY_ORDER.index(key)


def difficulty_for_file_count(file_count: int) -> str:
    """按文件数推出**难度下限**（从预算表派生，不另写一套阈值）。

    Args:
        file_count: 计划交付的文件数。

    Returns:
        难度下限；文件数为 0 时按 easy 处理（真正的兜底在 plan-agent 里）。
    """
    for name in DIFFICULTY_ORDER:
        if file_count <= BUDGET_BY_DIFFICULTY[name].max_files:
            return name
    return DIFFICULTY_ORDER[-1]


def resolve_difficulty(declared: str | None, file_count: int) -> tuple[str, str | None]:
    """复核难度：**只能上调，不能下调**。

    两种偏差的方向是不对称的：

    - 模型把 ``hard`` 判成 ``easy`` → 预算不够，循环半路被掐断，用户拿到失败（**必须防**）；
    - 模型把 ``easy`` 判成 ``hard`` → 只是多给预算，最坏是多花一点钱（可接受）。

    所以这里取"模型判定"与"文件数推算"的**较高者**：
    既不裁剪模型声明的文件（那等于静默丢掉交付物），也不让它低报难度。
    模型没给或给了个无效值时，直接按文件数推算（并说明原因，便于优化提示词）。

    Args:
        declared: 模型声明的难度。
        file_count: 清洗后的实际文件数。

    Returns:
        (最终难度, 调整原因或 None)。原因会写进 plan 的警告与
        ``generation_plan.difficulty_declared`` 的对照里，便于后续优化提示词。
    """
    floor = difficulty_for_file_count(file_count)
    key = (declared or "").strip().lower()

    if key not in DIFFICULTY_ORDER:
        label = "未给出难度" if not key else f"给出的难度 {declared!r} 无效"
        return floor, f"模型{label}，已按 {file_count} 个文件推算为 {floor}"

    if difficulty_rank(key) >= difficulty_rank(floor):
        return key, None

    return (
        floor,
        f"模型声明难度为 {key}，但计划交付 {file_count} 个文件（该难度上限 "
        f"{budget_for(key).max_files} 个），已按 {floor} 上调预算",
    )


# ==================== Agent 循环的可观测（Harness 六件套之⑤） ====================


@dataclass
class ToolCallRecord:
    """一次工具调用的观测记录。

    Attributes:
        step: 第几次工具调用（从 1 开始）。
        tool: 工具名。
        arguments: 调用参数（原样记录，便于复盘模型"问了什么"）。
        result: 工具返回给模型的字符串（这是模型实际看到的东西）。
        duration_ms: 工具自身耗时（毫秒）。
    """

    step: int
    tool: str
    arguments: dict[str, Any]
    result: str
    duration_ms: int


@dataclass
class AgentTrace:
    """Agent 循环的逐轮记录。

    为什么必须单独记（不是为了日志好看）：
      Agent 的失败常常不是"最后一次调用错了"，而是"第 3 步的工具返回被模型忽略了"。
      没有逐轮的工具调用记录，排查只能靠猜。
      成功时它同样是证据来源 —— 阶段 6 的"模型自主性三层验证"
      （是否自主调工具 / 是否自主拆解 / 是否响应失败）就是直接读它。

    Attributes:
        steps: 按时间顺序累积的工具调用记录。
    """

    steps: list[ToolCallRecord] = field(default_factory=list)

    def record(
        self, tool: str, arguments: dict[str, Any], result: str, duration_ms: int
    ) -> ToolCallRecord:
        """追加一条工具调用记录（step 自动递增）。

        Args:
            tool: 工具名。
            arguments: 调用参数。
            result: 返回给模型的字符串。
            duration_ms: 工具耗时（毫秒）。

        Returns:
            刚写入的记录。
        """
        item = ToolCallRecord(
            step=len(self.steps) + 1,
            tool=tool,
            arguments=arguments,
            result=result,
            duration_ms=duration_ms,
        )
        self.steps.append(item)
        return item

    def to_jsonl(self) -> str:
        """渲染成 JSON Lines，便于落盘成 ``_debug_trace.jsonl``（一行一条，可直接 tail）。

        Returns:
            JSON Lines 文本；没有记录时返回空字符串。
        """
        return "\n".join(
            json.dumps(asdict(item), ensure_ascii=False) for item in self.steps
        )

    def summary(self) -> str:
        """一行摘要：调了几次、各是什么工具、合计耗时。

        Returns:
            摘要文本（如 ``"共 3 次工具调用（write_file×3），耗时 12ms"``）。
        """
        if not self.steps:
            return "未调用任何工具"

        counts: dict[str, int] = {}
        for item in self.steps:
            counts[item.tool] = counts.get(item.tool, 0) + 1
        detail = "、".join(f"{name}×{count}" for name, count in counts.items())
        total_ms = sum(item.duration_ms for item in self.steps)
        return f"共 {len(self.steps)} 次工具调用（{detail}），耗时 {total_ms}ms"
