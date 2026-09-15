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

### 10. Agent 生成模式（阶段 6 起）

10.1 循环行为 / 三层自主性 / 端到端自检

```shell
cd ./backend-uv-fastapi
uv run python -m app.utils.utils_check.check_web_agent
```

> 四段：
> ① 离线（假模型）：门禁拦"只写 1 个文件就收工"、故障注入后模型能否改正、步数刹车保留产物；
> ② ⚠️ 真实模型：**三层自主性**（自主调工具 / 自主拆解 / 响应工具报错）+ **思考与非思考两种客户端对照**
> （同一份清单、同一次故障注入，用于确定默认客户端）；
> ③ 端到端：需要 API **与 worker 都是新代码**在跑；
> ④ 进程内流水线：真模型 + 真 MySQL/PG + 真落盘，不需要 API/worker，结束后自动清理。

⚠️ **改了 worker 相关代码必须重启 arq worker**（它不热重载，FastAPI dev 会热重载）。
否则提交 `gen_type="agent"` 的任务会在旧 worker 上立刻失败（"生成类型 agent 尚未实现"）：

```shell
cd ./backend-uv-fastapi
arq app.core.worker.WorkerSettings          # Ctrl+C 后用同一条命令重启
```

10.2 手工试用 agent 模式

```shell
# 提交（立刻 202，真正的生成由 worker 异步执行）：
curl -X POST http://127.0.0.1:8000/api/generation/create \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"prompt":"做一个单页待办清单，能添加/勾选/删除，数据存 localStorage","gen_type":"agent"}'

# 轮询阶段（routing → retrieving → planning → generating → done）：
curl http://127.0.0.1:8000/api/generation/<task_uuid> -H "Authorization: Bearer <token>"
```

> 带附件时再加 `"session_uuid":"<uuid>"`（附件必须先用 `/api/agent/source/upload` 传进该会话）。
> 三种模式并存：`single` / `multi`（旧实现，阶段 7 做对照）与 `agent`（新流水线）。
> 难度档位与"预估 vs 实际"对账见 `generation_plan` 表（`docs/agent_refactor_plan.md` 阶段 5）。

### 11. 新旧实现对照实验（阶段 7）

11.1 三模式对照（成功率 / 产物完整度 / 总 token / 耗时）

```shell
cd ./backend-uv-fastapi
uv run python -m app.utils.utils_check.check_compare --dry-run   # 先看将执行的组合，不调模型
uv run python -m app.utils.utils_check.check_compare             # 默认 3 需求 × 3 模式 × 1 次 = 9 次
```

> ⚠️ 本脚本会**真实调用大模型**（按 token 计费），且需要 API 与 **worker 同时在跑**。
> 常用参数：`--needs r1,r3`（挑需求）、`--modes agent,multi`（挑模式）、`--runs 3`（重复次数）、
> `--timeout 600`（单次轮询上限秒）。
> 判据：`integ=Y` 表示产物完整（入口存在、清单文件全部落盘、入口引用的本地资源都在、
> 入口不是被截断的 HTML）；`delivery` = 成功且产物完整；`out/delivery` = 每份**可用交付**
> 平均烧掉的输出 token（含失败任务的浪费）。
> 报告落盘 `docs/experiments/stage7_compare_<时间戳>.json`（含明细 / 汇总 / agent 对账）。
>
> 中文控制台若显示乱码或报 `UnicodeEncodeError`，用 `$env:PYTHONIOENCODING="utf-8"` 再跑。

### 12. 会话式生成（阶段 8 起，前端）

12.1 启动前端

```shell
cd ./frontend-react
npm run dev
```

> http://localhost:5173 —— `/api` 与 `/preview` 都会代理到后端 8000（见 `vite.config.ts`）。
> 会话式生成需要**三个进程同时在跑**：`fastapi dev` + `arq app.core.worker.WorkerSettings` + `npm run dev`。

12.2 手工走一遍会话式流程（浏览器）

1. 打开 `/generate`：输入框为空时"发送"禁用；还没有会话时"添加附件"禁用（提示先发一条消息）
2. 发一句**模糊需求**（如"帮我做个网站"）→ Agent 会追问；补齐"做什么 + 有哪些功能"后，
   出现**需求确认卡片**（槽位里"未提及"的项也会显示）与「开始生成」
3. 上传一个 `.md` / `.pdf`（≤10MB）→ 出现 `@doc1` chip；传一个**扫描版 PDF** 会看到
   chip 标红 + 一条说明（后端返回 `parse_status=failed` 而不是报错）
4. 点「开始生成」→ 进度条 + 已等待秒数；完成后出现结果卡片 →「打开预览」
5. **刷新页面**：历史消息会回放出来（`session_uuid` 存在 localStorage 的 `wgp_agent_session`）
6. 折叠的「极速生成（单个 HTML 文件）」是 `single` 模式入口；界面上**不再有 multi**（已退役）

12.3 用 curl 复现同一套调用（排查接口层问题）

```shell
# ① 建会话（不带 session_uuid 表示新建）
curl -X POST http://127.0.0.1:8000/api/agent/chat \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"message":"帮我做个网站"}'

# ② 上传附件（multipart；字段名必须是 session_uuid / file）
curl -X POST http://127.0.0.1:8000/api/agent/source/upload \
  -H "Authorization: Bearer <token>" \
  -F "session_uuid=<uuid>" -F "file=@./需求说明.md"

# ③ 带附件的对话（attachments 里的 source_uuid 来自上一步）
curl -X POST http://127.0.0.1:8000/api/agent/chat \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"session_uuid":"<uuid>","message":"@doc1 按这份说明做","attachments":[{"source_uuid":"<source_uuid>","role":"content"}]}'

# ④ 生成（agent 模式 + 会话，附件才能被读到）
curl -X POST http://127.0.0.1:8000/api/generation/create \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" \
  -d '{"prompt":"需求要点：类型是单页展示；功能包含添加、勾选完成。","gen_type":"agent","session_uuid":"<uuid>"}'
```

> ⚠️ `POST /api/generation/create` 只接受**纯文本需求**：带附件时必须传 `session_uuid`，
> 别把文件内容塞进 `prompt`（附件由后端从会话里读，`@docN` 的作用域是会话）。

