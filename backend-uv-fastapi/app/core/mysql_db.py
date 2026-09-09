from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from collections.abc import Generator

from app.core.mysql_config import mysql_settings

mysql_engine = create_engine(
    mysql_settings.database_url,
    pool_pre_ping=True,   # 在 sqlalchemy 操作，前先 ping，避免 MySQL wait_timeout 断开后报错
    pool_recycle=3600, # 
)

MysqlSessionLocal = sessionmaker(bind=mysql_engine, autocommit=False, autoflush=False)


class MysqlBase(DeclarativeBase):
    """所有 ORM 实体的基类"""


def get_mysql_db()-> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求一个会话，用完自动关闭"""
    db = MysqlSessionLocal()
    try:
        yield db
    finally:
        db.close()