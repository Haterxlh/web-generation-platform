"""PG（对话 / 知识库）侧的 ORM 实体。

⚠️ 本包内的模型**只继承 `PgBase`**，绝不与 `MysqlBase` 混用。
`Base.metadata.create_all()` 只认自己 Base 下的表；混注册会让它在**错误的库里建表且不报错**
（见 `docs/agent_refactor_plan.md` §5 硬约束 1）。

命名：PG 侧统一 `snake_case`。PG 对大小写敏感，驼峰列名必须处处加双引号（`"userId"`），
在原生 SQL / psql / Alembic 迁移里都很别扭。MySQL 侧沿用既有的驼峰 —— 两库不 JOIN，
风格不一致不会产生歧义。

为什么这里要显式 import 每个模型：
Alembic 的 `env.py` 通过 import 本包来收集 `PgBase.metadata`；
**新增模型必须在这里 import 一次**，否则 autogenerate 看不见它。
"""

from app.models.agent.agent_message import AgentMessage
from app.models.agent.agent_session import AgentSession
from app.models.agent.generation_source import GenerationSource

__all__ = ["AgentMessage", "AgentSession", "GenerationSource"]
