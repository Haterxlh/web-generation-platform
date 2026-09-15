# app/core/pg_db.py —— PostgreSQL（对话 / 知识库）的 engine / 会话 / 声明基类
#
# ⚠️ 本模块与 mysql_db.py 是**两套互相隔离的基础设施**，读代码时务必分清：
#   - mysql_engine + MysqlBase：业务库（user / generation_task）
#   - pg_engine    + PgBase   ：对话与知识库（agent_session / agent_message / generation_source）
#
# 最阴的坑（见 docs/agent_refactor_plan.md §5 硬约束 1）：
#   两个 declarative Base 若被混注册，`Base.metadata.create_all()` 会**在错误的库里建表，
#   而且不报错**。因此 `app/models/agent/*` 只 import PgBase，`app/models/*.py` 只 import MysqlBase，
#   并用测试断言两边 metadata 的表集合完全不相交。

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.pg_config import pg_settings

pg_engine = create_engine(
    pg_settings.database_url,
    pool_pre_ping=True,   # 取连接前先 ping，避免连接被 PG 端回收后报错
    pool_recycle=3600,
)

PgSessionLocal = sessionmaker(bind=pg_engine, autocommit=False, autoflush=False)


class PgBase(DeclarativeBase):
    """PG（对话 / 知识库）侧所有 ORM 实体的基类。

    ⚠️ 不要与 ``MysqlBase`` 混用：一个模型只能继承其中一个。
    """


def get_pg_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求一个 PG 会话，用完自动关闭。

    与 ``get_mysql_db()`` 同名同形，路由里按需注入：
    ``db: Session = Depends(get_pg_db)``。

    Yields:
        PG 数据库会话。
    """
    db = PgSessionLocal()
    try:
        yield db
    finally:
        db.close()
