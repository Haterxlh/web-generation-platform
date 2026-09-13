# app/agents/multi_file_graph.py —— 多文件模式：LangGraph 状态图
"""多文件模式的状态图。

图结构（箭头 = 边，ok/retry = 条件边）::

    START → plan → generate → validate ─(ok)───→ END
                       ↑          │
                       └──(retry)─┘   （本轮有错 且 attempts < MAX_ATTEMPTS）

三个节点各司其职：

- ``plan``     ：用**非思考模式 + 结构化输出**定下站点标题 / 风格 / html 区块（设计约定 §7.3、§7.4）
- ``generate`` ：用**思考模式**一次生成三个文件（同一次上下文里先写 html 再写 css/js，天然自洽）；
                 重试时把上一轮的校验失败原因带进 prompt
- ``validate`` ：**纯 Python 校验**（抽取文件、检查引用关系与 viewport），不调模型

职责边界（设计约定 §3.2）：本模块是纯函数 —— 给它需求、还它 {文件名: 内容}；不落盘、不落库、不碰 HTTP。
"""

import re
from typing import TypedDict

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.agents.common import (
    GenerationFailedError,
    GenerationResult,
    GenerationTruncatedError,
    ModelUsage,
    ensure_not_truncated,
)
from app.core.llm_client import llm_client, llm_structured_client
from app.utils.weg_gen.code_extractor import CodeExtractError, extract_multi_files
from app.utils.weg_gen.prompt_loader import load_prompt

# 契约：多文件模式固定产出这三个文件（与 app/prompts/multi_file_system.md 一致）
REQUIRED_FILES = ("index.html", "style.css", "script.js")

# 最多生成几次（刹车：防止一直不合格把接口拖死）
MAX_ATTEMPTS = 2

# 单个文件短于这么多字符就认为"疑似占位或截断"
MIN_FILE_CHARS = 40


class SitePlan(BaseModel):
    """plan 节点的结构化输出：先想清楚"这个网站长什么样"，再让三个文件照着写。"""

    site_title: str = Field(description="网站标题（会用在 <title> 与页头）")
    style_hint: str = Field(
        description="配色与整体风格，例如：深色背景 #0f172a、主色 #4f46e5、圆角卡片、无衬线字体"
    )
    html_outline: str = Field(
        description="index.html 需要包含的区块，按从上到下的顺序，每行一个区块"
    )
    


class MultiFileState(TypedDict):
    """图的共享状态。

    Attributes:
        prompt: 用户需求原文。
        site_title/style_hint/html_outline: plan 的产出，注入每次生成。
        raw_output: generate 产出的模型原文。
        contents: validate 抽取出来的文件（**覆盖**语义：重试时整批替换，避免旧内容残留）。
        errors: validate 本轮的结论（**覆盖**语义，不是累加 —— 原因见 route 的注释）。
        attempts: 已生成次数（刹车）。
        usage: 累计用量（覆盖语义：节点自己加完再写回）。
        truncated: 本轮输出是否被截断。
    """

    prompt: str
    site_title: str
    style_hint: str
    html_outline: str
    raw_output: str
    contents: dict[str, str]
    errors: list[str]
    attempts: int
    usage: ModelUsage   # 累计用量（覆盖语义：节点自己加完再写回）
    truncated: bool     # 本轮输出是否被截断


_VIEWPORT_RE = re.compile(r'<meta[^>]+name=["\']viewport["\']', re.IGNORECASE)
_CSS_LINK_RE = re.compile(r'<link[^>]+href=["\']style\.css["\']', re.IGNORECASE)
_JS_SRC_RE = re.compile(r'<script[^>]+src=["\']script\.js["\']', re.IGNORECASE)

# 规划链在模块级建一次（结构化输出必须用非思考模式客户端，见设计约定 §7.3）
# 还要拿到原始 AIMessage —— 用量在它身上
_PLANNER = llm_structured_client.with_structured_output(SitePlan, include_raw=True)


def _build_user_message(state: MultiFileState) -> str:
    """拼出这次生成要用的用户消息：需求 + 规划 + （重试时）上一轮的失败原因。

    Args:
        state: 当前图状态。

    Returns:
        用户消息文本。
    """
    parts = [f"用户需求：\n{state['prompt']}"]

    if state.get("site_title"):
        parts.append(f"站点标题：{state['site_title']}")
    if state.get("style_hint"):
        parts.append(f"整体风格：{state['style_hint']}")
    if state.get("html_outline"):
        parts.append(f"index.html 需要包含的区块：\n{state['html_outline']}")

    # 这一步是"重试"真正有用的地方：把上一轮哪里不合格明确告诉模型
    if state.get("errors"):
        details = "\n".join(f"- {error}" for error in state["errors"])
        parts.append(f"上一次生成不合格，这次必须修正以下问题：\n{details}")

    return "\n\n".join(parts)


def _extract_and_check(raw_output: str) -> tuple[dict[str, str], list[str]]:
    """把模型原文抽成文件，并做"结构级"校验（不判断美观，只看硬性的引用关系）。

    Args:
        raw_output: 模型返回的原文。

    Returns:
        (文件字典, 问题清单)；抽取失败时文件字典为空。
    """
    try:
        contents = extract_multi_files(raw_output)
    except CodeExtractError as error:
        # 可预期的失败 → 变成状态，让图有机会重试；异常只留给真正意外的情况
        return {}, [str(error)]

    errors: list[str] = []
    html = contents["index.html"]

    if not _VIEWPORT_RE.search(html):
        errors.append("index.html 缺少 viewport meta（移动端响应式会失效）")
    if not _CSS_LINK_RE.search(html):
        errors.append('index.html 里没有 <link rel="stylesheet" href="style.css">')
    if not _JS_SRC_RE.search(html):
        errors.append('index.html 里没有 <script src="script.js"></script>')

    for name, text in contents.items():
        if len(text) < MIN_FILE_CHARS:
            errors.append(f"{name} 内容过短（{len(text)} 字符），疑似占位或截断")

    return contents, errors


def build_multi_file_graph(
    model: Runnable | None = None, planner: Runnable | None = None
):
    """构造多文件生成图。

    Args:
        model: 可注入的生成模型（测试用；默认用全局 llm_client）。
        planner: 可注入的规划链（测试用；默认用全局 llm_structured_client）。

    Returns:
        已编译的图（可直接 invoke / stream）。
    """
    llm = model or llm_client
    planner_chain = planner or _PLANNER
    system_prompt = load_prompt("multi_file_system")

    def plan_node(state: MultiFileState) -> dict:
        """规划节点：定标题 / 风格 / 区块清单（并累计用量）。"""
        usage = state.get("usage") or ModelUsage()
        try:
            result = planner_chain.invoke(
                f"为下面这个网页需求做一份简短规划。\n\n需求：{state['prompt']}"
            )
        except Exception:
            # 规划只是"提质量"的一步，不该拖垮整个生成
            return {"site_title": "", "style_hint": "", "html_outline": "", "usage": usage}

        raw = result.get("raw")
        if raw is not None:
            usage = usage + ModelUsage.from_message(raw)

        plan = result.get("parsed")
        if plan is None:
            # 结构化解析失败（模型没按 schema 调工具）：降级，但用量照记
            return {"site_title": "", "style_hint": "", "html_outline": "", "usage": usage}

        return {
            "site_title": plan.site_title,
            "style_hint": plan.style_hint,
            "html_outline": plan.html_outline,
            "usage": usage,
        }

    def generate_node(state: MultiFileState) -> dict:
        """生成节点：一次生成三个文件（并累计用量）。"""
        message = llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=_build_user_message(state)),
            ]
        )
        usage = (state.get("usage") or ModelUsage()) + ModelUsage.from_message(message)
        update = {"attempts": state.get("attempts", 0) + 1, "usage": usage}

        try:
            ensure_not_truncated(message)
        except GenerationTruncatedError as error:
            # 截断不再让整张图崩掉，而是变成"不合格 → 走重试"（重试还可能成功）
            return {**update, "raw_output": message.text, "truncated": True, "errors": [str(error)]}

        return {**update, "raw_output": message.text, "truncated": False}

    def validate_node(state: MultiFileState) -> dict:
        """校验节点：抽取 + 结构校验，只写 contents 与 errors。"""
        contents, errors = _extract_and_check(state.get("raw_output", ""))
        if state.get("truncated"):
            errors.insert(0, "上一次输出被 max_tokens 截断，页面不完整（建议调大 LLM_MAX_TOKENS）")
        return {"contents": contents, "errors": errors}

    def route_after_validate(state: MultiFileState) -> str:
        """条件边：本轮的 errors 决定继续重试还是收工。

        注意 errors 用的是**覆盖**语义，不是 Annotated[..., operator.add]：
        如果用累加，重试成功之后旧错误还留在列表里，这里会永远认为"不合格"，
        只能靠 attempts 兜底，而外层还会把成功当失败。
        ——reducer 用错，就会把"历史"和"当前状态"混在同一个字段里。
        """
        if not state.get("errors"):
            return "done"
        if state.get("attempts", 0) >= MAX_ATTEMPTS:
            return "done"
        return "retry"

    builder = StateGraph(MultiFileState)
    builder.add_node("plan", plan_node)
    builder.add_node("generate", generate_node)
    builder.add_node("validate", validate_node)

    builder.add_edge(START, "plan")
    builder.add_edge("plan", "generate")
    builder.add_edge("generate", "validate")
    builder.add_conditional_edges(
        "validate", route_after_validate, {"retry": "generate", "done": END}
    )

    return builder.compile()


# 模块级只建一次图（图本身无状态，可反复 invoke）
_GRAPH = build_multi_file_graph()


def generate_multi_file(
    prompt: str, *, model: Runnable | None = None, planner: Runnable | None = None
) -> dict[str, str]:
    """多文件模式对外入口。

    Args:
        prompt: 用户需求描述。
        model: 可注入的生成模型（测试用）。
        planner: 可注入的规划链（测试用）。

    Returns:
        {"index.html": ..., "style.css": ..., "script.js": ...}

    Raises:
        GenerationTruncatedError: 模型输出被截断。
        CodeExtractError: 重试 MAX_ATTEMPTS 次后仍不齐全。
    """
    graph = (
        _GRAPH
        if model is None and planner is None
        else build_multi_file_graph(model=model, planner=planner)
    )
    state = graph.invoke(
        {"prompt": prompt, "attempts": 0, "usage": ModelUsage()}, 
        config={"recursion_limit": 25},
    )

    usage = state.get("usage") or ModelUsage()
    contents = state.get("contents") or {}
    missing = [name for name in REQUIRED_FILES if name not in contents]
    if missing:
        raise GenerationFailedError(
            f"多文件生成失败（已尝试 {state.get('attempts')} 次）：{state.get('errors')}", usage
        )
    return GenerationResult(files=contents, usage=usage)