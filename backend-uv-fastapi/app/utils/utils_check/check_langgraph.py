# app/utils/utils_check/check_langgraph.py —— 开发期脚本：LangGraph 1.x 最小示例（先不含 LLM）
# 运行：uv run python -m app.utils.utils_check.check_langgraph

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph


# ============ 示例 1：最小图（两个节点）============

class CalcState(TypedDict):
    """全图共享的"白板"：节点从它读，也往它写"""
    a: int
    b: int
    total: int
    product: int


def add_node(state: CalcState) -> dict:
    """节点①：算加法。返回"要更新的字段"，不用返回整个 state"""
    return {"total": state["a"] + state["b"]}


def mul_node(state: CalcState) -> dict:
    """节点②：算乘法"""
    return {"product": state["a"] * state["b"]}


def build_calc_graph():
    """搭图四步：声明 state → 登记节点 → 连边 → 编译"""
    builder = StateGraph(CalcState)      # ① 声明 state 结构

    builder.add_node("add", add_node)    # ② 登记节点（第一个参数是节点名）
    builder.add_node("mul", mul_node)

    builder.add_edge(START, "add")       # ③ 连边：入口 → add
    builder.add_edge("add", "mul")       #    add  → mul
    builder.add_edge("mul", END)         #    mul  → 出口

    return builder.compile()             # ④ 编译成可执行的图（别忘！）


# ============ 示例 2：reducer + 条件边 + 循环 ============

class RetryState(TypedDict):
    n: int
    # Annotated[类型, 合并函数]：该字段不再"覆盖"，而是按合并函数累加
    logs: Annotated[list[str], operator.add]


def work_node(state: RetryState) -> dict:
    n = state["n"] + 1
    return {"n": n, "logs": [f"try {n}"]}


def should_retry(state: RetryState) -> str:
    """条件边的"岔路判断"：只返回一个字符串"""
    return "again" if state["n"] < 3 else "done"


def build_retry_graph():
    builder = StateGraph(RetryState)
    builder.add_node("work", work_node)
    builder.add_edge(START, "work")
    # 条件边：work 跑完后，按 should_retry 的返回值选下一站
    builder.add_conditional_edges("work", should_retry, {"again": "work", "done": END})
    return builder.compile()


if __name__ == "__main__":
    g1 = build_calc_graph()
    print("1 最小图  :", g1.invoke({"a": 3, "b": 4}))

    g2 = build_retry_graph()
    print("2 循环重试:", g2.invoke({"n": 0, "logs": []}))
    print("3 逐节点流:", list(g2.stream({"n": 0, "logs": []}, stream_mode="updates")))