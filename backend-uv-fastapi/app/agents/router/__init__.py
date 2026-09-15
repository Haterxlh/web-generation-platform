"""意图路由子包：一个结构化输出节点（不是 agent）。

`intent_router` 没有循环、不调用工具，只做一次结构化判定 —— 按
`docs/agent_refactor_plan.md` §3.2.1 的划分，它是"节点"而不是"agent"。
区分这一点有实际意义：节点可以纯离线单测（喂假 chain → 断言结构化产物）。
"""
