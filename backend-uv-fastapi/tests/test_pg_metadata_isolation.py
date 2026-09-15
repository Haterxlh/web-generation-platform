"""阶段 1 的离线测试：两个 declarative Base 的隔离（**最阴的坑**）。

见 `docs/agent_refactor_plan.md` §5 硬约束 1：
PgBase 与 MysqlBase 若被混注册，`Base.metadata.create_all()` 会**在错误的库里建表，
而且不报错** —— 你会在 MySQL 里看到 `agent_session`、在 PG 里看到 `generation_task`，
直到某条查询莫名失败才发现。

这些断言**不需要连任何数据库**：SQLAlchemy 的 metadata 在 import 时就装配好了，
所以它们能在 CI 里稳定拦截"顺手 import 错了 Base"这类改动。
"""

from sqlalchemy import UniqueConstraint

from app.core.mysql_db import MysqlBase
from app.core.pg_db import PgBase
from app.models.agent import AgentMessage, AgentSession, GenerationSource

# 显式 import 才会把模型注册进各自的 metadata（这也是必须"物理隔离"的另一面：
# 谁 import 了什么，决定了谁会被建到哪个库）
import app.models.generation_task  # noqa: F401
import app.models.user  # noqa: F401
from app.models.generation_task import GenerationTask
from app.models.user import User

AGENT_TABLES = {"agent_session", "agent_message", "generation_source"}
MYSQL_TABLES = {"user", "generation_task"}


# --------------------------------------------------------------------------
# 1. 两个 Base 必须完全隔离
# --------------------------------------------------------------------------


def test_metadata_objects_are_distinct() -> None:
    """两个 Base 用的必须是不同的 MetaData 对象。"""
    assert PgBase.metadata is not MysqlBase.metadata


def test_pg_base_owns_agent_tables() -> None:
    """PG 侧 Base 应持有三张对话/知识库表。"""
    assert AGENT_TABLES <= set(PgBase.metadata.tables)


def test_mysql_base_owns_business_tables() -> None:
    """MySQL 侧 Base 应持有业务表。"""
    assert MYSQL_TABLES <= set(MysqlBase.metadata.tables)


def test_no_table_registered_in_both_bases() -> None:
    """⚠️ 最关键的一条：两个 metadata 的表集合必须完全不相交。

    有交集就说明某个模型继承错了 Base —— 那正是"在错库建表且不报错"的成因。
    """
    overlap = set(PgBase.metadata.tables) & set(MysqlBase.metadata.tables)

    assert overlap == set(), f"这些表被两个 Base 同时注册了：{sorted(overlap)}"


def test_mysql_base_contains_no_agent_tables() -> None:
    """MySQL 侧绝不能出现对话/知识库表（否则 create_all 会把它们建进 MySQL）。"""
    leaked = AGENT_TABLES & set(MysqlBase.metadata.tables)

    assert leaked == set(), f"这些 PG 表泄漏进了 MysqlBase：{sorted(leaked)}"


def test_pg_base_contains_no_business_tables() -> None:
    """反过来也不行：PG 侧不能出现 MySQL 业务表。"""
    leaked = MYSQL_TABLES & set(PgBase.metadata.tables)

    assert leaked == set(), f"这些 MySQL 表泄漏进了 PgBase：{sorted(leaked)}"


def test_each_model_belongs_to_the_right_base() -> None:
    """逐个模型确认归属，比只数表名更直接。"""
    for model in (AgentSession, AgentMessage, GenerationSource):
        assert model.metadata is PgBase.metadata, f"{model.__name__} 不属于 PgBase"

    for model in (User, GenerationTask):
        assert model.metadata is MysqlBase.metadata, f"{model.__name__} 不属于 MysqlBase"


# --------------------------------------------------------------------------
# 2. 阶段 1 定下的表约定（防止后续被无意改掉）
# --------------------------------------------------------------------------


def test_agent_tables_use_snake_case_columns() -> None:
    """PG 侧统一 snake_case。

    出现大写列名就说明有人把 MySQL 的驼峰习惯带过来了 ——
    在 PG 里那会导致每次写原生 SQL 都得加双引号。
    """
    for table_name in AGENT_TABLES:
        table = PgBase.metadata.tables[table_name]
        assert all(col.name.islower() for col in table.columns), (
            f"{table_name} 存在非 snake_case 列名："
            f"{[c.name for c in table.columns if not c.name.islower()]}"
        )


def test_agent_time_columns_are_timezone_aware() -> None:
    """时间列必须是 timestamptz。

    naive timestamp 在 PG 里是个经典陷阱：写进去的 naive datetime 会被按**会话时区**解释，
    读出来又是另一个值，跨时区排查时非常难受。
    """
    for table_name in AGENT_TABLES:
        table = PgBase.metadata.tables[table_name]
        for column in table.columns:
            if column.name.endswith("_time"):
                assert column.type.timezone is True, (
                    f"{table_name}.{column.name} 不是 timestamptz"
                )


def test_all_agent_tables_support_logical_delete() -> None:
    """逻辑删除沿用项目统一约定（0/1），两个库的查询写法因此保持一致。"""
    for table_name in AGENT_TABLES:
        table = PgBase.metadata.tables[table_name]
        assert "is_delete" in table.columns, f"{table_name} 缺少 is_delete"


def test_generation_source_alias_is_scoped_to_session() -> None:
    """别名作用域是**会话**：唯一约束必须建在 (session_id, alias) 上。

    这个约束决定了"别名只在会话内唯一"，从而不必让别名全局唯一
    （那会带来分配与回收的复杂度）。见 docs/agent_refactor_plan.md §3.6。
    """
    table = PgBase.metadata.tables["generation_source"]
    unique_column_sets = {
        tuple(sorted(col.name for col in constraint.columns))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("alias", "session_id") in unique_column_sets, (
        f"未找到 (session_id, alias) 唯一约束，实际有：{sorted(unique_column_sets)}"
    )


def test_generation_source_has_no_vector_column_yet() -> None:
    """一期刻意**不建向量列**。

    华为云 BGE-M3 是 1024 维；过早把维度写死进表，二期换 embedding 模型就要迁移数据。
    一期只装 `vector` 扩展（零成本），向量表留到二期用迁移加。
    """
    table = PgBase.metadata.tables["generation_source"]
    column_types = {type(col.type).__name__ for col in table.columns}

    assert not any("VECTOR" in name.upper() for name in column_types), (
        f"出现了一期不该有的向量列类型：{sorted(column_types)}"
    )
