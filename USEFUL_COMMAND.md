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

### 6. Agent 对话（阶段 2 起）

6.1 意图路由与多轮对话自检

```shell
cd ./backend-uv-fastapi
uv run python -m app.utils.utils_check.check_agent_chat
```

> ⚠️ 本脚本会**真实调用大模型**（按 token 计费）。
> 第一段验证三类输入的意图路由（不连库）；第二段走真实 HTTP 验证多轮对话落库与回放
> （需要 `uv run fastapi dev` 在跑，否则自动跳过）。

6.2 手工试用对话接口

```shell
# 先注册登录拿 token，再：
curl -X POST http://127.0.0.1:8000/api/agent/chat \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"message":"帮我做个网站吧"}'

# 带上上一轮返回的 session_uuid 继续聊：
curl -X POST http://127.0.0.1:8000/api/agent/chat \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"session_uuid":"<uuid>","message":"做个单页待办清单，能添加和删除"}'

# 查看会话详情与历史消息（回放）：
curl http://127.0.0.1:8000/api/agent/session/<uuid> -H "Authorization: Bearer <token>"
```

> 也可以直接打开 http://127.0.0.1:8000/docs 里的「Agent 对话」分组试用。

### 7. 文档解析与附件上传（阶段 3 起）

7.1 解析层与上传全链路自检

```shell
cd ./backend-uv-fastapi
uv run python -m app.utils.utils_check.check_source_upload
```

> 第一段（离线）验证解析层：GBK 的 `.txt` 正确解码、坏编码明确报错、`.md` 骨架、
> HTML 设计令牌、扫描版 PDF 报错、落盘字节保真；
> 第二段（⚠️ 真实模型）走真实 HTTP 验证：`.md` / 设计规范 `.html` / 真实 PDF / GBK `.txt` 各一份，
> 以及"扫描版 PDF 返回 `parse_status=failed` 而不是 5xx"（需要 `uv run fastapi dev` 在跑，否则自动跳过）。

7.2 手工试用上传接口

```shell
# 先建会话（或复用已有 session_uuid）：
curl -X POST http://127.0.0.1:8000/api/agent/chat \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"message":"按我上传的规范做"}'

# 上传附件（multipart；支持 .pdf/.html/.htm/.md/.txt，上限 10MB）：
curl -X POST http://127.0.0.1:8000/api/agent/source/upload \
  -H "Authorization: Bearer <token>" \
  -F "session_uuid=<uuid>" -F "file=@./需求说明.md"

# 查看会话下的附件（渲染 @docN chip 用）：
curl "http://127.0.0.1:8000/api/agent/source/list?session_uuid=<uuid>" \
  -H "Authorization: Bearer <token>"
```

### 8. 个人 RAG 占位（阶段 4 起）

```shell
cd ./backend-uv-fastapi
uv run python -m app.utils.utils_check.check_rag
```

> 第一段（离线）验证三态语义与安全边界：`skipped` 时**不调用** provider、
> 桩 `miss` 自报"检索尚未接入"、`hit` 保留 L1/L2 来源、`user_id` 必填且拒绝非法值、
> merge 的 `miss→uncertainty` 与 `skipped→静默`；
> 第二段（⚠️ 真实模型，约 1k in/次）验证"通用需求判不需要检索、指代私人资料判需要并给出关键词"。
> 一期**不实现检索**，二期只替换 `build_retriever_provider()`。

### 9. 交付规划与难度预算（阶段 5 起）

9.1 规划质量与「预估 vs 实际」对账自检

```shell
cd ./backend-uv-fastapi
uv run python -m app.utils.utils_check.check_plan
```

> 第一段（离线）验证清单清洗与预算：非法文件名丢弃、悬空依赖剔除、难度只上调、
> 全非法时兜底单文件、预算随难度递增；
> 第二段（⚠️ 真实模型，各跑 3 次）验证同一需求的文件清单是否稳定、难度是否与文件数匹配；
> 第三段（真实 PG）把规划写进 `generation_plan`、回填"实际值"并打出对账表（结束后自动清理）。

9.2 迁移与对账查询

```shell
cd ./backend-uv-fastapi
uv run alembic upgrade head          # 阶段 5 新增 generation_plan 表（revision b7c1d2e3f4a5）
uv run alembic current
```

> 难度档位与预算的唯一真源：`app/agents/common.py` 的 `BUDGET_BY_DIFFICULTY`
> （easy 6 步/12k/1 文件、medium 12 步/40k/4、hard 20 步/80k/8）。
> ⚠️ 实际值（实际步数/文件数/结果）由**阶段 6 的生成循环结束时**调用
> `GenerationPlanRepository.mark_outcome()` 回填；在那之前该表只有预估侧有值。
