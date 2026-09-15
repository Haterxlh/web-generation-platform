# app/alembic/env.py —— Alembic 运行环境（**只处理 PgBase**）
#
# ⚠️ 头号铁律（docs/agent_refactor_plan.md §5 硬约束 1）：
#    target_metadata 只能是 PgBase.metadata。
#    一旦把 MysqlBase.metadata 混进来，autogenerate 会试图把 MySQL 的表也建到 PG 里，
#    而且整个过程**不会报错**。

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# 双保险：alembic.ini 里已设 prepend_sys_path = .，这里再按文件位置兜一次，
# 保证无论从哪个目录调 alembic，`app` 都导入得到
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.pg_config import pg_settings  # noqa: E402
from app.core.pg_db import PgBase  # noqa: E402

# 关键：import 这个包，模型才会把自己注册进 PgBase.metadata。
# 少了这行 metadata 就是空的，autogenerate 会以为"什么表都不需要建"。
import app.models.agent  # noqa: E402,F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 连接串从 .env 来；刻意不用 config.set_main_option()，
# 因为 ConfigParser 会对 % 做插值，而 URL 转义后的密码可能含 %XX（例如 p@ss→p%40ss）。
# 直接把 URL 传给 create_engine / context.configure 最稳。
DATABASE_URL = pg_settings.database_url

target_metadata = PgBase.metadata


def run_migrations_offline() -> None:
    """离线模式：只渲染 SQL，不连库（``alembic upgrade head --sql``）。"""
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连库并执行迁移。"""
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
