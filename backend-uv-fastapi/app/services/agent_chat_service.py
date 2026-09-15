# app/services/agent_chat_service.py —— 对话编排（业务层）
#
# 职责：把「一轮对话」编排起来 —— 建/取会话 → 装配上下文 → router → chat-agent → 落库。
# 不写 SQL（交 repository）、不写 prompt（交 agents）、不碰 HTTP（交 api）。
#
# ⚠️ 本模块**只碰 PG**（会话与消息都在 Agent 域），不碰 MySQL。
# 生成任务的创建要等阶段 6 的编排图，这里只负责告诉调用方"能不能开始生成"。

import logging
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.chat import chat_agent
from app.agents.router import intent_router
from app.agents.state import RequirementDraft, RequirementSlots, digest_one_line
from app.models.agent import AgentMessage, AgentSession, GenerationSource
from app.repositories.agent import AgentMessageRepository, AgentSessionRepository
from app.schemas.agent_schemas import (
    AgentChatRequest,
    AgentChatResponse,
    AgentMessageOut,
    AgentSessionDetailResponse,
    AgentUsageOut,
    RequirementSlotsOut,
)
from app.utils.agent.alias import AliasTarget, expand_aliases

logger = logging.getLogger(__name__)


class AgentChatService:
    """对话相关的业务操作集合。"""

    # 上下文预算：从最近往前累加，超了就停（不用固定条数 —— 长消息会直接撑爆上下文）
    HISTORY_MAX_MESSAGES = 50
    HISTORY_CHAR_BUDGET = 6000

    @staticmethod
    def chat(db: Session, user_id: int, req: AgentChatRequest) -> AgentChatResponse:
        """处理一轮对话。

        流程：取/建会话 → 装配上下文 → 意图路由 →（澄清对话 或 已可生成）→ 落库。

        Args:
            db: PG 数据库会话。
            user_id: 当前登录用户 id。
            req: 本轮请求。

        Returns:
            一轮对话的结果（回复 + 槽位 + 是否可生成 + 用量）。
        """
        # 1) 取或建会话
        session = AgentChatService._get_or_create_session(db, user_id, req)

        # 2) 装配上下文。⚠️ 必须在落"本轮用户消息"**之前**取历史，否则会把自己也算进去。
        targets = AgentChatService._load_attachment_targets(db, user_id, session, req)
        history = AgentChatService._build_history(db, session, targets)

        # 3) 落用户消息：**先落库再调模型** —— 模型挂了也不能丢用户说的话
        user_message = AgentMessage(
            message_uuid=uuid.uuid4().hex,
            session_id=session.id,
            role="user",
            content=req.message,
            attachments=[
                {"alias": t.alias, "source_uuid": t.source_uuid, "role": t.role}
                for t in targets
            ]
            or None,
        )
        AgentMessageRepository.create(db, user_message)

        # 4) 意图路由（判定权归模型，否决权归 Python —— 见 intent_router.apply_readiness_gate）
        draft = AgentChatService._load_draft(session)
        router_result = intent_router.route(
            req.message,
            history=history,
            draft_slots=draft.slots,
            attachments=list(targets),
        )
        decision = router_result.decision

        # 5) 槽位跨轮累积：本轮抽到的新信息合并进旧草稿
        merged_slots = draft.slots.merged_with(decision.slots)

        # 6) 分支：
        #    - generate + ready → 不调 chat-agent。此时"确认摘要"的内容由槽位唯一确定，
        #      不需要模型发挥，省一次调用（也省一次延迟）。
        #    - 其余情况（chat，或 generate 但信息不足）→ 交给 chat-agent 说话。
        ready_to_generate = decision.intent == "generate" and decision.readiness == "ready"
        usage = router_result.usage
        degraded = router_result.degraded

        if ready_to_generate:
            reply_text = AgentChatService._confirmation_reply(merged_slots)
        else:
            chat_result = chat_agent.reply(
                req.message,
                history=history,
                slots=merged_slots,
                missing_slots=decision.missing_slots,
                ask_hint=decision.ask_hint,
                attachments=list(targets),
            )
            reply_text = chat_result.reply
            usage = usage + chat_result.usage
            degraded = degraded or chat_result.degraded

        # 7) 落助手消息 + 更新会话上的需求草稿
        assistant_message = AgentMessage(
            message_uuid=uuid.uuid4().hex,
            session_id=session.id,
            role="assistant",
            content=reply_text,
            intent=decision.intent,
            slots=merged_slots.model_dump(),
            # 是否值得进向量库（二期 RAG 用）：只有"携带需求信息"的轮次才值得，
            # 否则"你好""谢谢"会把检索结果灌满（§5 硬约束 13）
            is_memorable=1 if (merged_slots.features or merged_slots.site_kind) else 0,
        )
        AgentMessageRepository.create(db, assistant_message)

        updated_draft = RequirementDraft(
            slots=merged_slots,
            summary=AgentChatService._summarize(merged_slots),
        )
        session.draft_requirement = updated_draft.model_dump()
        AgentSessionRepository.update(db, session)

        return AgentChatResponse(
            session_uuid=session.session_uuid,
            message_uuid=assistant_message.message_uuid,
            reply=reply_text,
            intent=decision.intent,
            readiness=decision.readiness,
            slots=RequirementSlotsOut(**merged_slots.model_dump()),
            missing_slots=decision.missing_slots,
            ready_to_generate=ready_to_generate,
            degraded=degraded,
            usage=AgentUsageOut(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
            ),
        )

    @staticmethod
    def get_session(db: Session, user_id: int, session_uuid: str) -> AgentSessionDetailResponse:
        """查会话详情（只能查自己的会话）。

        Args:
            db: PG 数据库会话。
            user_id: 当前登录用户 id。
            session_uuid: 会话唯一标识。

        Returns:
            会话详情（含消息列表）。

        Raises:
            HTTPException: 会话不存在或不属于当前用户，一律 404。
        """
        session = AgentSessionRepository.get_by_uuid(db, session_uuid)
        # "不存在"和"不是你的"故意返回同一个结果：不泄露"这个 uuid 存在但不属于你"
        if session is None or session.user_id != user_id:
            raise HTTPException(status_code=404, detail="会话不存在")

        draft = AgentChatService._load_draft(session)
        messages = AgentMessageRepository.list_by_session(db, session.id)
        return AgentSessionDetailResponse(
            session_uuid=session.session_uuid,
            title=session.title,
            status=session.status,
            slots=RequirementSlotsOut(**draft.slots.model_dump()),
            summary=draft.summary,
            messages=[AgentMessageOut.model_validate(item) for item in messages],
        )

    # ==================== 内部工具 ====================

    @staticmethod
    def _get_or_create_session(
        db: Session, user_id: int, req: AgentChatRequest
    ) -> AgentSession:
        """取已有会话，或新建一个。

        Raises:
            HTTPException: 传入的 session_uuid 不存在或不属于当前用户 → 404。
        """
        if req.session_uuid:
            session = AgentSessionRepository.get_by_uuid(db, req.session_uuid)
            if session is None or session.user_id != user_id:
                raise HTTPException(status_code=404, detail="会话不存在")
            return session

        session = AgentSession(
            session_uuid=uuid.uuid4().hex,
            user_id=user_id,
            # 标题取首条用户消息的前 30 字：够用且不必额外调模型
            title=req.message.strip()[:30] or None,
            status="active",
        )
        return AgentSessionRepository.create(db, session)

    @staticmethod
    def _load_draft(session: AgentSession) -> RequirementDraft:
        """读会话上的需求草稿；数据不合法时退回空草稿而不是报错。"""
        if not session.draft_requirement:
            return RequirementDraft()
        try:
            return RequirementDraft.model_validate(session.draft_requirement)
        except ValidationError as error:
            logger.warning("会话 %s 的需求草稿无法解析，按空草稿处理：%s", session.session_uuid, error)
            return RequirementDraft()

    @staticmethod
    def _load_attachment_targets(
        db: Session, user_id: int, session: AgentSession, req: AgentChatRequest
    ) -> list[AliasTarget]:
        """把请求里的附件引用解析成"别名目标"。

        阶段 2 还没有上传接口，所以这里通常拿不到东西；但逻辑先写好，
        阶段 3 只需让上传接口把行写进 generation_source，这里就自动生效了。

        Args:
            db: PG 数据库会话。
            user_id: 当前登录用户 id。
            session: 当前会话。
            req: 本轮请求。

        Returns:
            解析到的别名目标列表（查不到的引用被忽略 —— 阶段 3 会在上传接口就拦住非法引用）。
        """
        if not req.attachments:
            return []

        # 只要 uuid 在集合里就说明"这个附件确实属于本会话"；
        # 角色一律以**库里那一行**为准（它已经过 Python 侧的能力否决），
        # 不用请求里带的 role —— 否则前端可以绕过否决，把 .md 说成风格源
        wanted = {item.source_uuid for item in req.attachments}
        stmt = select(GenerationSource).where(
            GenerationSource.user_id == user_id,
            GenerationSource.session_id == session.id,
            GenerationSource.source_uuid.in_(list(wanted)),
            GenerationSource.is_delete == 0,
        )
        targets: list[AliasTarget] = []
        for source in db.scalars(stmt):
            targets.append(
                AliasTarget(
                    alias=source.alias,
                    source_uuid=source.source_uuid,
                    display_name=source.display_name,
                    role=source.role,
                    digest=digest_one_line(source.digest),
                    available=True,
                )
            )
        return targets

    @staticmethod
    def _build_history(
        db: Session, session: AgentSession, targets: list[AliasTarget]
    ) -> list[tuple[str, str]]:
        """装配历史上下文（旧 → 新），按字符预算从最近往前累加。

        为什么要展开别名：历史消息里存的是 ``@doc1`` 这样的**别名原文**，
        直接喂给模型它不知道那是什么；必须展开成"这是个什么文件、里面有什么要点"。

        Args:
            db: PG 数据库会话。
            session: 当前会话。
            targets: 本会话的别名目标（用于展开）。

        Returns:
            (role, content) 列表，旧 → 新。
        """
        rows = AgentMessageRepository.recent_by_session(
            db, session.id, limit=AgentChatService.HISTORY_MAX_MESSAGES
        )
        alias_map = {item.alias: item for item in targets}

        picked: list[tuple[str, str]] = []
        used = 0
        # rows 是"新 → 旧"：从最近往前累加，超预算就停
        for row in rows:
            text = expand_aliases(row.content, alias_map)
            if picked and used + len(text) > AgentChatService.HISTORY_CHAR_BUDGET:
                break
            picked.append((row.role, text))
            used += len(text)

        picked.reverse()  # 变回"旧 → 新"，符合对话的自然顺序
        return picked

    @staticmethod
    def _summarize(slots: RequirementSlots) -> str:
        """把槽位拼成一句话的需求摘要（草稿的 summary 字段用它）。

        Returns:
            形如 ``"类型是单页展示；功能包含添加、删除；风格是极简白底"``；槽位全空时返回空串。
        """
        bits: list[str] = []
        if slots.site_kind:
            bits.append(f"类型是{slots.site_kind}")
        if slots.features:
            bits.append("功能包含" + "、".join(slots.features))
        if slots.audience:
            bits.append(f"面向{slots.audience}")
        if slots.style:
            bits.append(f"风格是{slots.style}")
        if slots.need_persistence is not None:
            bits.append("数据" + ("需要存下来" if slots.need_persistence else "不需要存储"))
        return "；".join(bits)

    @staticmethod
    def _confirmation_reply(slots: RequirementSlots) -> str:
        """用槽位拼出"需求已明确"的确认话术。

        刻意**不调模型**：此时确认摘要的内容由槽位唯一确定，
        模型发挥不了额外价值，反而多一次调用、多一份不确定性。
        """
        detail = AgentChatService._summarize(slots) or "按你的描述来"
        return f"需求已经清楚了 —— {detail}。可以开始生成了；如果还想调整，直接告诉我就行。"
