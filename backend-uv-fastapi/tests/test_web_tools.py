"""阶段 1 的离线测试：虚拟文件系统与 web-agent 工具集。

全程不连任何外部服务，只测纯内存逻辑。
这里每一条都对应一个真实会出事的场景：

- **并发串文件**：store 若被写成模块级全局变量，两个用户同时生成时 B 会读到 A 的文件；
- **工具抛异常**：会让整个 Agent 循环崩掉，模型连"看到错误并改正"的机会都没有；
- **路径穿越**：`../x` 若放行，模型就能写到产物目录之外。
"""

from collections.abc import Iterator

import pytest

from app.agents.common import AgentTrace
from app.agents.web.tools import build_agent_tools
from app.utils.weg_gen.file_store import FileStore
from app.utils.weg_gen.file_writer import UnsafeFileNameError


@pytest.fixture
def store() -> FileStore:
    """每个用例一个全新的 store（模拟"每次生成新建一个"）。"""
    return FileStore()


# --------------------------------------------------------------------------
# 1. FileStore 基本行为
# --------------------------------------------------------------------------


def test_put_and_get_roundtrip(store: FileStore) -> None:
    """写入后应能按名字读回同样的内容。"""
    store.put("index.html", "<html></html>")

    assert store.get("index.html") == "<html></html>"
    assert store.names() == ["index.html"]
    assert len(store) == 1


def test_put_same_name_overwrites(store: FileStore) -> None:
    """同名写入是覆盖语义（Agent 反复改一个文件时依赖它）。"""
    store.put("style.css", "a{}")
    store.put("style.css", "b{}")

    assert store.get("style.css") == "b{}"
    assert len(store) == 1


@pytest.mark.parametrize(
    "bad_name",
    [
        "../evil.html",      # 路径穿越
        "a/b.css",           # 子目录
        "a\\b.css",          # Windows 分隔符
        "/abs.html",         # 绝对路径
        "C:/x.html",         # 盘符
        "..",                # 纯上跳
        "",                  # 空名
        "  ",                # 全空白
        "a b.css",           # 空格
        "文件.html",          # 非白名单字符（中文）
    ],
)
def test_put_rejects_unsafe_names(store: FileStore, bad_name: str) -> None:
    """非法文件名必须被拒绝，且**什么都不写进 store**。"""
    with pytest.raises(UnsafeFileNameError):
        store.put(bad_name, "x")

    assert len(store) == 0


def test_put_many_is_all_or_nothing(store: FileStore) -> None:
    """批量写入必须全成功或全失败 —— 不能留下半批文件（"写到一半"最危险）。"""
    with pytest.raises(UnsafeFileNameError):
        store.put_many({"index.html": "ok", "../evil.css": "bad"})

    assert store.names() == [], "有一个名字非法时，整批都不应写入"


def test_missing_reports_absent_names(store: FileStore) -> None:
    """missing() 是完成门禁的判据：只报"要求了但没写"的文件。"""
    store.put("index.html", "x")

    assert store.missing(["index.html", "style.css"]) == ["style.css"]


def test_snapshot_is_a_copy(store: FileStore) -> None:
    """snapshot() 必须返回拷贝：调用方改了它不能影响 store 本身。"""
    store.put("index.html", "a")
    snap = store.snapshot()
    snap["index.html"] = "tampered"

    assert store.get("index.html") == "a"


# --------------------------------------------------------------------------
# 2. ⚠️ 并发隔离（本次改造里最危险的 bug 的回归保护）
# --------------------------------------------------------------------------


def test_two_stores_do_not_share_files() -> None:
    """两个独立 store 写同名文件，内容必须互不串。

    这正是"store 绝不能是模块级全局变量"要防的事：
    若做成全局变量，A 的产物会被 B 覆盖。
    """
    store_a = FileStore()
    store_b = FileStore()

    store_a.put("index.html", "A 的页面")
    store_b.put("index.html", "B 的页面")

    assert store_a.get("index.html") == "A 的页面"
    assert store_b.get("index.html") == "B 的页面"


def test_two_tool_sets_do_not_share_files() -> None:
    """工具层同样必须隔离：两套 build_agent_tools 各管自己的 store。"""
    store_a, store_b = FileStore(), FileStore()
    tools_a = {t.name: t for t in build_agent_tools(store_a)}
    tools_b = {t.name: t for t in build_agent_tools(store_b)}

    tools_a["write_file"].invoke({"files": {"index.html": "A"}})
    tools_b["write_file"].invoke({"files": {"index.html": "B"}})

    assert store_a.get("index.html") == "A"
    assert store_b.get("index.html") == "B"
    assert tools_a["list_files"].invoke({}).count("index.html") == 1


# --------------------------------------------------------------------------
# 3. 工具行为：永不抛异常，返回值面向模型
# --------------------------------------------------------------------------


def test_build_agent_tools_exposes_expected_tools() -> None:
    """工具集应恰好是这三个，且每个都有可用的 schema（能被 bind_tools 接受）。"""
    tools = build_agent_tools(FileStore())
    by_name = {t.name: t for t in tools}

    assert set(by_name) == {"write_file", "read_file", "list_files"}
    for item in tools:
        assert item.description, f"{item.name} 缺少 description（模型只能靠它理解工具）"
        assert isinstance(item.args_schema or item.args, dict) or item.args_schema is not None


def test_no_completion_tool_exists() -> None:
    """刻意不提供 finish/done 这类工具：完成判定权必须在 Python 侧。

    模型可能在只写了 1 个文件时就宣布"我完成了"，
    所以"是否交付完整"只能由 file_store.missing() 判定。
    """
    names = {t.name for t in build_agent_tools(FileStore())}

    assert not names & {"finish", "done", "complete", "submit"}


def test_write_file_reports_written_names(store: FileStore) -> None:
    """成功时返回"写了哪些文件 + 工作区现状"，让模型知道下一步还缺什么。"""
    tools = {t.name: t for t in build_agent_tools(store)}

    result = tools["write_file"].invoke({"files": {"index.html": "a", "style.css": "b"}})

    assert "已写入 2 个文件" in result
    assert "index.html" in result and "style.css" in result
    assert store.names() == ["index.html", "style.css"]


def test_write_file_returns_error_string_instead_of_raising(store: FileStore) -> None:
    """非法文件名必须变成**可读的错误字符串**，而不是异常。

    这是感知闭环的关键：模型要能读到"名字不合法"并换个名字重试。
    """
    tools = {t.name: t for t in build_agent_tools(store)}

    result = tools["write_file"].invoke({"files": {"../evil.html": "x"}})

    assert isinstance(result, str)
    assert "写入失败" in result
    assert "请换名后重试" in result, "要给出面向模型的下一步指令，而不只是报告失败"
    assert store.names() == []


def test_read_file_missing_lists_available_names(store: FileStore) -> None:
    """读不到时要告诉模型"现在有什么"，而不是只回一句失败。"""
    store.put("index.html", "a")
    tools = {t.name: t for t in build_agent_tools(store)}

    result = tools["read_file"].invoke({"filename": "script.js"})

    assert "读取失败" in result
    assert "index.html" in result


def test_read_file_returns_content(store: FileStore) -> None:
    """读到了就原样返回内容。"""
    store.put("script.js", "console.log(1)")
    tools = {t.name: t for t in build_agent_tools(store)}

    assert tools["read_file"].invoke({"filename": "script.js"}) == "console.log(1)"


def test_list_files_on_empty_store(store: FileStore) -> None:
    """空工作区的提示要明确，便于模型判断"我还没开始写"。"""
    tools = {t.name: t for t in build_agent_tools(store)}

    assert "空的" in tools["list_files"].invoke({})


def test_list_files_shows_sizes(store: FileStore) -> None:
    """非空时列出文件名与大小。"""
    store.put("index.html", "12345")
    tools = {t.name: t for t in build_agent_tools(store)}

    result = tools["list_files"].invoke({})

    assert "index.html" in result
    assert "5 字符" in result


# --------------------------------------------------------------------------
# 4. AgentTrace：Agent 循环的可观测
# --------------------------------------------------------------------------


def test_trace_records_each_tool_call(store: FileStore) -> None:
    """每次工具调用都应留下记录，step 递增。"""
    trace = AgentTrace()
    tools = {t.name: t for t in build_agent_tools(store, trace)}

    tools["write_file"].invoke({"files": {"index.html": "a"}})
    tools["list_files"].invoke({})

    assert len(trace.steps) == 2
    assert [s.step for s in trace.steps] == [1, 2]
    assert [s.tool for s in trace.steps] == ["write_file", "list_files"]
    assert all(s.duration_ms >= 0 for s in trace.steps)


def test_trace_does_not_store_file_contents(store: FileStore) -> None:
    """trace 只记文件名，不记文件正文 —— 否则会被几万字的 HTML 撑爆。"""
    trace = AgentTrace()
    tools = {t.name: t for t in build_agent_tools(store, trace)}

    tools["write_file"].invoke({"files": {"index.html": "X" * 5000}})

    assert trace.steps[0].arguments == {"files": ["index.html"]}


def test_trace_summary_and_jsonl(store: FileStore) -> None:
    """摘要与 JSONL 输出要可用（阶段 6 会直接落盘成 _debug_trace.jsonl）。"""
    trace = AgentTrace()
    tools = {t.name: t for t in build_agent_tools(store, trace)}
    tools["write_file"].invoke({"files": {"index.html": "a"}})
    tools["write_file"].invoke({"files": {"style.css": "b"}})

    assert "共 2 次工具调用" in trace.summary()
    assert "write_file×2" in trace.summary()

    lines = trace.to_jsonl().splitlines()
    assert len(lines) == 2
    assert '"tool": "write_file"' in lines[0]


def test_empty_trace_summary() -> None:
    """没有调用过工具时摘要也要可读。"""
    assert AgentTrace().summary() == "未调用任何工具"
    assert AgentTrace().to_jsonl() == ""


def test_trace_is_optional(store: FileStore) -> None:
    """不传 trace 时工具照常工作（trace 是可选的可观测增强）。"""
    tools = {t.name: t for t in build_agent_tools(store)}

    assert "已写入 1 个文件" in tools["write_file"].invoke({"files": {"a.html": "x"}})


@pytest.fixture(autouse=True)
def _no_side_effects() -> Iterator[None]:
    """提示：本文件的用例不写磁盘、不连库 —— 产物只存在于内存 store 里。"""
    yield
