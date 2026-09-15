---
name: useful-commands
description: 项目常用命令, 比如前端和后端的启动命令
metadata:
  version: "1.0"
---

### 1. 前端

1.1 前端启动命令

```shell
cd ./frontend-react
npm run dev
```

> Local: http://localhost:5173/

### 2. 后端

2.1 后端启动命令

```shell
cd ./backend-uv-fastapi
uv run fastapi dev
```

> Server started at http://127.0.0.1:8000
> Documentation at http://127.0.0.1:8000/docs

2.2 后端测试命令

```shell
uv run pytest -q
```

> -q 含义：quiet，静默模式，不输出测试详情

### 3. 中间件（Redis 队列 / PostgreSQL）

3.1 启动中间件

```shell
cd ./backend-uv-fastapi
docker compose up -d
```

> Redis: 127.0.0.1:6379 —— 任务队列，**阶段 0 起必需**
> PostgreSQL: 127.0.0.1:5432 —— 对话 / 知识库 / 向量，阶段 1 起使用

3.2 验证 Redis 连通

```shell
docker exec wgp-redis redis-cli ping
```

> 期望输出：PONG

3.3 队列与 worker 配置自检

```shell
cd ./backend-uv-fastapi
uv run python -m app.utils.utils_check.check_arq
```

> 必须用 `-m` 方式运行，否则 `app` 包不在 import 路径里。
> uv 不可用时用：`.venv\Scripts\python.exe -m app.utils.utils_check.check_arq`

### 4. Agent worker（**阶段 0 起必需**）

4.1 启动 worker

```shell
cd ./backend-uv-fastapi
arq app.core.worker.WorkerSettings
```

> ⚠️ worker 与后端 API 是**两个进程**，必须各起一个。
> 生成任务由 worker 执行，API 只负责"提交任务 + 查询进度"；
> 只起 API 不起 worker 的话，任务会一直停在"排队中"。

4.2 检查 worker 是否存活

```shell
arq --check app.core.worker.WorkerSettings
```

> 输出类似：`j_complete=0 j_failed=0 j_retried=0 j_ongoing=0 queued=0`
> 查不到该 key 时命令以退出码 1 结束，说明当前没有 worker 在运行。

### 5. PostgreSQL（对话 / 知识库，阶段 1 起）

5.1 执行迁移（建三张表 + 装 `vector` 扩展）

```shell
cd ./backend-uv-fastapi
uv run alembic upgrade head
uv run alembic current
```

> ⚠️ `alembic.ini` **必须保持纯 ASCII**（注释用英文）。
> Alembic 用系统 locale 编码读这个文件，中文 Windows 下是 GBK；
> 一旦里面出现 UTF-8 中文注释，**所有 alembic 命令**都会在启动前 `UnicodeDecodeError` 崩掉。
> 中文说明放在 `app/alembic/env.py` 与 `docs/agent_refactor_plan.md`。

5.2 双数据源自检（含"跨库查询必须失败"验收）

```shell
cd ./backend-uv-fastapi
uv run python -m app.utils.utils_check.check_pg
```

> 检查四项：PG 连通与迁移状态 / MySQL 既有链路未受影响 /
> 两个 declarative Base 静态隔离 / **拿错库的会话查表必须报错**。
