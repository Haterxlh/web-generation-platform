# app/utils/db/create_all_table.py —— 开发期建表脚本
# 运行：uv run python -m app.utils.db.create_all_table （在 backend-uv-fastapi 目录下）

from sqlalchemy import text
from app.core.mysql_db import MysqlBase, mysql_engine

# 关键！先导入所有模型，模型才会注册到 MysqlBase.metadata，
"""
为什么加 import app.models.user：
模型文件被 import 的那一刻，class User(MysqlBase) 才会把自己登记进 MysqlBase.metadata。
没有这行，metadata 就是空的，create_all 无事可做
"""
import app.models.user  # noqa: F401  （noqa 表示"这行暂时没用变量，别报警告"）
import app.models.generation_task  # noqa: F401  ← 新增：注册生成任务模型

# ⚠️ 绝对不要在这里 import app.models.agent.*
# 那些模型继承的是 PgBase（PostgreSQL 侧），一旦被 import 进来，
# 下面的 create_all 会**把对话/知识库表建到 MySQL 里，而且不报错**。
# PG 侧的表由 Alembic 管理：`uv run alembic upgrade head`。
# 详见 docs/agent_refactor_plan.md §5 硬约束 1。

# 创建所有"数据库中还不存在"的表（已存在的表不会动，可安全重复运行）
MysqlBase.metadata.create_all(bind=mysql_engine)
print('建表完成')

# 查看现有的表
with mysql_engine.connect() as c:
    rows = c.execute(text('SHOW TABLES')).fetchall()
    print('wgp_db 现有表:', [row[0] for row in rows])