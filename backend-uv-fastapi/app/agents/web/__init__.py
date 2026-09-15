"""web-agent 子包：工具集（虚拟文件系统上的读写）与（阶段 6 的）ReAct 循环。

为什么单独一个子包：阶段 6 会加入 `web_agent.py`（model ⇄ ToolNode 的 LangGraph 环），
它与 `tools.py` 一起构成"内层 Agent"，与外层的 `orchestrator.py` 明确区分开 ——
内层只关心"怎么把规划变成文件"，外层只关心"该走到哪一步"。
"""
