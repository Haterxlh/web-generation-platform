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

# 创建所有"数据库中还不存在"的表（已存在的表不会动，可安全重复运行）
MysqlBase.metadata.create_all(bind=mysql_engine)
print('建表完成')

# 查看现有的表
with mysql_engine.connect() as c:
    rows = c.execute(text('SHOW TABLES')).fetchall()
    print('wgp_db 现有表:', [row[0] for row in rows])