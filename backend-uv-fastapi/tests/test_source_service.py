"""阶段 3 的离线测试（五）：附件上传的编排（SourceService）。

不连 PG、不调模型：仓储与 digest 节点都被替换成假对象，文件写到用例自己的临时目录。
覆盖的是**编排逻辑**与**失败矩阵**：

- 解析失败（扫描版 PDF / 编码读不了）**不能**变成 500 —— 文件已保存、别名已分配；
- 不支持的类型必须在**落盘之前**拒绝，不留垃圾文件、不留库里的孤儿行；
- 别名**不可复用**：附件被删除后，它的 ``@doc1`` 也不能再分配给别人；
- 角色否决要一路生效到 HTTP 响应（.md 说自己是风格源 → 最终仍是 content）。
"""

from pathlib import Path

import pytest
from fastapi import HTTPException
from langchain_core.runnables import RunnableLambda

import app.services.source_service as source_service
from app.agents.common import ModelUsage
from app.agents.source import doc_digest_agent
from app.agents.source.doc_digest_agent import DigestResult
from app.agents.state import RequirementDigest, StyleSpec
from app.core.storage_config import storage_settings
from app.models.agent import AgentSession, GenerationSource
from app.services.source_service import SourceService

HTML_TOKENS = {"colors": ["#0f172a"], "font_families": ["Inter"]}


# --------------------------------------------------------------------------
# 测试替身
# --------------------------------------------------------------------------


class FakeSessionRepo:
    """内存版会话仓储。"""

    store: dict[str, AgentSession] = {}

    @classmethod
    def reset(cls) -> None:
        cls.store = {}

    @classmethod
    def get_by_uuid(cls, db: object, session_uuid: str) -> AgentSession | None:
        return cls.store.get(session_uuid)


class FakeSourceRepo:
    """内存版附件仓储（模拟自增 id 与"逻辑删除仍占用别名"）。"""

    rows: list[GenerationSource] = []
    _next_id = 1

    @classmethod
    def reset(cls) -> None:
        cls.rows = []
        cls._next_id = 1

    @classmethod
    def create(cls, db: object, source: GenerationSource) -> GenerationSource:
        source.id = cls._next_id
        cls._next_id += 1
        # 真实库里 is_delete 的 server_default 是 0；内存替身要手动补上，
        # 否则 list_by_session 的 `is_delete == 0` 过滤会把刚建的行也滤掉
        source.is_delete = 0
        cls.rows.append(source)
        return source

    @classmethod
    def update(cls, db: object, source: GenerationSource) -> GenerationSource:
        return source

    @classmethod
    def list_aliases(cls, db: object, session_id: int) -> list[str]:
        # ⚠️ 刻意**不过滤** is_delete：别名不可复用是真实约定（见 list_aliases 文档）
        return [row.alias for row in cls.rows if row.session_id == session_id]

    @classmethod
    def list_by_session(cls, db: object, session_id: int) -> list[GenerationSource]:
        return [
            row
            for row in cls.rows
            if row.session_id == session_id and row.is_delete == 0
        ]


def _session(session_uuid: str = "sess1", user_id: int = 1) -> AgentSession:
    """造一个已入库的会话对象。"""
    session = AgentSession(session_uuid=session_uuid, user_id=user_id, status="active")
    session.id = 1
    return session


def _digest_result(
    *,
    role: str = "content",
    degraded: bool = False,
    warnings: list[str] | None = None,
    usage: ModelUsage | None = None,
) -> DigestResult:
    """造一个 digest 节点的返回值。"""
    return DigestResult(
        digest=RequirementDigest(
            summary="一份需求说明书",
            role=role,  # type: ignore[arg-type]
            content_points=["标题：我的待办"],
            constraints=["必须支持回车添加"],
            style_spec=StyleSpec.from_design_tokens(HTML_TOKENS, notes="深色底"),
        ),
        degraded=degraded,
        usage=usage or ModelUsage(input_tokens=100, output_tokens=20),
        warnings=warnings or [],
    )


@pytest.fixture
def uploads_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把上传根目录改到用例临时目录（避免污染真实 uploads/）。"""
    root = tmp_path / "uploads"
    monkeypatch.setattr(storage_settings, "uploads_dir", str(root))
    return root


@pytest.fixture(autouse=True)
def _fake_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    """所有用例统一换上内存仓储。"""
    FakeSessionRepo.reset()
    FakeSourceRepo.reset()
    FakeSessionRepo.store["sess1"] = _session()
    monkeypatch.setattr(source_service, "AgentSessionRepository", FakeSessionRepo)
    monkeypatch.setattr(source_service, "GenerationSourceRepository", FakeSourceRepo)


@pytest.fixture
def fake_digest(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """把 digest 节点换成假实现，并记录调用参数。"""
    calls: list[dict] = []

    def _digest(parsed: object, *, filename: str | None = None, **kwargs: object) -> DigestResult:
        calls.append({"parsed": parsed, "filename": filename})
        return _digest_result()

    monkeypatch.setattr(doc_digest_agent, "digest", _digest)
    return calls


# --------------------------------------------------------------------------
# 1. 成功路径
# --------------------------------------------------------------------------


def test_upload_success_returns_alias_and_digest(uploads_root: Path, fake_digest: list) -> None:
    """成功上传：分配 @doc1、解析成功、digest 落库、文件按字节落盘。"""
    data = "需求说明：做一个待办清单".encode("gb18030")

    response = SourceService.upload(
        db=object(), user_id=1, session_uuid="sess1", filename="需求说明.txt", mime="text/plain", data=data
    )

    assert response.alias == "@doc1"
    assert response.display_name == "需求说明.txt"
    assert response.parse_status == "success"
    assert response.parse_error is None
    assert response.role == "content"
    assert response.degraded is False
    assert response.size_bytes == len(data)
    assert response.digest is not None
    assert response.digest.content_points == ["标题：我的待办"]
    assert response.digest.style_spec.colors == ["#0f172a"]
    assert response.usage.input_tokens == 100

    row = FakeSourceRepo.rows[0]
    assert row.parse_status == "success"
    assert row.digest and row.digest["summary"] == "一份需求说明书"
    assert row.storage_path == f"1/{response.source_uuid}/source.txt"
    assert (uploads_root / row.storage_path).read_bytes() == data, "必须按原始字节保存"
    assert len(fake_digest) == 1


def test_upload_gbk_text_is_parsed_correctly(uploads_root: Path, fake_digest: list) -> None:
    """⚠️ GBK 的中文 .txt 一路走到底都不出错（解析层的编码回退真的接上了）。"""
    response = SourceService.upload(
        db=object(),
        user_id=1,
        session_uuid="sess1",
        filename="gbk.txt",
        mime="text/plain",
        data="中文需求正文".encode("gb18030"),
    )

    parsed = fake_digest[0]["parsed"]
    assert response.parse_status == "success"
    assert "中文需求正文" in parsed.text
    assert parsed.encoding == "gb18030"


def test_upload_html_design_tokens_reach_digest(uploads_root: Path, fake_digest: list) -> None:
    """HTML 的设计令牌要传到 digest 层（否则风格源无从谈起）。"""
    html = '<html><head><style>body{color:#0f172a;font-family:"Inter"}</style></head><body><p>规范</p></body></html>'

    SourceService.upload(
        db=object(), user_id=1, session_uuid="sess1", filename="spec.html", mime="text/html", data=html.encode()
    )

    parsed = fake_digest[0]["parsed"]
    assert parsed.source_type == "html"
    assert parsed.design_tokens["colors"] == ["#0f172a"]
    assert parsed.can_be_style_source() is True


def test_upload_second_file_gets_next_alias(uploads_root: Path, fake_digest: list) -> None:
    """同一会话的第二个附件必须是 @doc2（别名按会话递增）。"""
    first = SourceService.upload(
        db=object(), user_id=1, session_uuid="sess1", filename="a.txt", mime=None, data=b"a"
    )
    second = SourceService.upload(
        db=object(), user_id=1, session_uuid="sess1", filename="b.txt", mime=None, data=b"b"
    )

    assert (first.alias, second.alias) == ("@doc1", "@doc2")


def test_deleted_attachment_alias_is_not_reused(uploads_root: Path, fake_digest: list) -> None:
    """⚠️ 附件被逻辑删除后，它的别名也不能再分配 —— 否则历史消息会指向另一个文件。"""
    SourceService.upload(
        db=object(), user_id=1, session_uuid="sess1", filename="a.txt", mime=None, data=b"a"
    )
    FakeSourceRepo.rows[0].is_delete = 1

    second = SourceService.upload(
        db=object(), user_id=1, session_uuid="sess1", filename="b.txt", mime=None, data=b"b"
    )

    assert second.alias == "@doc2", "已删除附件的 @doc1 不能被复用"


def test_upload_marks_role_from_digest(uploads_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """角色最终写到附件行上（对话展开别名时读的就是它）。"""
    monkeypatch.setattr(
        doc_digest_agent, "digest", lambda parsed, **kwargs: _digest_result(role="style")
    )

    response = SourceService.upload(
        db=object(),
        user_id=1,
        session_uuid="sess1",
        filename="spec.html",
        mime="text/html",
        data=b'<html><head><style>body{color:#fff}</style></head><body>x</body></html>',
    )

    assert response.role == "style"
    assert FakeSourceRepo.rows[0].role == "style"


def test_role_veto_applies_end_to_end(uploads_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """⚠️ 用**真实** digest 节点 + 假模型链：.md 被判成风格源时，否决要一路体现在 HTTP 响应上。

    这里刻意不替换 `doc_digest_agent.digest`，只替换它内部那条链 ——
    否则测的就是"service 会不会照抄 digest 的结论"，而真正要守的是
    "**解析器能力**的否决确实生效了"。
    """
    fake_chain = RunnableLambda(
        lambda _payload: {
            "raw": None,
            "parsed": RequirementDigest(summary="需求说明书", role="style"),
        }
    )
    monkeypatch.setattr(doc_digest_agent, "_DIGEST", fake_chain)

    response = SourceService.upload(
        db=object(),
        user_id=1,
        session_uuid="sess1",
        filename="需求.md",
        mime="text/markdown",
        data="# 需求".encode(),
    )

    assert response.role == "content", "Markdown 没有设计令牌，不能当风格源"
    assert any("设计令牌" in item for item in response.warnings)
    assert FakeSourceRepo.rows[0].role == "content"


# --------------------------------------------------------------------------
# 2. 拒绝路径：类型 / 体积 / 空内容 / 会话
# --------------------------------------------------------------------------


def test_unsupported_type_is_rejected_before_writing(uploads_root: Path) -> None:
    """⚠️ 不支持的类型在**落盘之前**拒绝：不写文件、不入库（否则留下孤儿数据）。"""
    with pytest.raises(HTTPException) as excinfo:
        SourceService.upload(
            db=object(), user_id=1, session_uuid="sess1", filename="需求.docx", mime=None, data=b"x"
        )

    assert excinfo.value.status_code == 400
    assert ".pdf / .html / .htm / .md / .txt" in excinfo.value.detail
    assert FakeSourceRepo.rows == []
    assert not uploads_root.exists(), "拒绝的上传不该在磁盘上留下任何东西"


def test_empty_content_is_rejected(uploads_root: Path) -> None:
    """空内容 → 400。"""
    with pytest.raises(HTTPException) as excinfo:
        SourceService.upload(
            db=object(), user_id=1, session_uuid="sess1", filename="a.txt", mime=None, data=b""
        )

    assert excinfo.value.status_code == 400
    assert "为空" in excinfo.value.detail


def test_oversized_content_is_rejected(
    uploads_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """超过上限 → 400（且提示里带上实际大小）。"""
    monkeypatch.setattr(source_service, "MAX_PARSE_BYTES", 10)

    with pytest.raises(HTTPException) as excinfo:
        SourceService.upload(
            db=object(), user_id=1, session_uuid="sess1", filename="big.txt", mime=None, data=b"x" * 100
        )

    assert excinfo.value.status_code == 400
    assert "超过上限" in excinfo.value.detail


def test_unknown_session_is_404(uploads_root: Path) -> None:
    """会话不存在 → 404。"""
    with pytest.raises(HTTPException) as excinfo:
        SourceService.upload(
            db=object(), user_id=1, session_uuid="nope", filename="a.txt", mime=None, data=b"a"
        )

    assert excinfo.value.status_code == 404


def test_other_users_session_is_404(uploads_root: Path) -> None:
    """别人的会话同样报 404（不泄露"这个 uuid 存在"）。"""
    FakeSessionRepo.store["sess1"] = _session(user_id=999)

    with pytest.raises(HTTPException) as excinfo:
        SourceService.upload(
            db=object(), user_id=1, session_uuid="sess1", filename="a.txt", mime=None, data=b"a"
        )

    assert excinfo.value.status_code == 404


# --------------------------------------------------------------------------
# 3. 解析 / 理解失败：不 500、不静默
# --------------------------------------------------------------------------


def test_parse_failure_returns_failed_status_not_500(uploads_root: Path, fake_digest: list) -> None:
    """⚠️ 坏 PDF：返回 parse_status=failed + 原因，而不是抛异常；别名照样分配。"""
    response = SourceService.upload(
        db=object(),
        user_id=1,
        session_uuid="sess1",
        filename="scan.pdf",
        mime="application/pdf",
        data=b"this is not a real pdf",
    )

    assert response.parse_status == "failed"
    assert response.degraded is True
    assert response.digest is None
    assert response.role == "content"
    assert "无法作为 PDF 读取" in (response.parse_error or "")
    assert response.warnings, "失败原因要出现在 warnings 里，前后端都不该静默"
    assert response.alias == "@doc1", "文件仍归属会话，别名照常分配"
    assert fake_digest == [], "解析失败不应调用模型（省一次钱）"

    row = FakeSourceRepo.rows[0]
    assert row.parse_status == "failed"
    assert row.digest is None


def test_parse_failure_keeps_file_on_disk(uploads_root: Path, fake_digest: list) -> None:
    """解析失败也要把文件留下：用户可以重新解析（换解析器/修编码），不必重新上传。"""
    SourceService.upload(
        db=object(),
        user_id=1,
        session_uuid="sess1",
        filename="broken.txt",
        mime=None,
        data=b"\xff\xff\xff",
    )

    row = FakeSourceRepo.rows[0]
    assert (uploads_root / row.storage_path).is_file()


def test_digest_degradation_keeps_parse_success(
    uploads_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """理解降级但解析成功：parse_status 仍为 success，degraded 标记与警告照实返回。"""
    monkeypatch.setattr(
        doc_digest_agent,
        "digest",
        lambda parsed, **kwargs: _digest_result(
            degraded=True, warnings=["文档理解失败，已降级为仅保留正文片段（超时）"]
        ),
    )

    response = SourceService.upload(
        db=object(), user_id=1, session_uuid="sess1", filename="a.txt", mime=None, data=b"hello"
    )

    assert response.parse_status == "success", "文档本身读懂了，只是理解降级"
    assert response.degraded is True
    assert any("降级" in item for item in response.warnings)


def test_digest_exception_is_contained(
    uploads_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """⚠️ digest 抛出意外异常也不能变成 500（文件与别名都已落库）。"""

    def _boom(parsed: object, **kwargs: object) -> DigestResult:
        raise RuntimeError("意料之外的解析后处理异常")

    monkeypatch.setattr(doc_digest_agent, "digest", _boom)

    response = SourceService.upload(
        db=object(), user_id=1, session_uuid="sess1", filename="a.txt", mime=None, data=b"hello"
    )

    assert response.parse_status == "success"
    assert response.degraded is True
    assert response.digest is not None, "兜底 digest 仍要给出正文片段"
    assert any("理解异常" in item for item in response.warnings)


# --------------------------------------------------------------------------
# 4. 列表
# --------------------------------------------------------------------------


def test_list_sources_returns_alias_and_summary(uploads_root: Path, fake_digest: list) -> None:
    """列表要带上别名、状态与一行摘要（前端渲染 chip 用）。"""
    SourceService.upload(
        db=object(), user_id=1, session_uuid="sess1", filename="需求.md", mime=None, data="# 需求".encode()
    )

    items = SourceService.list_sources(db=object(), user_id=1, session_uuid="sess1")

    assert len(items) == 1
    assert items[0].alias == "@doc1"
    assert items[0].display_name == "需求.md"
    assert items[0].parse_status == "success"
    assert items[0].digest_summary and "内容要点" in items[0].digest_summary


def test_list_sources_skips_deleted(uploads_root: Path, fake_digest: list) -> None:
    """逻辑删除的附件不再出现在列表里。"""
    SourceService.upload(
        db=object(), user_id=1, session_uuid="sess1", filename="a.txt", mime=None, data=b"a"
    )
    FakeSourceRepo.rows[0].is_delete = 1

    assert SourceService.list_sources(db=object(), user_id=1, session_uuid="sess1") == []


def test_list_sources_on_unknown_session_is_404(uploads_root: Path) -> None:
    """查不存在的会话 → 404。"""
    with pytest.raises(HTTPException) as excinfo:
        SourceService.list_sources(db=object(), user_id=1, session_uuid="nope")

    assert excinfo.value.status_code == 404
