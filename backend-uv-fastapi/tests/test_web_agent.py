"""阶段 6 的离线测试（一）：内层 ReAct 环（web-agent）。

这是全项目**唯一真正的 agent**，所以这里断言的不是"输出对不对"，而是**循环行为**：

- 会不会自主调用工具、会不会分多轮把文件逐个写出来；
- **门禁能不能拦住"只写 1 个文件就宣布完成"**（本次改造最核心的一条）；
- 工具报错后模型能不能看到错误字符串并改正（感知闭环）；
- 三道刹车（步数 / token / 无进展）是否真的生效，且**不会丢掉已有产物**；
- trace 是否只记"做了什么"、**不记文件正文**。
"""

import json

import pytest
from langchain_core.messages import AIMessage

from app.agents.state import FilePlan, FinalRequirement, PlannedFile, RequirementSlots
from app.agents.web import web_agent
from app.utils.weg_gen.prompt_loader import load_prompt


def _tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _write(files: dict[str, str], call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[_tool_call("write_file", {"files": files}, call_id)])


def _read(name: str, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[_tool_call("read_file", {"filename": name}, call_id)])


def _done(text: str = "已全部写完") -> AIMessage:
    return AIMessage(content=text)


def _usage(message: AIMessage, input_tokens: int = 100, output_tokens: int = 50) -> AIMessage:
    message.usage_metadata = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    return message


class ScriptedModel:
    """按剧本返回 AIMessage 的假模型，并记录每次收到的消息（用于验证"模型看到了什么"）。"""

    def __init__(self, turns: list[AIMessage], *, repeat_last: bool = False) -> None:
        self.turns = turns
        self.repeat_last = repeat_last
        self.seen: list[list] = []

    def invoke(self, messages: list) -> AIMessage:
        self.seen.append(list(messages))
        index = len(self.seen) - 1
        if index < len(self.turns):
            return self.turns[index]
        if self.repeat_last and self.turns:
            return self.turns[-1]
        return _done("（剧本用尽）")


class BrokenModel:
    """调用即抛异常，用于验证"单轮失败不毁掉已有产物"。"""

    def __init__(self, fail_after: int = 0) -> None:
        self.calls = 0
        self.fail_after = fail_after

    def invoke(self, messages: list) -> AIMessage:
        self.calls += 1
        if self.calls > self.fail_after:
            raise RuntimeError("模型服务超时")
        return _write({"index.html": "<html></html>"}, f"call_{self.calls}")


PLAN_3 = FilePlan(
    difficulty="medium",
    entry_file="index.html",
    files=[
        PlannedFile(name="index.html", role="markup", summary="页面结构"),
        PlannedFile(name="style.css", role="style", depends_on=["index.html"]),
        PlannedFile(name="app.js", role="script", depends_on=["index.html"]),
    ],
)

PLAN_1 = FilePlan(
    difficulty="easy",
    entry_file="index.html",
    files=[PlannedFile(name="index.html", role="markup", summary="单文件页面")],
)

REQUIREMENT = FinalRequirement(
    summary="做一个待办清单页面",
    slots=RequirementSlots(site_kind="单页展示", features=["添加待办"]),
)


# --------------------------------------------------------------------------
# 1. 正常路径：自主调工具、多轮交付
# --------------------------------------------------------------------------


def test_single_turn_delivers_all_files() -> None:
    """一次 write_file 交齐清单里的文件 → 门禁通过。"""
    model = ScriptedModel([_usage(_write({"index.html": "A", "style.css": "B", "app.js": "C"}))])

    result = web_agent.generate(PLAN_3, REQUIREMENT, model=model)

    assert result.gate_passed is True
    assert result.missing == []
    assert sorted(result.files) == ["app.js", "index.html", "style.css"]
    assert result.steps_used == 2, "一次写全部文件 + 一次收工（模型不再调工具）"
    assert result.rounds == 1
    assert result.stop_reason == "done"
    assert result.degraded is False


def test_autonomous_multi_turn_delivery() -> None:
    """模型自己分多轮逐个写文件（"是否自主拆解"的第一层证据）。"""
    model = ScriptedModel(
        [
            _usage(_write({"index.html": "A"}, "c1")),
            _usage(_write({"style.css": "B"}, "c2")),
            _usage(_write({"app.js": "C"}, "c3")),
            _done(),
        ]
    )

    result = web_agent.generate(PLAN_3, REQUIREMENT, model=model)

    assert result.gate_passed is True
    assert result.steps_used == 4, "三次写入 + 一次收工"
    assert sorted(result.files) == ["app.js", "index.html", "style.css"]


def test_initial_message_carries_plan_and_requirement() -> None:
    """初始消息必须带交付清单与最终需求（清单不再硬编码在提示词里）。"""
    model = ScriptedModel([_done()])

    web_agent.generate(PLAN_3, REQUIREMENT, model=model)

    system_text = model.seen[0][0].content
    user_text = model.seen[0][1].content
    assert "交付清单" in user_text and "style.css" in user_text
    assert "做一个待办清单页面" in user_text
    assert "write_file" in system_text
    assert "没有" in system_text and "完成" in system_text, "要写明没有 finish 类工具"


# --------------------------------------------------------------------------
# 2. 门禁：本次改造最核心的一条
# --------------------------------------------------------------------------


def test_gate_blocks_partial_delivery() -> None:
    """⚠️ 核心用例：只写了 1 个文件就宣布完成 → **不得**判定通过。"""
    model = ScriptedModel([_usage(_write({"index.html": "A"})), _done("我完成了！")])

    result = web_agent.generate(PLAN_3, REQUIREMENT, model=model, max_repair_rounds=0)

    assert result.gate_passed is False
    assert sorted(result.missing) == ["app.js", "style.css"]
    assert result.rounds == 1, "max_repair_rounds=0 时不做补缺"
    assert result.files == {"index.html": "A"}, "已写好的文件不能丢"


def test_gate_fails_when_model_writes_nothing() -> None:
    """模型只是"聊天"不调工具 → 门禁全不过。"""
    model = ScriptedModel([_done("好的，我马上开始")], repeat_last=True)

    result = web_agent.generate(PLAN_1, REQUIREMENT, model=model, max_repair_rounds=0)

    assert result.gate_passed is False
    assert result.missing == ["index.html"]


def test_gate_requires_entry_file() -> None:
    """入口文件在清单里但没写 → 同样算缺件（预览会打不开任何东西）。"""
    model = ScriptedModel([_usage(_write({"style.css": "B"}))], repeat_last=True)

    result = web_agent.generate(PLAN_3, REQUIREMENT, model=model, max_repair_rounds=0)

    assert "index.html" in result.missing


# --------------------------------------------------------------------------
# 3. 定向补缺
# --------------------------------------------------------------------------


def test_repair_round_completes_delivery() -> None:
    """⚠️ 第一轮缺件 → 带"补缺指令"再跑一轮，把缺的补齐；同一 store 累积。"""
    model = ScriptedModel(
        [
            _usage(_write({"index.html": "A"}, "c1")),
            _done("先写入口"),
            _usage(_write({"style.css": "B", "app.js": "C"}, "c2")),
            _done(),
        ]
    )

    result = web_agent.generate(PLAN_3, REQUIREMENT, model=model)

    assert result.gate_passed is True
    assert result.rounds == 2
    assert sorted(result.files) == ["app.js", "index.html", "style.css"]
    assert any("补缺" in item for item in result.warnings)
    # 第二轮的用户消息里要列出缺件
    repair_message = model.seen[2][1].content
    assert "补缺指令" in repair_message
    assert "style.css" in repair_message and "app.js" in repair_message


def test_repair_rounds_are_capped() -> None:
    """补缺也有上限：多轮仍交不全就停，不能无限重试烧 token。"""
    model = ScriptedModel([_usage(_write({"index.html": "A"}, "c1")), _done()], repeat_last=True)

    result = web_agent.generate(PLAN_3, REQUIREMENT, model=model, max_repair_rounds=2)

    assert result.gate_passed is False
    assert result.rounds == 3, "1 轮 + 2 轮补缺"
    assert result.steps_used == 4, "每轮最多 2 步（写一次 + 收工），三轮共 4 步；不会无限重试"


# --------------------------------------------------------------------------
# 4. 三道刹车：步数 / token / 无进展
# --------------------------------------------------------------------------


def test_max_steps_brake_stops_the_loop() -> None:
    """⚠️ 步数上限生效，且停之前写下的文件必须还在。"""
    turns = [_usage(_write({"index.html": "A"}, f"c{index}")) for index in range(1, 10)]
    model = ScriptedModel(turns, repeat_last=True)

    result = web_agent.generate(PLAN_1, REQUIREMENT, model=model, max_steps=2)

    assert result.steps_used == 2
    assert result.stop_reason == "max_steps"
    assert result.degraded is True
    assert result.gate_passed is True, "两份写入里有 index.html，产物不该被丢掉"


def test_token_budget_brake_stops_the_loop() -> None:
    """输出 token 预算生效（思考 token 也计入 output，所以必须有这道刹车）。"""
    model = ScriptedModel([_usage(_write({"index.html": "A"}), output_tokens=500)], repeat_last=True)

    result = web_agent.generate(PLAN_3, REQUIREMENT, model=model, max_output_tokens=300)

    assert result.stop_reason == "token_budget"
    assert result.degraded is True
    assert result.steps_used == 1


def test_no_progress_detection_stops_loop() -> None:
    """⚠️ 连续多轮只读不写（快照不变）→ 停，免得把预算耗光。"""
    model = ScriptedModel([_usage(_read("index.html", f"c{index}")) for index in range(1, 9)],
                          repeat_last=True)

    result = web_agent.generate(PLAN_1, REQUIREMENT, model=model)

    assert result.stop_reason == "no_progress"
    assert result.degraded is True
    assert result.steps_used <= web_agent.NO_PROGRESS_LIMIT + 1


def test_writes_reset_no_progress_counter() -> None:
    """读一轮、写一轮交替不算"没有进展"（写入会刷新快照）。"""
    model = ScriptedModel(
        [
            _usage(_read("index.html", "c1")),
            _usage(_write({"index.html": "A"}, "c2")),
            _usage(_read("index.html", "c3")),
            _done(),
        ]
    )

    result = web_agent.generate(PLAN_1, REQUIREMENT, model=model)

    assert result.stop_reason == "done"
    assert result.gate_passed is True


def test_model_exception_keeps_partial_files() -> None:
    """⚠️ 单轮调用抛异常 → 停止并记为 error，但**已经写好的文件不丢**。"""
    model = BrokenModel(fail_after=1)

    result = web_agent.generate(PLAN_3, REQUIREMENT, model=model)

    assert result.stop_reason == "error"
    assert result.degraded is True
    assert result.files == {"index.html": "<html></html>"}
    assert result.gate_passed is False
    assert any("模型调用失败" in item for item in result.warnings)


# --------------------------------------------------------------------------
# 5. 感知闭环：工具报错后模型能读到并改正
# --------------------------------------------------------------------------


def test_model_sees_tool_error_and_can_correct() -> None:
    """⚠️ 核心用例（验收第二层）：故意让第一次 write_file 失败（非法文件名），
    模型必须在**下一轮的输入里看到错误字符串**，然后换个名字重试成功。"""
    model = ScriptedModel(
        [
            _usage(_write({"../evil.html": "X"}, "c1")),
            _usage(_write({"index.html": "A"}, "c2")),
            _done(),
        ]
    )

    result = web_agent.generate(PLAN_1, REQUIREMENT, model=model)

    assert result.gate_passed is True, "改正后应当交付成功"
    # 第二轮的输入里必须包含工具返回的错误文本
    second_round_text = "\n".join(str(message.content) for message in model.seen[1])
    assert "写入失败" in second_round_text
    assert "请换名后重试" in second_round_text


# --------------------------------------------------------------------------
# 6. 额外文件、trace 与回调
# --------------------------------------------------------------------------


def test_extra_files_are_kept_but_reported() -> None:
    """清单外的文件保留但记警告（不计入交付，也不静默丢掉用户可能需要的产物）。"""
    model = ScriptedModel(
        [_usage(_write({"index.html": "A", "extra.css": "B"})), _done()]
    )

    result = web_agent.generate(PLAN_1, REQUIREMENT, model=model)

    assert result.gate_passed is True
    assert result.extra_files == ["extra.css"]
    assert any("清单外" in item for item in result.warnings)


def test_trace_records_turns_without_file_contents() -> None:
    """⚠️ trace 只记"做了什么"（轮次 + 工具名 + 文件名），**绝不记文件正文**。"""
    secret = "console.log('这段内容不该出现在 trace 里')"
    model = ScriptedModel([_usage(_write({"index.html": "A", "app.js": secret})), _done()])

    result = web_agent.generate(PLAN_1, REQUIREMENT, model=model)

    lines = result.trace_jsonl.splitlines()
    assert len(lines) == 2, "一轮写文件 + 一轮收工"
    first = json.loads(lines[0])
    assert first["step"] == 1
    assert first["tool_calls"][0]["name"] == "write_file"
    assert first["tool_calls"][0]["args"] == {"files": ["index.html", "app.js"]}
    assert secret not in result.trace_jsonl
    assert "A" not in json.dumps(first["tool_calls"])


def test_on_step_callback_receives_progress() -> None:
    """阶段推进回调：每步一次，带步号与一句人话说明。"""
    seen: list[tuple[int, str]] = []
    model = ScriptedModel([_usage(_write({"index.html": "A"})), _done()])

    web_agent.generate(PLAN_1, REQUIREMENT, model=model, on_step=lambda s, d: seen.append((s, d)))

    assert seen[0][0] == 1
    assert "write_file" in seen[0][1] and "index.html" in seen[0][1]
    assert seen[1][0] == 2


def test_failing_callback_does_not_break_generation() -> None:
    """回调里通常是写数据库：它失败不该让生成整体失败，但要留痕。"""
    def _boom(_step: int, _detail: str) -> None:
        raise RuntimeError("数据库断了")

    model = ScriptedModel([_usage(_write({"index.html": "A"})), _done()])

    result = web_agent.generate(PLAN_1, REQUIREMENT, model=model, on_step=_boom)

    assert result.gate_passed is True
    assert any("步数回调失败" in item for item in result.warnings)


# --------------------------------------------------------------------------
# 7. 客户端选择与提示词
# --------------------------------------------------------------------------


def test_default_client_is_configurable() -> None:
    """默认客户端由一层薄封装决定：自检要能分别跑思考与非思考两种。"""
    from app.core.llm_client import llm_client, llm_no_thinking_client

    assert web_agent.build_web_agent_model(thinking=True) is llm_client
    assert web_agent.build_web_agent_model(thinking=False) is llm_no_thinking_client


def test_default_thinking_flag_matches_documented_decision() -> None:
    """默认值是一个**显式记录过的决策**（改它要同时改注释与文档）。"""
    assert isinstance(web_agent.DEFAULT_THINKING, bool)


def test_prompt_has_no_hardcoded_delivery_list() -> None:
    """⚠️ 提示词里**不许**再硬编码"交付清单"（清单改由 FilePlan 注入）。"""
    content = load_prompt("web_agent_system")

    assert "index.html" not in content, "硬编码三件套正是本次改造要根治的毛病"
    assert "style.css" not in content
    assert "write_file" in content
    assert "交付清单" in content, "要告诉模型清单会由系统给出"


def test_tool_wrapper_can_inject_failure() -> None:
    """可注入工具包装器：自检靠它在真实循环里做**故障注入**（第一次写入必失败）。

    ⚠️ 包装器拿到的是**本次请求的 store 与工具**：包装后仍必须委托给同一个 store 上的真工具，
    否则产物会写进另一个游魂 store，现象看起来像"模型根本不会写文件"。
    """
    from langchain_core.tools import StructuredTool

    state = {"failed": False}
    calls: list[str] = []

    def _wrapper(store, tools):  # type: ignore[no-untyped-def]
        real = {item.name: item for item in tools}

        def _flaky(files: dict[str, str]) -> str:
            calls.append("flaky")
            if not state["failed"]:
                state["failed"] = True
                return "写入失败：文件名不合法。请换名后重试"
            return str(real["write_file"].invoke({"files": files}))

        return [
            StructuredTool.from_function(func=_flaky, name="write_file", description="写入文件"),
            real["read_file"],
            real["list_files"],
        ]

    # 剧本：第一次写入被注入失败 → 模型重试（这次委托给真工具，写入成功）→ 收工
    model = ScriptedModel(
        [_usage(_write({"index.html": "A"}, "c1")), _usage(_write({"index.html": "A"}, "c2")), _done()]
    )

    result = web_agent.generate(PLAN_1, REQUIREMENT, model=model, tool_wrapper=_wrapper)

    # 两次都经过注入通道：第一次被拦下，第二次才委托给真工具落盘
    assert calls == ["flaky", "flaky"]
    assert state["failed"] is True, "注入只发生一次（后续必须真的能写进去）"
    assert result.gate_passed is True, "第二次委托给真工具后应当交付成功"
    assert result.files == {"index.html": "A"}
    assert any("写入失败" in str(message.content) for message in model.seen[1]), "错误要回到模型上下文"


def test_plan_difficulty_drives_default_budget() -> None:
    """默认预算来自计划难度：easy 只给 6 步，硬编码模型写第 7 步时就被拦下。"""
    easy_turns = [_usage(_write({"index.html": "A"}, f"c{index}")) for index in range(1, 20)]
    model = ScriptedModel(easy_turns, repeat_last=True)

    result = web_agent.generate(PLAN_1, REQUIREMENT, model=model)

    assert result.steps_used <= 6, "easy 档位不该超过 6 步"
