# app/utils/utils_check/check_pg.py —— 开发期自检：双数据源是否真的隔离、PG 是否就绪
#
# 运行（在 backend-uv-fastapi 下，必须用 -m 让 app 包可导入）：
#   uv run python -m app.utils.utils_check.check_pg
#
# 为什么这个脚本必须存在（而不是只写 pytest）：
#   "两个 Base 不会串库" 只能靠**真实连库**证明 ——
#   metadata 层面的断言（tests/test_pg_metadata_isolation.py）能拦住"继承错 Base"，
#   但拦不住"模型写对了、engine 用错了"这种运行时错误。
#   本脚本第 4 项就是那条验收：拿 MySQL 会话去查 PG 的表，**必须报错**。

import sys

from sqlalchemy import select, text

from app.core.mysql_db import MysqlBase, mysql_engine
from app.core.pg_config import pg_settings
from app.core.pg_db import PgBase, pg_engine
from app.models.agent import AgentSession, GenerationSource

# 让 MySQL 侧的业务表也注册进来（用于反向检查）
import app.models.generation_task  # noqa: F401
import app.models.user  # noqa: F401
from app.models.generation_task import GenerationTask

AGENT_TABLES = ("agent_message", "agent_session", "generation_source")


def check_pg_connection() -> bool:
    """1) PG 连得上吗，扩展装了吗，迁移到哪个版本了。"""
    print("=" * 70)
    print("1) PostgreSQL 连通性与迁移状态")
    try:
        with pg_engine.connect() as conn:
            print(f"   地址     -> {pg_settings.pg_host}:{pg_settings.pg_port}"
                  f"/{pg_settings.postgres_db} (user={pg_settings.postgres_user})")
            print(f"   版本     -> {conn.execute(text('SHOW server_version')).scalar()}")

            extensions = conn.execute(
                text("SELECT extname, extversion FROM pg_extension WHERE extname = 'vector'")
            ).fetchall()
            if extensions:
                print(f"   vector   -> 已安装 {extensions[0][1]}")
            else:
                print("   vector   -> ❌ 未安装（阶段 1 应已由迁移 CREATE EXTENSION）")

            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            }
            missing = [name for name in AGENT_TABLES if name not in tables]
            print(f"   数据表   -> {sorted(tables)}")

            try:
                revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
                print(f"   alembic  -> {revision}")
            except Exception:  # noqa: BLE001 —— 表不存在就是"没跑过迁移"
                revision = None
                print("   alembic  -> ❌ 没有 alembic_version 表（迁移没跑过）")

        ok = bool(extensions) and not missing and revision is not None
        print(f"   结论     -> {'通过' if ok else '不通过'}")
        return ok
    except Exception as error:  # noqa: BLE001 —— 自检脚本要把失败原因原样打出来
        print(f"   失败     -> {type(error).__name__}: {error}")
        print("   处理     -> 确认容器在跑：docker compose up -d pg")
        return False


def check_mysql_still_works() -> bool:
    """2) 加了 PG 之后，MySQL 这条老链路不能被影响。"""
    print("=" * 70)
    print("2) MySQL 既有链路")
    try:
        with mysql_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            tables = {row[0] for row in conn.execute(text("SHOW TABLES"))}
        print(f"   数据表   -> {sorted(tables)}")
        missing = {"user", "generation_task"} - tables
        ok = not missing
        print(f"   结论     -> {'通过' if ok else f'不通过（缺 {sorted(missing)}）'}")
        return ok
    except Exception as error:  # noqa: BLE001
        print(f"   失败     -> {type(error).__name__}: {error}")
        return False


def check_two_bases_isolated() -> bool:
    """3) 两边 metadata 的表集合不能有交集。"""
    print("=" * 70)
    print("3) 两个 declarative Base 的隔离（静态）")
    pg_tables = set(PgBase.metadata.tables)
    my_tables = set(MysqlBase.metadata.tables)
    overlap = pg_tables & my_tables

    print(f"   PgBase   -> {sorted(pg_tables)}")
    print(f"   MysqlBase-> {sorted(my_tables)}")
    print(f"   交集     -> {sorted(overlap) if overlap else '（空）'}")
    ok = not overlap
    print(f"   结论     -> {'通过' if ok else '不通过：有表被两个 Base 同时注册'}")
    return ok


def check_wrong_database_queries_fail() -> bool:
    """4) ⚠️ 核心验收：拿 MySQL 会话去查 PG 的表，**必须报错**。

    这一条证明的不是"代码写对了"，而是"运行时确实没有串库"：
    模型继承对了 Base，但 engine 若传错，事情照样会错。
    """
    print("=" * 70)
    print("4) 跨库查询必须失败（证明没有串库）")

    ok = True

    # 4.1 MySQL 会话查 PG 的表
    try:
        with mysql_engine.connect() as conn:
            conn.execute(select(AgentSession).limit(1))
        print("   MySQL 查 agent_session -> ❌ 竟然成功了，说明串库了！")
        ok = False
    except Exception as error:  # noqa: BLE001 —— 预期就是失败
        print(f"   MySQL 查 agent_session -> ✅ 按预期失败（{type(error).__name__}）")

    # 4.2 PG 会话查 MySQL 的业务表（驼峰列名在 PG 里会被折叠成小写，必失败）
    try:
        with pg_engine.connect() as conn:
            conn.execute(select(GenerationTask).limit(1))
        print("   PG 查 generation_task  -> ❌ 竟然成功了，说明串库了！")
        ok = False
    except Exception as error:  # noqa: BLE001
        print(f"   PG 查 generation_task  -> ✅ 按预期失败（{type(error).__name__}）")

    # 4.3 正确方向的查询反而应当成功（否则上面的"失败"可能只是因为表不存在）
    try:
        with pg_engine.connect() as conn:
            conn.execute(select(GenerationSource).limit(1))
        print("   PG 查 generation_source-> ✅ 正常可查（说明表确实存在）")
    except Exception as error:  # noqa: BLE001
        print(f"   PG 查 generation_source-> ❌ 失败（{type(error).__name__}: {error}）")
        ok = False

    print(f"   结论     -> {'通过' if ok else '不通过'}")
    return ok


def main() -> int:
    """跑完全部检查，返回进程退出码（0=全通过）。"""
    results = {
        "PG 连通与迁移": check_pg_connection(),
        "MySQL 既有链路": check_mysql_still_works(),
        "Base 隔离（静态）": check_two_bases_isolated(),
        "跨库查询必须失败": check_wrong_database_queries_fail(),
    }

    print("=" * 70)
    for name, passed in results.items():
        print(f"   {'✅' if passed else '❌'} {name}")

    passed_all = all(results.values())
    print(f"\n汇总：{'全部通过' if passed_all else '存在失败项'}")
    if not passed_all:
        print("提示：PG 未就绪时先跑 `docker compose up -d pg`，再 `uv run alembic upgrade head`")
    return 0 if passed_all else 1


if __name__ == "__main__":
    sys.exit(main())
