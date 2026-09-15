"""阶段 2 的离线测试：对话编排（AgentChatService）。

全程不连 PG、不调模型：仓储与两个节点都被替换成假对象。
覆盖的是**编排逻辑**本身 —— 会话怎么建、消息怎么落、什么时候调 chat-agent、
槽位怎么跨轮累积、以及"纯咨询不产生生成任务"。
"""

from pathlib import Path

import pytest
from fastapi import HTTPException

import app.services.agent_chat_service as acs
from app.agents.common import ModelUsage
from app.agents.state import ChatResult, RequirementSlots, RouterDecision, RouterResult
from app.models.agent import AgentMessage, AgentSession
from app.schemas.agent_schemas import AgentChatRequest


# --------------------------------------------------------------------------
# 测试替身
# --------------------------------------------------------------------------


class FakeSessionRepo:
    """内存版会话仓储。"""

    store: dict[str, AgentSession] = {}
    updated: list[AgentSession] = []
    _next_id = 1

    @classmethod
    def reset(cls) -> None:
        cls.store = {}
        cls.updated = []
        cls._next_id = 1

    @classmethod
    def create(cls, db: object, session: AgentSession) -> AgentSession:
        session.id = cls._next_id
        cls._next_id += 1
        cls.store[session.session_uuid] = session
        return session

    @classmethod
    def update(cls, db: object, session: AgentSession) -> AgentSession:
        cls.updated.append(session)
        cls.store[session.session_uuid] = session
        return session

    @classmethod
    def get_by_uuid(cls, db: object, session_uuid: str) -> AgentSession | None:
        return cls.store.get(session_uuid)


class FakeMessageRepo:
    """内存版消息仓储（按插入顺序保存，模拟自增 id 的顺序）。"""

    messages: list[AgentMessage] = []
    _next_id = 1

    @classmethod
    def reset(cls) -> None:
        cls.messages = []
        cls._next_id = 1

    @classmethod
    def create(cls, db: object, message: AgentMessage) -> AgentMessage:
        message.id = cls._next_id
        cls._next_id += 1
        cls.messages.append(message)
        return message

    @classmethod
    def recent_by_session(
        cls, db: object, session_id: int, limit: int = 50
    ) -> list[AgentMessage]:
        rows = [m for m in cls.messages if m.session_id == session_id]
        return list(reversed(rows))[:limit]  # 新 → 旧（与真实仓储一致）

    @classmethod
    def list_by_session(cls, db: object, session_id: int, limit: int = 200) -> list[AgentMessage]:
        return [m for m in cls.messages if m.session_id == session_id][:limit]


class FakeRouter:
    """假 router 节点：记录调用参数，返回预设判定。"""

    calls: list[dict] = []
    result: RouterResult | None = None

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.result = None

    @classmethod
    def route(cls, message: str, **kwargs: object) -> RouterResult:
        cls.calls.append({"message": message, **kwargs})
        assert cls.result is not None, "用例必须先设置 FakeRouter.result"
        return cls.result


class FakeChatAgent:
    """假 chat-agent 节点。"""

    calls: list[dict] = []
    result = ChatResult(reply="你更想要单页展示，还是带表单的小工具？")

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.result = ChatResult(reply="你更想要单页展示，还是带表单的小工具？")

    @classmethod
    def reply(cls, message: str, **kwargs: object) -> ChatResult:
        cls.calls.append({"message": message, **kwargs})
        return cls.result


def _router_result(
    *,
    intent: str = "chat",
    readiness: str = "needs_clarification",
    slots: RequirementSlots | None = None,
    missing: list[str] | None = None,
    usage: ModelUsage | None = None,
    degraded: bool = False,
) -> RouterResult:
    """造一个 router 返回值，字段默认值贴合"还在澄清"的场景。"""
    return RouterResult(
        decision=RouterDecision(
            intent=intent,  # type: ignore[arg-type]
            readiness=readiness,  # type: ignore[arg-type]
            slots=slots or RequirementSlots(),
            missing_slots=missing if missing is not None else ["site_kind", "features"],
            ask_hint="问问他要做什么类型的页面",
            reason="信息不足",
        ),
        degraded=degraded,
        usage=usage or ModelUsage(input_tokens=100, output_tokens=20),
    )


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    """把服务层的四个外部依赖全部换成假的。"""
    FakeSessionRepo.reset()
    FakeMessageRepo.reset()
    FakeRouter.reset()
    FakeChatAgent.reset()
    FakeRouter.result = _router_result()

    monkeypatch.setattr(acs, "AgentSessionRepository", FakeSessionRepo)
    monkeypatch.setattr(acs, "AgentMessageRepository", FakeMessageRepo)
    monkeypatch.setattr(acs, "intent_router", FakeRouter)
    monkeypatch.setattr(acs, "chat_agent", FakeChatAgent)


DB = object()  # 假会话：仓储全被替换，服务层不会真的用它


def _chat(message: str = "帮我做个网站", session_uuid: str | None = None):
    """跑一轮对话。"""
    return acs.AgentChatService.chat(
        DB,  # type: ignore[arg-type]
        7,
        AgentChatRequest(session_uuid=session_uuid, message=message),
    )


# --------------------------------------------------------------------------
# 1. 会话与消息的落库
# --------------------------------------------------------------------------


def test_chat_creates_session_and_persists_both_messages() -> None:
    """第一轮要建会话，并落下用户消息与助手消息各一条。"""
    response = _chat("帮我做个网站")

    assert response.session_uuid in FakeSessionRepo.store
    assert len(FakeMessageRepo.messages) == 2
    assert [m.role for m in FakeMessageRepo.messages] == ["user", "assistant"]
    assert FakeMessageRepo.messages[0].content == "帮我做个网站"
    assert FakeMessageRepo.messages[1].content == response.reply


def test_session_title_from_first_message() -> None:
    """会话标题取首条用户消息前 30 字（够用，且不必额外调模型）。"""
    _chat("这是一个很长很长的需求描述" * 5)

    session = next(iter(FakeSessionRepo.store.values()))
    assert session.title is not None
    assert len(session.title) <= 30


def test_chat_reuses_existing_session() -> None:
    """传了 session_uuid 就复用同一会话，消息累积在它下面。"""
    first = _chat("你好")
    second = _chat("帮我做个待办清单", session_uuid=first.session_uuid)

    assert second.session_uuid == first.session_uuid
    assert len(FakeSessionRepo.store) == 1, "不该新建第二个会话"
    assert len(FakeMessageRepo.messages) == 4


def test_chat_rejects_foreign_session() -> None:
    """别人的会话一律 404（不泄露"这个 uuid 存在但不属于你"）。"""
    alien = AgentSession(session_uuid="alien", user_id=999, status="active")
    FakeSessionRepo.store["alien"] = alien

    with pytest.raises(HTTPException) as excinfo:
        _chat("你好", session_uuid="alien")

    assert excinfo.value.status_code == 404


def test_history_excludes_current_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """装配历史必须发生在落"本轮用户消息"**之前**，否则模型会看到两遍。"""
    first = _chat("第一句")
    _chat("第二句", session_uuid=first.session_uuid)

    # 第二轮 router 收到的 history 只应包含第一轮的两条消息
    second_call_history = FakeRouter.calls[-1]["history"]
    contents = [content for _role, content in second_call_history]

    assert contents == ["第一句", FakeChatAgent.result.reply]
    assert "第二句" not in contents


# --------------------------------------------------------------------------
# 2. 分支：什么时候调 chat-agent，什么时候不调
# --------------------------------------------------------------------------


def test_needs_clarification_calls_chat_agent() -> None:
    """信息不足 → 交给 chat-agent 说话。"""
    response = _chat("帮我做个网站")

    assert len(FakeChatAgent.calls) == 1
    assert response.reply == FakeChatAgent.result.reply
    assert response.ready_to_generate is False


def test_ready_generate_skips_chat_agent() -> None:
    """需求已明确 → **不调 chat-agent**。

    此时确认摘要的内容由槽位唯一确定，模型发挥不了额外价值，
    多一次调用只多一份延迟与不确定性。
    """
    FakeRouter.result = _router_result(
        intent="generate",
        readiness="ready",
        slots=RequirementSlots(site_kind="单页展示", features=["添加待办", "删除"]),
        missing=[],
    )

    response = _chat("做个待办清单，能添加和删除")

    assert FakeChatAgent.calls == [], "ready 时不该再调 chat-agent"
    assert response.ready_to_generate is True
    assert "需求已经清楚了" in response.reply
    assert "添加待办" in response.reply, "确认话术要复述槽位，让用户有机会纠正"


def test_chat_agent_receives_missing_slots_and_hint() -> None:
    """chat-agent 要拿到"缺什么"和"该问什么"，否则它只能自己瞎猜。"""
    FakeRouter.result = _router_result(missing=["style"], slots=RequirementSlots())

    _chat("帮我做个网站")

    call = FakeChatAgent.calls[0]
    assert call["missing_slots"] == ["style"]
    assert call["ask_hint"] == "问问他要做什么类型的页面"


# --------------------------------------------------------------------------
# 3. 槽位跨轮累积
# --------------------------------------------------------------------------


def test_slots_accumulate_across_turns() -> None:
    """第 1 轮给出 site_kind，第 2 轮补上 features —— 历史信息不能被清掉。"""
    FakeRouter.result = _router_result(slots=RequirementSlots(site_kind="单页展示"))
    first = _chat("做个单页展示的页面")

    FakeRouter.result = _router_result(slots=RequirementSlots(features=["添加待办"]))
    second = _chat("要能添加待办", session_uuid=first.session_uuid)

    assert second.slots.site_kind == "单页展示", "第 2 轮新抽取为空时不能覆盖第 1 轮的成果"
    assert second.slots.features == ["添加待办"]


def test_slots_persisted_on_session_draft() -> None:
    """需求草稿要落到会话行上，供跨轮复用。"""
    FakeRouter.result = _router_result(slots=RequirementSlots(site_kind="单页展示"))

    response = _chat("做个单页展示的页面")
    session = FakeSessionRepo.store[response.session_uuid]

    assert session.draft_requirement is not None
    assert session.draft_requirement["slots"]["site_kind"] == "单页展示"
    assert "单页展示" in session.draft_requirement["summary"]


def test_corrupt_draft_is_tolerated() -> None:
    """草稿数据损坏时退回空草稿，不能把整轮对话搞崩。"""
    FakeRouter.result = _router_result(slots=RequirementSlots(site_kind="单页展示"))
    response = _chat("你好")
    session = FakeSessionRepo.store[response.session_uuid]
    session.draft_requirement = {"slots": {"site_kind": 123, "features": "不是列表"}}

    # 不应抛异常
    again = _chat("继续", session_uuid=response.session_uuid)

    assert again.slots.site_kind == "单页展示"


# --------------------------------------------------------------------------
# 4. 用量、降级、以及"不碰生成域"
# --------------------------------------------------------------------------


def test_usage_accumulates_router_and_chat() -> None:
    """ready 之外的路径会调两次模型，用量必须累加。"""
    FakeRouter.result = _router_result(usage=ModelUsage(input_tokens=100, output_tokens=20, reasoning_tokens=5))
    FakeChatAgent.result = ChatResult(
        reply="好的", usage=ModelUsage(input_tokens=200, output_tokens=50, reasoning_tokens=10)
    )

    response = _chat("帮我做个网站")

    assert response.usage.input_tokens == 300
    assert response.usage.output_tokens == 70
    assert response.usage.reasoning_tokens == 15


def test_degraded_flag_propagates() -> None:
    """router 走了降级路径时，响应里要能看出来（排查用）。"""
    FakeRouter.result = _router_result(degraded=True)

    assert _chat("你好").degraded is True


def test_message_records_intent_and_memorable_flag() -> None:
    """助手消息要记下意图；只有携带需求信息的轮次才标记为"值得进向量库"。"""
    FakeRouter.result = _router_result(slots=RequirementSlots(site_kind="单页展示"))
    _chat("做个单页展示")

    assistant = FakeMessageRepo.messages[-1]
    assert assistant.intent == "chat"
    assert assistant.is_memorable == 1, "带了 site_kind，这一轮值得进向量库"


def test_pure_chitchat_is_not_memorable() -> None:
    """⚠️ 寒暄不该进向量库：否则"你好""谢谢"会把二期检索结果灌满。"""
    FakeRouter.result = _router_result(slots=RequirementSlots())

    _chat("你好")

    assert FakeMessageRepo.messages[-1].is_memorable == 0


def test_chat_service_never_touches_generation_domain() -> None:
    """架构护栏：对话服务**绝不能**碰 MySQL 侧的生成域。

    "纯咨询不产生任何生成任务"这条验收，在阶段 2 是靠"这个模块根本不认识
    GenerationTask / get_mysql_db"来保证的 —— 用源码断言把它钉住，
    以后谁顺手加一行 import 都会被这条用例拦住。
    """
    source = Path(acs.__file__).read_text(encoding="utf-8")

    assert "GenerationTask" not in source
    assert "get_mysql_db" not in source
    assert "generation_service" not in source


# --------------------------------------------------------------------------
# 5. 会话详情（历史回放）
# --------------------------------------------------------------------------


def test_get_session_returns_messages_in_chronological_order() -> None:
    """历史回放要"旧 → 新"，符合对话的自然顺序。"""
    first = _chat("第一句")
    _chat("第二句", session_uuid=first.session_uuid)

    detail = acs.AgentChatService.get_session(DB, 7, first.session_uuid)  # type: ignore[arg-type]

    assert [m.content for m in detail.messages] == [
        "第一句",
        FakeChatAgent.result.reply,
        "第二句",
        FakeChatAgent.result.reply,
    ]


def test_get_session_rejects_foreign_session() -> None:
    """会话详情同样只能查自己的。"""
    FakeSessionRepo.store["alien"] = AgentSession(session_uuid="alien", user_id=999, status="active")

    with pytest.raises(HTTPException) as excinfo:
        acs.AgentChatService.get_session(DB, 7, "alien")  # type: ignore[arg-type]

    assert excinfo.value.status_code == 404
