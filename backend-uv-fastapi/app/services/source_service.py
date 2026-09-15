# app/services/source_service.py —— 附件（内容源 / 风格源）的业务编排
#
# 职责：把「一次上传」编排起来 —— 校验 → 落盘 → 分配别名 → 解析 → 理解 → 落库。
# 不写 SQL（交 repository）、不写 prompt（交 agents）、不碰 HTTP 类型（交 api）。
#
# ⚠️ 本模块**只碰 PG**（附件与别名都在 Agent 域）；文件字节落在 uploads/ 目录。
#
# 三条刻意的取舍：
#
# 1. **解析/理解失败不返回 4xx/5xx**：文件已经安全落盘、别名已经分配，
#    失败的是"读懂它"。返回 `parse_status=failed` + 原因，让用户继续对话，
#    比抛 500 友好得多，也不会让一个坏文件毁掉整轮会话（§3.8.6 失败矩阵）。
#
# 2. **先入库再解析**（`parse_status=pending → parsing → success/failed`）：
#    进程若在解析中途被杀，库里留下的是 `parsing` 而不是永远 `pending` ——
#    前者能看出"死在解析里"，后者看起来像"从来没开始"，排查方向完全不同。
#
# 3. **别名冲突要重试**：`next_alias()` 与 `create()` 之间不是原子的，
#    同一会话并发上传两个附件可能算出同一个 `@docN`。
#    靠 DB 的 `UNIQUE(session_id, alias)` 兜底，冲突时重新分配（而不是 500）。

import logging
import uuid

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.common import ModelUsage
from app.agents.source import doc_digest_agent
from app.agents.state import RequirementDigest, digest_one_line
from app.models.agent import AgentSession, GenerationSource
from app.repositories.agent import AgentSessionRepository, GenerationSourceRepository
from app.schemas.agent_schemas import (
    AgentUsageOut,
    RequirementDigestOut,
    SourceOut,
    SourceUploadResponse,
    StyleSpecOut,
)
from app.utils.agent.alias import next_alias
from app.utils.agent.source_store import save_upload
from app.utils.doc import MAX_PARSE_BYTES, DocParseError, detect_source_type, parse_document

logger = logging.getLogger(__name__)


class SourceService:
    """附件上传与会话内附件查询。"""

    # 别名冲突时的重试次数：并发上传才会撞上，两三次足够；撞满说明有别的问题
    MAX_ALIAS_ATTEMPTS = 3

    @staticmethod
    def upload(
        db: Session,
        user_id: int,
        *,
        session_uuid: str,
        filename: str | None,
        mime: str | None,
        data: bytes,
    ) -> SourceUploadResponse:
        """上传一个附件：落盘 → 分配别名 → 解析 → 理解 → 落库。

        Args:
            db: PG 数据库会话。
            user_id: 当前登录用户 id。
            session_uuid: 归属会话标识（别名的作用域就是会话）。
            filename: 原始文件名（只用于展示与判断类型）。
            mime: 浏览器声明的 MIME（类型判断的兜底）。
            data: 文件字节。

        Returns:
            上传结果（含别名、角色、解析状态、理解结果与用量）。

        Raises:
            HTTPException: 会话不存在/不属于当前用户 → 404；
                内容为空、超限或类型不支持 → 400。
        """
        session = SourceService._require_session(db, user_id, session_uuid)

        if not data:
            raise HTTPException(status_code=400, detail="上传内容为空")
        if len(data) > MAX_PARSE_BYTES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"文件大小 {len(data) / 1024 / 1024:.1f} MB，"
                    f"超过上限 {MAX_PARSE_BYTES // 1024 // 1024} MB"
                ),
            )

        # 类型判断放在落盘之前：不支持的类型直接拒绝，不留下垃圾文件
        try:
            detect_source_type(filename or "", mime)
        except DocParseError as error:
            raise HTTPException(status_code=400, detail=error.reason) from error

        source_uuid = uuid.uuid4().hex
        relative_path, stored_path = save_upload(user_id, source_uuid, filename, data)
        source = SourceService._create_with_alias(db, session, user_id, source_uuid)

        source.display_name = filename
        source.mime = mime
        source.size_bytes = len(data)
        source.storage_path = relative_path
        source.parse_status = "parsing"
        GenerationSourceRepository.update(db, source)

        # 解析（无 LLM）。失败 → 明确记录原因，直接返回，不阻塞
        try:
            parsed = parse_document(
                stored_path, filename=filename or stored_path.name, mime=mime
            )
        except DocParseError as error:
            logger.info("附件 %s 解析失败：%s", source_uuid, error.reason)
            return SourceService._failed_response(db, source, error.reason)

        warnings = list(parsed.warnings)

        # 理解（有 LLM）。节点内部已做降级，这里再兜一层：
        # 这是 HTTP 入口，任何意外异常都不该变成 500（文件与别名都已经落库了）
        try:
            result = doc_digest_agent.digest(parsed, filename=filename or stored_path.name)
            digest = result.digest
            degraded = result.degraded
            warnings.extend(result.warnings)
            usage = result.usage
        except Exception as error:  # noqa: BLE001
            logger.exception("附件 %s 理解阶段异常，降级为正文片段", source_uuid)
            digest = doc_digest_agent.fallback_digest(parsed)
            degraded = True
            warnings.append(f"文档理解异常，已降级为正文片段（{type(error).__name__}）")
            usage = ModelUsage()

        source.digest = digest.model_dump()
        source.role = digest.role
        source.parse_status = "success"
        source.parse_error = None
        GenerationSourceRepository.update(db, source)

        return SourceService._build_response(
            source=source,
            digest=digest,
            parse_status="success",
            degraded=degraded,
            warnings=warnings,
            usage=usage,
        )

    @staticmethod
    def list_sources(db: Session, user_id: int, session_uuid: str) -> list[SourceOut]:
        """列出某个会话下的附件（旧 → 新）。

        前端需要它来渲染别名 chip：消息里只有 ``@doc1``，而"@doc1 是哪个文件"的映射在附件表里。

        Args:
            db: PG 数据库会话。
            user_id: 当前登录用户 id。
            session_uuid: 会话标识。

        Returns:
            附件列表。

        Raises:
            HTTPException: 会话不存在或不属于当前用户 → 404。
        """
        session = SourceService._require_session(db, user_id, session_uuid)
        rows = GenerationSourceRepository.list_by_session(db, session.id)
        return [
            SourceOut(
                source_uuid=row.source_uuid,
                alias=row.alias,
                display_name=row.display_name,
                role=row.role,  # type: ignore[arg-type]
                parse_status=row.parse_status,
                parse_error=row.parse_error,
                size_bytes=row.size_bytes,
                digest_summary=digest_one_line(row.digest),
            )
            for row in rows
        ]

    # ==================== 内部工具 ====================

    @staticmethod
    def _require_session(db: Session, user_id: int, session_uuid: str) -> AgentSession:
        """取会话并校验归属。

        Raises:
            HTTPException: 不存在或不属于当前用户（一律 404，不泄露"这个 uuid 存在"）。
        """
        session = AgentSessionRepository.get_by_uuid(db, session_uuid)
        if session is None or session.user_id != user_id:
            raise HTTPException(status_code=404, detail="会话不存在")
        return session

    @staticmethod
    def _create_with_alias(
        db: Session, session: AgentSession, user_id: int, source_uuid: str
    ) -> GenerationSource:
        """创建附件行并分配会话内别名（冲突则重试）。

        别名分配会**把已删除的附件也算作已占用**（见 `list_aliases` 的说明）——
        否则历史消息里的 ``@doc1`` 会指向另一个文件。

        Raises:
            HTTPException: 连续多次都撞上别名唯一约束 → 409。
        """
        for _attempt in range(SourceService.MAX_ALIAS_ATTEMPTS):
            occupied = GenerationSourceRepository.list_aliases(db, session.id)
            source = GenerationSource(
                source_uuid=source_uuid,
                user_id=user_id,
                session_id=session.id,
                alias=next_alias(occupied),
                role="content",  # 安全侧初值；理解完成后由 digest 的角色覆盖
                parse_status="pending",
            )
            try:
                return GenerationSourceRepository.create(db, source)
            except IntegrityError:
                # 并发下两个请求可能算到同一个 @docN：回滚后重新取已占用的别名
                db.rollback()
                logger.warning("会话 %s 的别名分配冲突，重试", session.session_uuid)
        raise HTTPException(status_code=409, detail="别名分配冲突，请重试")

    @staticmethod
    def _failed_response(
        db: Session, source: GenerationSource, reason: str
    ) -> SourceUploadResponse:
        """解析失败时的响应：明确原因，并把角色退回安全侧。"""
        source.parse_status = "failed"
        source.parse_error = reason
        source.role = "content"
        GenerationSourceRepository.update(db, source)
        return SourceService._build_response(
            source=source,
            digest=None,
            parse_status="failed",
            degraded=True,
            warnings=[f"文件解析失败：{reason}"],
            usage=None,
        )

    @staticmethod
    def _build_response(
        *,
        source: GenerationSource,
        digest: RequirementDigest | None,
        parse_status: str,
        degraded: bool,
        warnings: list[str],
        usage: ModelUsage | None,
    ) -> SourceUploadResponse:
        """把 ORM 行与理解结果拼成响应（响应字段是白名单，不直接回 ORM 对象）。"""
        return SourceUploadResponse(
            source_uuid=source.source_uuid,
            alias=source.alias,
            display_name=source.display_name,
            size_bytes=source.size_bytes or 0,
            role=source.role,  # type: ignore[arg-type]
            parse_status=parse_status,  # type: ignore[arg-type]
            parse_error=source.parse_error,
            digest=SourceService._digest_out(digest),
            degraded=degraded,
            warnings=warnings,
            usage=AgentUsageOut(
                input_tokens=getattr(usage, "input_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0),
                reasoning_tokens=getattr(usage, "reasoning_tokens", 0),
            ),
        )

    @staticmethod
    def _digest_out(digest: RequirementDigest | None) -> RequirementDigestOut | None:
        """把 digest 转成响应模型（从 dict 还原时也走这里）。"""
        if digest is None:
            return None
        data = digest.model_dump()
        style = data.get("style_spec") or {}
        return RequirementDigestOut(
            summary=data.get("summary", ""),
            role=data.get("role", "content"),
            content_points=list(data.get("content_points") or []),
            style_spec=StyleSpecOut(**style) if isinstance(style, dict) else StyleSpecOut(),
            constraints=list(data.get("constraints") or []),
            open_questions=list(data.get("open_questions") or []),
        )
