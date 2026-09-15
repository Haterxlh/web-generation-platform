# app/repositories/agent/source_repository.py —— 内容源 / 风格源表的数据库操作（PG 侧）
#
# 约定与其它 repository 一致：只做 CRUD，查询一律过滤 is_delete == 0。
# ⚠️ 唯一的例外是 list_aliases()：它**必须包含已删除的行** —— 见该方法自己的说明。

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent import GenerationSource


class GenerationSourceRepository:
    """把 generation_source 表的所有数据库操作集中在这里，方法名即语义。"""

    @staticmethod
    def create(db: Session, source: GenerationSource) -> GenerationSource:
        """新增内容源并返回。

        Args:
            db: PG 数据库会话。
            source: 尚未入库的对象。

        Returns:
            入库后的对象。

        Raises:
            ValueError: 传入的对象已经入库。
        """
        if source.id is not None:
            raise ValueError("create() 只接受尚未入库的对象；更新请用 update()")
        db.add(source)
        db.commit()
        db.refresh(source)
        return source

    @staticmethod
    def update(db: Session, source: GenerationSource) -> GenerationSource:
        """提交对内容源的修改（解析状态 / digest / 角色都会走这里）。

        Args:
            db: PG 数据库会话。
            source: 已修改的对象。

        Returns:
            更新后的对象。

        Raises:
            ValueError: 传入的对象尚未入库。
        """
        if source.id is None:
            raise ValueError("update() 只接受已入库的对象；新增请用 create()")
        db.add(source)
        db.commit()
        db.refresh(source)
        return source

    @staticmethod
    def get_by_uuid(db: Session, source_uuid: str) -> GenerationSource | None:
        """按 source_uuid 查（未删除）。

        Args:
            db: PG 数据库会话。
            source_uuid: 附件唯一标识。

        Returns:
            内容源对象；不存在则 None。
        """
        stmt = select(GenerationSource).where(
            GenerationSource.source_uuid == source_uuid,
            GenerationSource.is_delete == 0,
        )
        return db.scalar(stmt)

    @staticmethod
    def list_by_session(db: Session, session_id: int) -> list[GenerationSource]:
        """取某会话下的全部附件（旧 → 新）。

        Args:
            db: PG 数据库会话。
            session_id: 会话 id。

        Returns:
            附件列表（按 id 升序）。
        """
        stmt = (
            select(GenerationSource)
            .where(
                GenerationSource.session_id == session_id,
                GenerationSource.is_delete == 0,
            )
            .order_by(GenerationSource.id.asc())
        )
        return list(db.scalars(stmt))

    @staticmethod
    def list_aliases(db: Session, session_id: int) -> list[str]:
        """取某会话**已经占用过**的全部别名（字符串列表）。

        ⚠️ 这里**刻意不过滤 is_delete**：别名不可重命名、**不可复用**
        （见 `app/utils/agent/alias.py` 的 `next_alias`）。
        若把已删除附件的别名排除在外，它就会被分配给新附件，
        于是历史消息里那个 `@doc1` 会指向另一个文件 —— 这是静默的数据错乱，
        比"序号用光"严重得多。

        Args:
            db: PG 数据库会话。
            session_id: 会话 id。

        Returns:
            别名列表（可能含已删除附件的别名）。
        """
        stmt = select(GenerationSource.alias).where(
            GenerationSource.session_id == session_id
        )
        return [alias for alias in db.scalars(stmt) if alias]
