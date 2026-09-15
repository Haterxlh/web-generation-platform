# app/agents/chat/chat_agent.py —— 澄清对话节点
#
# 职责（docs/agent_refactor_plan.md §3.2.1）：把"还缺什么"变成一句人话，
# 与用户多轮往复，直到需求足够明确。
#
# 刻意的职责划界：**chat-agent 不抽取槽位**（那是 intent_router 的活），它只负责"说话"。
# 好处是槽位抽取可以单独评测、话术质量可以单独迭代 —— 两个 prompt 各自可改、互不牵连。
#
# 模型选择：**非思考客户端**。对话对延迟敏感（每轮多等 10 秒体验很差），
# 而"写一段澄清问句"不需要深度推理。若话术质量不达标，切回 `llm_client` 是一行改动。

import logging
from collections.abc import Sequence

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable, RunnableLambda

from app.agents.common import ModelUsage
from app.agents.state import ChatResult, RequirementSlots
from app.core.llm_client import llm_no_thinking_client
from app.utils.agent.alias import AliasTarget, render_target
from app.utils.weg_gen.prompt_loader import load_prompt

logger = logging.getLogger(__name__)

PROMPT_NAME = "chat_agent_system"

# 调用失败时的兜底话术：宁可说一句"我没接住"，也不要给用户一个空白回复
FALLBACK_REPLY = "抱歉，我这边刚才没能正常回应。可以把你想要做的网页再说一遍吗？"

# 历史消息里的角色 → 提示词里的称呼
_ROLE_TEXT = {"user": "用户", "assistant": "你（上一轮）"}


def _build_messages(payload: dict) -> list[BaseMessage]:
    """把上下文拼成"system 约束 + user 上下文"两条消息。

    为什么不用 ChatPromptTemplate：提示词与历史里可能含大量花括号（HTML/CSS/JSON），
    模板引擎会把它们当变量解析而报错（这是 2026-09-12 已踩过的坑，见 prompt_loader.py）。

    Args:
        payload: 含 message / history / slots / missing_slots / ask_hint / attachments。

    Returns:
        两条消息组成的列表。
    """
    parts: list[str] = []

    history: Sequence[tuple[str, str]] = payload.get("history") or ()
    if history:
        lines = [
            f"{_ROLE_TEXT.get(role, role)}：{content}" for role, content in history
        ]
        parts.append("## 最近的对话\n" + "\n".join(lines))

    attachments: Sequence[AliasTarget] = payload.get("attachments") or ()
    if attachments:
        parts.append(
            "## 本轮附件\n" + "\n".join(render_target(item) for item in attachments)
        )

    slots: RequirementSlots | None = payload.get("slots")
    if slots is not None and (slots.site_kind or slots.features or slots.style or slots.audience):
        parts.append("## 当前已确认的需求槽位\n" + slots.model_dump_json())

    missing: Sequence[str] = payload.get("missing_slots") or ()
    if missing:
        parts.append("## 本轮还缺的信息（别重复问已有的）\n" + "、".join(missing))

    hint = payload.get("ask_hint") or ""
    if hint:
        parts.append(f"## 建议追问方向（来自上游判定，参考即可）\n{hint}")

    # 最新一句话放最后：离输出最近的指令权重最高
    parts.append("## 用户最新一句话\n" + payload["message"])
    return [
        SystemMessage(content=load_prompt(PROMPT_NAME)),
        HumanMessage(content="\n\n".join(parts)),
    ]


def build_chat_chain(model: Runnable | None = None) -> Runnable:
    """构造 chat-agent 链。

    Args:
        model: 可注入的模型（测试用；默认用全局 `llm_no_thinking_client`）。

    Returns:
        LCEL 链：输入 payload（dict）→ 输出 AIMessage。
    """
    return RunnableLambda(_build_messages) | (model or llm_no_thinking_client)


# 模块级只建一次（链本身无状态，可反复 invoke）
_CHAIN = build_chat_chain()


def reply(
    message: str,
    *,
    history: Sequence[tuple[str, str]] = (),
    slots: RequirementSlots | None = None,
    missing_slots: Sequence[str] = (),
    ask_hint: str = "",
    attachments: Sequence[AliasTarget] = (),
    chain: Runnable | None = None,
) -> ChatResult:
    """生成给用户看的回复。

    Args:
        message: 用户最新一句话。
        history: 最近对话。
        slots: 当前已确认的槽位。
        missing_slots: 还缺的槽位（用于告诉模型"别重复问已有的"）。
        ask_hint: 上游给出的追问方向。
        attachments: 本轮附件。
        chain: 可注入的链（测试用）。

    Returns:
        `ChatResult`（回复文本 + 是否降级 + token 用量）。
    """
    chat_chain = chain or _CHAIN
    payload = {
        "message": message,
        "history": history,
        "slots": slots,
        "missing_slots": missing_slots,
        "ask_hint": ask_hint,
        "attachments": attachments,
    }

    try:
        ai = chat_chain.invoke(payload)
    except Exception as error:  # noqa: BLE001 —— 回复失败要给用户一句人话，而不是 500
        logger.warning("chat-agent 调用失败，返回兜底话术：%s", error)
        return ChatResult(reply=FALLBACK_REPLY, degraded=True)

    usage = ModelUsage.from_message(ai)
    text = (getattr(ai, "text", "") or "").strip()
    if not text:
        # 空回复比报错更让人困惑：明确兜底
        return ChatResult(reply=FALLBACK_REPLY, degraded=True, usage=usage)
    return ChatResult(reply=text, usage=usage)
