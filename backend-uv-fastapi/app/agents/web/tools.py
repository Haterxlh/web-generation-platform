# app/agents/web/tools.py —— web-agent 的工具集（虚拟文件系统上的读写）
#
# 三条设计约束（docs/agent_refactor_plan.md §3.7、§5 硬约束 2 与 12）：
#
#   1) **工具永不抛异常**：失败原因作为**字符串返回值**交给模型。
#      这是"感知闭环"的关键 —— 模型必须能看到失败并据此改正；
#      抛异常会让整个 Agent 循环崩掉，模型连自救的机会都没有。
#
#   2) **工具返回值就是提示词**：返回的字符串会原样进入模型上下文，
#      所以要"面向模型、可执行"（说清缺什么、下一步怎么做），
#      **绝不能**塞开发者诊断信息（沿用 2026-09-14 多文件失败复盘的教训：
#      连着两次把"请检查提示词的输出格式约定"喂回模型，它依然只输出 index.html）。
#
#   3) **闭包捕获自己的 store**：每次生成调用一次 `build_agent_tools()`，
#      store 由闭包持有 —— 绝不能让 store 变成模块级全局变量，否则并发下会串文件。
#
# 刻意**没有** finish / done 之类的"我完成了"工具：
# 完成判定权必须在 Python 侧（§5 硬约束 9），模型自述"我写完了"不算数。

import time
from typing import Any

from langchain_core.tools import BaseTool, tool

from app.agents.common import AgentTrace
from app.utils.weg_gen.file_store import FileStore
from app.utils.weg_gen.file_writer import UnsafeFileNameError


def build_agent_tools(store: FileStore, trace: AgentTrace | None = None) -> list[BaseTool]:
    """为**一次**生成构造工具集。

    Args:
        store: 本次生成专用的虚拟文件系统（被闭包捕获，不与其它请求共享）。
        trace: 可选的观测记录器；传入后每次工具调用都会被记下来。

    Returns:
        可直接交给 ``model.bind_tools()`` 的工具列表。
    """

    def _finish(name: str, arguments: dict[str, Any], result: str, started: float) -> str:
        """记录一次工具调用，并把结果原样返回给模型。

        Args:
            name: 工具名。
            arguments: 记录的参数（**刻意只记文件名/大小，不记文件正文**，
                否则 trace 会被几万字的 HTML 撑爆）。
            result: 返回给模型的字符串。
            started: 计时起点。

        Returns:
            result 本身。
        """
        if trace is not None:
            trace.record(
                tool=name,
                arguments=arguments,
                result=result,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        return result

    @tool
    def write_file(files: dict[str, str]) -> str:
        """把文件写入工作区；一次可以写一个或多个文件（推荐一次写多个，更省往返）。

        Args:
            files: 文件名到完整文件内容的映射，例如 {"index.html": "<!DOCTYPE html>..."}。
        """
        started = time.perf_counter()
        try:
            written = store.put_many(files)
        except UnsafeFileNameError as error:
            # 把"哪个名字不合法 + 合法名字长什么样 + 下一步怎么做"一次说清
            return _finish(
                "write_file",
                {"files": list(files)},
                f"写入失败：{error}。文件名必须是单层名字，只允许字母、数字、下划线、"
                "连字符与点，不能包含斜杠、反斜杠、.. 或任何路径成分。请换名后重试。",
                started,
            )
        return _finish(
            "write_file",
            {"files": list(files)},
            f"已写入 {len(written)} 个文件：{'、'.join(written)}。"
            f"工作区当前共 {len(store)} 个文件：{'、'.join(store.names())}。",
            started,
        )

    @tool
    def read_file(filename: str) -> str:
        """读取工作区里某个文件的完整内容。

        Args:
            filename: 要读取的文件名（必须是工作区里已存在的文件）。
        """
        started = time.perf_counter()
        content = store.get(filename)
        if content is None:
            existing = store.names()
            return _finish(
                "read_file",
                {"filename": filename},
                f"读取失败：工作区里没有 {filename}。"
                f"当前已有文件：{'、'.join(existing) if existing else '（空）'}。",
                started,
            )
        return _finish("read_file", {"filename": filename}, content, started)

    @tool
    def list_files() -> str:
        """列出工作区里当前已有的文件及各自的大小。"""
        started = time.perf_counter()
        names = store.names()
        if not names:
            return _finish(
                "list_files", {}, "工作区当前是空的，还没有写入任何文件。", started
            )
        sizes = store.sizes()
        lines = "\n".join(f"- {name}（{sizes[name]} 字符）" for name in names)
        return _finish(
            "list_files", {}, f"工作区共 {len(names)} 个文件：\n{lines}", started
        )

    return [write_file, read_file, list_files]
