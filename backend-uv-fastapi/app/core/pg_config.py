# app/core/pg_config.py —— PostgreSQL 配置：从 .env 读取（对话 / 知识库库）
#
# 定位（见 docs/agent_refactor_plan.md §3.4）：
#   MySQL 存业务（user / generation_task），PG 存对话与知识库（agent_session / agent_message /
#   generation_source，二期再加向量表）。**两库不 JOIN**，只靠 id 在 service 层组装。

from sqlalchemy import URL

from app.core.settings_base import AppSettings


class PgSettings(AppSettings):
    """PostgreSQL 连接配置。

    变量命名刻意与 `docker-compose.yml` 对齐：
    compose 会用同一份 `.env` 插值 `POSTGRES_*` 建库，后端再读同一批变量去连 ——
    这样数据库用户名 / 密码 / 库名**只有 .env 一个真源**，不会出现"compose 改了、后端没改"。
    """

    pg_host: str = "127.0.0.1"
    pg_port: int = 5432
    postgres_user: str = "wgp"
    postgres_password: str = "wgp_dev_password"
    postgres_db: str = "wgp_agent"

    @property
    def database_url(self) -> str:
        """拼出 SQLAlchemy 连接串（驱动用 psycopg 3）。

        为什么用 `URL.create(...)` 而不是手写 f-string：
        密码里一旦出现 `@` `:` `/` 这类字符，手拼的 URL 会被解析错（主机名被截断、认证失败）。
        URL.create 会做正确的转义，这是 SQLAlchemy 官方推荐的拼法。

        Returns:
            形如 ``postgresql+psycopg://user:pass@host:5432/db`` 的连接串。
        """
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.pg_host,
            port=self.pg_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)


pg_settings = PgSettings()  # 全局共用一份
