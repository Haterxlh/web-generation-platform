# 项目进度 —— backend-uv-fastapi

> 本文件用于跨会话同步开发进度。每次总结进度时按此格式更新。
> 最近更新时间：2026-09-15

## 1. 模块进度

### 模块：基础设施（MySQL 连接与会话）
- **状态**：已完成
- **功能范围**：建立 FastAPI 与本地 MySQL 的连接、会话管理与建表能力
- **已交付内容**：
  - 核心文件：
    - `app/core/mysql_config.py`（读取 `.env` 的 MySQL 配置并拼连接串）
    - `app/core/mysql_db.py`（engine / sessionmaker / `MysqlBase` 基类 / `get_mysql_db()` 依赖）
    - `app/utils/db/create_all_table.py`（开发期建表脚本，需先 import 各模型）
  - `.env`：`MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DB_NAME`（已被 .gitignore 忽略）
- **关键决策**：
  - 连接串走环境变量，敏感信息不进代码/git
  - `extra="ignore"` 允许一份 `.env` 被多个 Settings 类共享（该配置现已收敛到 `settings_base.py`）
  - `Base` 命名为 `MysqlBase`（用户自定义）
- **验证情况**：已实测连接本机 MySQL 成功（`SELECT 1` 通过）；`wgp_db` 库连通
- **待办与遗留**：无

### 模块：用户模块（注册 / 登录 / 当前用户）
- **状态**：已完成
- **功能范围**：用户注册、登录签发 JWT、携带 token 获取当前用户
- **已交付内容**：
  - 接口：
    - `POST /api/user/register` —— 注册（账号查重，409）
    - `POST /api/user/login` —— 登录（返回 JWT + 用户信息）
    - `GET /api/user/current` —— 获取当前登录用户（Bearer token 校验）
  - 核心文件：
    - `app/models/user.py`（User ORM 实体，11 列，映射 `user` 表）
    - `app/schemas/user_schemas.py`（RegisterRequest / LoginRequest / UserResponse / LoginResponse，响应白名单不含密码）
    - `app/utils/jwt/security.py`（bcrypt 哈希、JWT 签发/解析；**本次会话已从 `app/utils/` 迁入 `jwt/` 子包**）
    - `app/core/jwt_config.py`（JWT_SECRET / HS256 / 过期时间 / 预览票据有效期）
    - `app/utils/jwt/parse_token.py`（`get_current_user` 登录校验依赖；同上已迁入 `jwt/`）
    - `app/repositories/user_repository.py`（create / get_by_user_account / get_by_id）
    - `app/services/user_service.py`（register / login 业务规则）
    - `app/api/user.py`（用户路由）
    - `app/main.py`（挂载用户路由，替换原脚手架示例）
    - `tests/test_smoke.py`、`tests/test_user_service.py`（初步测试文件）
- **关键决策**：
  - 密码使用 bcrypt 加盐哈希存储，绝不存明文
  - 认证采用 JWT 无状态方案（pyjwt），密钥走 `.env`
  - 建表采用 `Base.metadata.create_all()`，**模型为唯一真源**；尚未引入 Alembic
  - 逻辑删除：所有查询过滤 `is_delete == 0`
  - ORM 列名显式映射驼峰数据库列（`mapped_column("userAccount", ...)`）
  - docstring 统一 Google 风格（用户要求）
- **验证情况**：端到端 5 项验证全部通过（注册 200 无密码字段 / 重复注册 409 / 登录 200 拿 token / 带 token 访问 current 200 / 库中密码为 `$2b$12$...` 哈希）；本次重组 utils 后再次验证 `import app.main` 与登录链路正常
- **待办与遗留**：
  - `tests/` 尚不完整：`test_smoke.py` 断言根路由返回 `{"Hello": "World"}`，但 `main.py` 已删除该示例路由，**该用例预计失败**；`test_user_service.py` 无断言。需补全测试并跑 `uv run pytest`
  - 前端用户模块**已对接**（见 `frontend-react/docs/proj_progress.md`）
  - 模型字段后续变更时需引入 Alembic 迁移（`create_all` 不能改已存在的表）
  - `get_current_user` 目前位于 `app/utils/jwt/parse_token.py`，按分层约定属接口层依赖，后续可评估是否迁回 `api/deps.py`

### 模块：配置与目录规范（Settings 基类 + utils 子包化）
- **状态**：已完成
- **功能范围**：统一 `.env` 定位与读取规则；把 `app/utils/` 按用途拆分为子包
- **已交付内容**：
  - 核心文件：
    - `app/core/settings_base.py`（`BASE_DIR` / `ENV_FILE` / `AppSettings` 基类：`extra="ignore"` 只写一处）
    - `app/core/mysql_config.py`、`app/core/jwt_config.py`、`app/core/llm_config.py`、`app/core/storage_config.py` 全部继承 `AppSettings`
    - 子包：`app/utils/db/`（建表脚本）、`app/utils/jwt/`（security、parse_token）、`app/utils/weg_gen/`（prompt_loader、code_extractor、file_writer）、`app/utils/utils_check/`（开发期自检脚本）
- **关键决策**：
  - 路径推算统一用 `Path(__file__).resolve().parents[N]`，且只在 `settings_base.py` 写一次
  - `utils` 按**用途**分子包，不再平铺；移动文件后同步修正了 `PROMPTS_DIR` 的 `parents` 下标与全部 import
  - 开发期脚本（`check_*`）与生产工具分开放，避免混入业务路径
- **验证情况**：全仓扫描无残留旧导入路径；`import app.main` 通过；各 `utils_check` 脚本均可运行

### 模块：大模型接入（DeepSeek V4.1-Flash）
- **状态**：已完成
- **功能范围**：接入 `deepseek-flash`，为生成模块提供两条模型链路
- **已交付内容**：
  - 核心文件：`app/core/llm_config.py`（Settings）、`app/core/llm_client.py`（两个客户端）、`app/utils/utils_check/check_llm.py`
  - 依赖：`langchain` 1.x、`langgraph` 1.x、`langchain-deepseek`
- **关键决策**（**实测结论**，详见 `docs/generation_module_design.md` §7）：
  - 思考模式**默认开启**，开关是 `extra_body={"thinking": {"type": "enabled|disabled"}}`
  - 思考模式下 `temperature` **静默失效**（不报错但无效），调参只能调 `reasoning_effort`（low/high/max）
  - **强制 schema 与思考模式互斥**：思考模式不支持"强制指定某个函数"的 `tool_choice`，而 `with_structured_output` 正是靠它 → 结构化输出必须用**非思考模式**客户端
  - 因此建两个客户端：`llm_client`（生成用，思考模式）+ `llm_structured_client`（结构化输出用，强制关闭思考）
  - `reasoning` token **计入 output**；实测一次单文件生成 reasoning 占 12300/16384 → 已把 `LLM_MAX_TOKENS` 调至 32768、`DEEPSEEK_REASONING_EFFORT` 调至 `low`
    （2026-09-14 更新：`.env` 实际为 `LLM_MAX_TOKENS=345600`；且 `reasoning_effort=low` 实测疑似被静默忽略 —— 详见生成模块的「待办与遗留」）
- **验证情况**：`check_llm` 三项（普通对话 / 流式 / 结构化输出）通过；截断场景已实测复现，并按预期抛出明确错误

### 模块：生成模块（Web 生成：单文件 / 多文件）
- **状态**：已完成（核心链路；流式输出与测试待补）
- **功能范围**：一句话需求 → 生成可直接打开的网页（单 HTML 文件 / html+css+js 三文件），落盘 + 落库 + 带鉴权的预览
- **已交付内容**：
  - 接口：
    - `POST /api/generation/create` —— 提交生成（**202 异步，立即返回**；阶段 0 起改为入队，由 worker 执行）
    - `GET /api/generation/list` —— 我的生成历史（分页）
    - `GET /api/generation/{task_uuid}` —— 任务详情
    - `POST /api/generation/{task_uuid}/preview-ticket` —— 签发预览票据（写 HttpOnly Cookie）
  - 核心文件：
    - `app/models/generation_task.py`（`generation_task` 表：taskUuid / status / resultDir / fileList / token 用量 / 三时间列 / 逻辑删除）
    - `app/schemas/generation_schemas.py`、`app/repositories/generation_repository.py`、`app/services/generation_service.py`、`app/api/generation.py`
    - `app/agents/common.py`（`ModelUsage` / `GenerationResult` / 截断检查）
    - `app/agents/single_html_flow.py`（单文件：一条 LCEL 链）
    - `app/agents/multi_file_graph.py`（多文件：LangGraph 图）
    - `app/prompts/single_html_system.md`、`app/prompts/multi_file_system.md`（提示词）
    - `app/utils/weg_gen/prompt_loader.py`、`code_extractor.py`、`file_writer.py`
    - `app/core/storage_config.py`（产物根目录 / 预览前缀）、`app/core/preview_static.py`（`PreviewStaticFiles`）
  - 产物目录：`generated/{user_id}/{task_uuid}/`（根 `.gitignore` 已忽略）
- **关键决策**：
  - 一次生成 = **一条任务记录 + 一个磁盘目录**；对外标识一律用 `task_uuid`（uuid4.hex），不暴露自增 id
  - 分层新增 **`agents/`（LLM 编排层）**：纯函数，只产出 `{文件名: 内容}`，**不落盘、不落库**；落盘与落库统一由 service 做（因此可脱离 MySQL/FastAPI 测试）
  - 单文件模式 = 一条链；多文件模式 = 状态图 `plan → generate → validate →（不合格则重试，最多 2 次）`
  - 多文件**一次调用生成三个文件**（同一次上下文里先写 html 再写 css/js，天然自洽），质量靠"整体重试"保证，而不是分文件并发生成
  - 文件名由**我们**钉死（按代码块语言标记映射），不信任模型给出的文件名（实测模型会把 `style.css` 写成 `styles.css`）
  - 图里的 `errors` 用**覆盖**语义而非 `Annotated[..., operator.add]`：累加会让"重试成功"仍被判定为不合格
  - 预览鉴权用 **Cookie 票据**而非 Bearer：浏览器加载 css/js 子资源时不会带自定义请求头；票据与 `user_id + task_uuid` 绑定且 Cookie path 锁在本次预览目录
  - 提示词放 md 且**不经过模板引擎**（CSS/JS 的花括号会被 `ChatPromptTemplate` 当变量解析而报错）
  - token 用量分三列记录（input / output / reasoning），**失败的任务也记账**（异常携带用量）
- **本轮修复（2026-09-14）**：
  1. `GenerationService.create` 成功路径缺少 `GenerationTaskRepository.update(db, task)` 与
     `return GenerationService._to_response(task)`：接口报 500（`ResponseValidationError: input: None`），
     且任务状态永远停在 `running`、`result_dir` / `file_list` / token 用量均未落库。
     已补 `duration_ms` 赋值与「提交 + 返回」两步。
  2. **多文件模式改用非思考客户端（`llm_no_thinking_client`）**：
     思考模式下模型把 94% 的输出预算花在"思考里反复起草代码"（`reasoning_content` 57229 字符 / 96 个代码碎片），
     最终答案只剩约 1400 token，于是每次交出随机残缺子集（三次分别缺 js / 缺 css+js / 缺 css+js）；
     三轮提示词加固均无效 —— **提示词管不到思考阶段的预算分配**。
     对照实验（同需求、同提示词，只切换思考开关）：`output_tokens` 30619 → **3331**，一次调用即输出三个完整代码块。
     **单文件模式保留思考模式**（依据：单文件已成功 2 次、多文件 0/3）。
  3. 失败时把模型原文落盘到 `generated/{user_id}/{task_uuid}/_debug_raw.txt`（`write_debug_raw`），
     消除"失败只能再烧一次 token 复现"的可观测性缺口。
  4. 重试反馈区分读者：不再把"（请检查提示词的输出格式约定）"这类**面向开发者**的诊断喂回模型，
     改为面向模型的指令（`CodeExtractError` 新增 `missing` / `found` 属性，`_extract_and_check` 据此生成指令）。
- **验证情况**：
  - `agents/` 离线脚本：`check_extractor`（7 项）、`check_langgraph`、`check_multi_graph`（5 场景，含用量累加核对）全绿
  - **HTTP 接口端到端**：2026-09-14 修复上方 bug#1 后**首次**跑通（create → list → preview-ticket → 带 Cookie 打开产物，含 multi 子资源加载）
  - 多文件模式：修复 bug#2 后跑通，三个产物文件齐全、预览正常
  - 预览鉴权：8 种情形（无 Cookie / 乱码 / 合法 / 子资源 / 目录首页 / 越权 / 串票据 / 篡改）结果均符合预期
  - `pytest`：`test_smoke`(2) + `test_user_service`(2) + `test_generation_schemas`(2) = 6 个离线用例全绿
  - 开发期脚本：`check_llm`、`check_langgraph`、`check_extractor`、`check_multi_graph`、`check_preview_auth`、`check_router`、`check_single_flow`、`check_multi_response`
- **待办与遗留**：
  - **流式输出（SSE）未实现**（原计划步骤 10）；复杂需求实测耗时可达 **157 秒**，前端目前只有"请稍候"文案
  - 单文件模式**没有重试**（多文件有）；输出被截断时只能靠调大 `LLM_MAX_TOKENS`
  - `pytest` 已补 6 个**离线**用例（`test_smoke` / `test_user_service` / `test_generation_schemas`）；接口级与图的重试 / 截断分支仍未覆盖
  - 预览"分享链接"机制未做（票据与浏览器绑定，无法分享给别人）
  - 生成产物目前是本地目录，未做清理策略与配额
  - 本次给 `generation_task` 加 token 三列是**手写 ALTER TABLE**（`create_all` 不能改已存在的表）
  - **`DEEPSEEK_REASONING_EFFORT` 疑似被静默忽略**：`low` 档实测仍产出 28984 个思考 token（与设计约定 §7.2 的 temperature 静默失效同款），待专门验证
  - **`LLM_MAX_TOKENS` 实际为 `.env` 中的 345600**（本文档此前记为 32768，已过期；`.env` 不入库，换机器要重新确认）
  - 数据库遗留：**1 条历史僵尸 `running` 记录已于 2026-09-15 被 worker 的僵尸回收清理**（见 Agent 框架模块）；
    2 条 prompt 中文被替换为 `?` 的脏数据（2026-09-13 由命令行客户端发送时降级，与代码和数据库无关）
  - 多文件重试目前仍是"整批重来"；可选改造为**定向补缺**（非严格抽取 + `contents` 合并语义 + 只要求补缺文件）

### 模块：Agent 框架（阶段 0 异步骨架 / 阶段 1 双数据源基座）
- **状态**：进行中（阶段 0、1、2 已完成；阶段 3~8 与二期 RAG 的方案见 `docs/agent_refactor_plan.md`）
- **功能范围**：把"同步阻塞到生成结束"的生成接口改成 **Redis 队列 + 独立 arq worker 进程**，
  并引入 **Agent 流水线阶段**（阶段 / 进度 / 明细）供前端轮询展示
- **已交付内容**：
  - 接口变更：
    - `POST /api/generation/create` —— 同步 → **202 + 已受理**（返回 `poll_url` / `poll_interval_ms`）
    - `GET /api/generation/{task_uuid}` —— 兼作**进度轮询接口**（新增 `stage` / `stage_text` / `stage_detail` / `progress`）
  - 核心文件：
    - `app/core/agent_config.py`（并发 / 超时 / 重试 / 僵尸宽限 / 轮询间隔）
    - `app/core/redis_config.py`（→ arq `RedisSettings`）、`app/core/arq_pool.py`（连接池 + 任务名常量）
    - `app/core/worker.py`（`WorkerSettings` + `run_generation` job + 启动时僵尸回收）
    - `app/agents/stages.py`（`AgentStage` 枚举 + 中文文案 + 进度映射）
    - `app/models/generation_task.py`（+`stage` / `stageDetail` / `progress` 三列）
    - `app/repositories/generation_repository.py`（+`list_stale_running`）
    - `app/services/generation_service.py`（`create` 改入队；+`execute_pipeline` / `recover_zombies` / `_set_stage` / `_fail`）
    - `app/api/generation.py`、`app/main.py`（`lifespan` 管理 arq 池）
    - `docker-compose.yml`（redis + pgvector/pg18）、`sql/scripts/alter_generation_task_stage.sql`
    - `tests/test_task_pipeline.py`、`app/utils/utils_check/check_arq.py`、`check_http_e2e.py`
  - 前端：`src/types/generation_types.ts`（+`AgentStage` / `GenerateAccepted`）、`src/api/generation_api.ts`、
    `src/pages/Generate/GeneratePage.tsx`（改为轮询 + 进度条 + 已等待计时）、`src/styles/global.css`
- **关键决策**：
  - **Redis 只做队列，不是真源**：任务状态 / 阶段 / 进度 / 结果一律落 MySQL（`keep_result=0`）
  - **arq `max_tries=1`**：arq 默认 5 且采用悲观执行（worker 中途关闭会重跑）；LLM 按 token 计费，
    自动重试等于重复烧钱 → 宁可标 `failed` 让用户手动重试
  - **`status` 与 `stage` 正交**：前者是生命周期，后者是进度；合并会出现"success 但 stage 卡在 generating"
  - **`_set_stage` 必须显式写 `updateTime`**：本表 DDL 的 `updateTime` **没有 `ON UPDATE` 子句**
    （`server_onupdate` 不会出现在 UPDATE 语句里），而僵尸回收靠它判断陈旧 —— 这个字段同时是"最后心跳"
  - **失败时保留 progress**，不归零（知道"死在 70%"比归零更有排查价值）
  - 队列里**只传 `task_uuid`**（arq 默认 pickle 序列化，传字符串最小且安全），参数由 worker 回库读
  - 开发期 API 与 worker 是**两个进程**：`uv run fastapi dev` + `arq app.core.worker.WorkerSettings`
  - 注：`app/core/arq_pool.py` 用 `init_pool()` 而非 `get_pool()` 作为唯一入口 —— 它幂等可重试，
    Redis 恢复后能自愈；`app/main.py` 的 lifespan 刻意**不让 Redis 故障导致应用起不来**
- **验证情况**：
  - `pytest`：**23 个用例全绿**（新增 `test_task_pipeline.py`，全程离线，不连 Redis / MySQL / 模型）
  - **HTTP 端到端（真实 LLM）**：提交 202 仅 **0.09 秒**（原同步 30~157 秒）；1.6s 观测到 `generating 70%`，
    28.8s 到 `success/done 100%`；产物 `['index.html']`、`preview_url` 与 token 用量均正确
  - **僵尸回收（真实数据）**：历史僵尸 id=5 被 worker 启动时回收，`updateTime` 刷新、`error_msg` 写入
  - `check_arq`：Redis 连通 / 成本护栏配置 / 入队与 `_job_id` 去重 三项全通过
  - `npm run typecheck` 通过
  - **阶段 1（2026-09-15）**：`pytest` **65 个用例全绿**（原 23 → 新增 42）；
    `check_pg` 四项全通过 —— PG 连通与迁移（`vector 0.8.6` 已装、`alembic 4e97ff4fef89`）、
    MySQL 既有链路未受影响、两个 Base 静态隔离（表集合交集为空）、
    **跨库查询必须失败**（MySQL 查 `agent_session` 与 PG 查 `generation_task` 均按预期 `ProgrammingError`，
    而 PG 查自己的表正常 —— 排除"表不存在"的假阳性）；
    真实模型探针确认 `write_file` 的 `dict[str, str]` schema 被 DeepSeek 接受（`finish_reason=tool_calls`，337 tokens）
  - **阶段 2（2026-09-15）**：`pytest` **132 个用例全绿**（65 → 新增 67）；
    `check_agent_chat` 三项全通过 ——
    ① **三类输入路由正确**（纯咨询 → `chat`；"你看着办" → `generate+needs_clarification`；
    明确需求 → `generate+ready`，5 个槽位全中，单次 1.4~2.0s、`reasoning=0` 证实非思考客户端）
    ② **槽位跨轮累积**（旧槽位保住 + 新抽取的 style 合并进来）
    ③ **多轮对话落库与回放**（真实 HTTP + PG：4 条消息、角色序列与顺序正确、草稿 summary 正确）
- **本轮修复（2026-09-15）**：
  1. **`docker-compose.yml` 的 PG 数据卷路径**（用户实测发现并修正）：
     PG 18+ 镜像期望挂载整个 `/var/lib/postgresql`，并在其下自建 `18/docker` 子目录存放数据；
     原先沿用的 `/var/lib/postgresql/data` 会让容器启动时检测到目录结构不对而**直接崩溃退出**。
  2. **前端 2 个既有 lint 报错**（规则 `react-hooks/set-state-in-effect`）：
     - `hooks/AuthProvider.tsx`：`initializing` 改为由"本地有无 token"惰性初始化
       （`useState(() => getToken() !== null)`），删掉 effect 里那次多余的同步 `setInitializing(false)`
       —— 顺带消除了未登录用户第一帧的"正在恢复登录态"闪烁。
     - `pages/Projects/ProjectsPage.tsx`：首屏加载不再复用带 `setLoading(true)` 的 `load()`，
       改为把 `setState` 全部放进 Promise 回调（该规则只认可这种形态），
       并加 `cancelled` 兜住"组件卸载后请求才返回"的竞态。
  3. 修复后 `eslint` / `tsc -b` / `npm run build`（39 modules，886ms）全部通过。
  4. 阶段 1 依赖就绪：`psycopg[binary]`、`alembic` 已 `uv add` 写入 `pyproject.toml` / `uv.lock`。
- **阶段 1 交付（2026-09-15）**：
  - **双数据源基座**：`app/core/pg_config.py`（用 `URL.create` 拼串，避免密码含 `@` `:` 时被解析错）、
    `app/core/pg_db.py`（`pg_engine` / `PgSessionLocal` / `PgBase` / `get_pg_db()`，与 `mysql_db.py` 对称）、
    `app/models/agent/`（`agent_session` / `agent_message` / `generation_source`，PG 侧列名统一 `snake_case`，
    时间列用 `timestamptz`，`is_delete` 沿用 0/1 与 MySQL 一致）
  - **PG 迁移**：`alembic.ini` + `app/alembic/`；首个迁移 `4e97ff4fef89` 建三表 + `CREATE EXTENSION vector`，
    **刻意不建向量表**（BGE-M3 是 1024 维，过早写死维度会让二期换模型时要迁移数据）。
    MySQL 侧继续用 `create_all`，两套工具的边界已在文档写明
  - **Harness 基座**：`app/utils/weg_gen/file_store.py`（per-request 虚拟文件系统，含 `missing()` 完成门禁判据）、
    `app/agents/web/tools.py`（`write_file` / `read_file` / `list_files`，**永不抛异常**、失败原因作为字符串回给模型）、
    `app/agents/common.py`（+`AgentTrace` / `ToolCallRecord`）；
    `file_writer._safe_name` 提升为公开的 `safe_name`（工具集复用，避免两套正则漂移）
  - 自检与测试：`app/utils/utils_check/check_pg.py`、`tests/test_web_tools.py`、
    `tests/test_pg_metadata_isolation.py`；`app/utils/db/create_all_table.py` 加了"别 import PG 模型"的警告
  - **刻意推迟**：`StageBudget` → 阶段 5（唯一消费者是难度分级）；`app/repositories/agent/` → 阶段 2（首个消费者是 chat 会话）
- **阶段 2 交付（2026-09-15）**：
  - **意图路由**：`app/agents/router/intent_router.py` —— 全 AI 结构化判定（intent / readiness / slots /
    missing_slots / ask_hint / reason），不做规则前置；**Python 侧硬兜底**（`apply_readiness_gate`：
    模型说 ready 但必备槽位为空 → 强制转需澄清）
  - **澄清对话**：`app/agents/chat/chat_agent.py` + 两个 prompt —— chat-agent **不抽取槽位**（只负责说话），
    与 router 职责分离，因此两者可各自评测与迭代
  - **别名机制**：`app/utils/agent/alias.py`（纯函数）—— 正则带前后边界，**邮箱不会被误判成附件引用**；
    未注册的 `@xxx` 原样保留；失效附件展开成"已失效"而不阻断历史回放；
    未解析的附件显式渲染成"尚未解析"（不静默省略）
  - **结构化契约**：`app/agents/state.py` —— `RequirementSlots`（`need_persistence` 用 `bool|None` 三态，
    避免"没提"被当成"不需要"）、`RouterDecision`、`RouterResult`、`ChatResult`
  - **对话编排与接口**：`app/services/agent_chat_service.py`、`app/api/agent.py`（`POST /api/agent/chat`、
    `GET /api/agent/session/{uuid}`）、`app/schemas/agent_schemas.py`；
    数据源是 **PG**（注入 `get_pg_db`，传错 session 会直接 ProgrammingError）
  - **配套改动**：`AgentStage` 增加 `CLARIFYING`（human-in-the-loop 暂停态）；
    `list_stale_running()` 增加 `exclude_stages`，`recover_zombies()` 传入 `clarifying`
    —— 否则用户思考超过宽限期，暂停中的任务会被僵尸回收误判失败
  - **Alembic 迁移 `eac80e932e7f`**：给 `agent_session` 加 `draft_requirement`（JSONB 需求草稿）
  - **决策**：`ready` 时不调 chat-agent（确认摘要由槽位唯一确定，省一次调用）；
    router 失败降级**按路径不同**（对话路径 → chat，直达路径 → generate+ready）
- **待办与遗留**：
  - ✅ **`npm run lint` / `tsc -b` / `npm run build` 已于 2026-09-15 全部通过**；此前 2 个 `react-hooks/set-state-in-effect` 报错（位于
    `hooks/AuthProvider.tsx` 与 `pages/Projects/ProjectsPage.tsx`）已修复，详见上方"本轮修复（2026-09-15）"
  - 阶段 0 验证在库里留下 1 个临时账号 `e2e9754b32245` 与 1 条任务（如需清理请手动处理）
  - 进度只到"阶段级"，尚无 SSE 实时推送（当前靠前端轮询，间隔 1500ms）
  - 产物落本地磁盘 + worker 独立进程 = **单机假设**；将来 worker 与 API 分机器部署必须换对象存储
  - API 与 worker 需**分别启动**，目前仅靠 `USEFUL_COMMAND.md` 说明，未做进程守护 / 一键脚本

## 2. 项目级约定（跨模块通用）
- 后端分层调用方向：`api → services → repositories → 数据库`，禁止跨层调用；LLM 编排统一放 `agents/`
- 依赖管理：uv（依赖变更后提交 `uv.lock`）
- 敏感信息一律走 `.env`（`.env` 不入 git）；`.env` 的定位与读取规则只在 `app/core/settings_base.py` 定义
- 接口出入参使用 Pydantic 模型校验，字段带中文 `description`（保持 /docs 可读）
- 数据库操作一律 ORM/参数化，禁止拼接 SQL；只连本地业务库
- 若接入 MySQL：绝不操作 `mysql`、`sys`、`performance_schema` 等系统库
- docstring 采用 Google 风格
- `app/utils/` 按用途分子包（`db` / `jwt` / `weg_gen` / `utils_check`），不再平铺新文件

## 3. 下一步计划（按优先级）
- [x] **Agent 框架阶段 2**（已完成 2026-09-15，见上方模块进度）
- [ ] **Agent 框架阶段 3**：文档解析 —— `utils/doc/pdf_parser.py` / `html_parser.py`（**无 LLM**）、
      `digest-agent`（**仅文件** → `RequirementDigest`，含 content/style/both 判定）、
      `merge`（对话摘要 + 四来源冲突消解 → `FinalRequirement`）、`POST /api/agent/source/upload`
      （阶段 3 实现、阶段 6 入图）（归属：Agent 框架）
- [ ] 生成进度体验：把轮询升级为**流式输出（SSE）**；轮询版已在阶段 0 落地（进度条 + 已等待计时）（归属：生成模块 / frontend-react）
- [ ] 补全 pytest：用假模型覆盖图的重试分支与截断分支、service 状态流转（`build_multi_file_graph(model=..., planner=...)` 是现成注入点）（归属：生成模块）
- [ ] 验证 `DEEPSEEK_REASONING_EFFORT` 是否真的生效（同需求 low / max 各跑一次，比对 `reasoning_tokens`）（归属：大模型接入）
- [ ] 可选：多文件"定向补缺"重试；失败原文落盘时附带元信息头（finish_reason / usage / errors）（归属：生成模块）
- [ ] 长期：引入 Alembic 管理表结构迁移（归属：基础设施）
- [ ] 可选：预览分享链接、生成产物清理策略、用量统计接口

### 变更记录
- **2026-09-12（commit `2158079`）**：生成模块全套（`agents/`、`api/generation.py`、`models/generation_task.py`、`schemas/`、`services/`、`repositories/`、`prompts/`、`utils/weg_gen/`）+ 大模型接入（`core/llm_client.py`）+ 配置基类（`core/settings_base.py`）+ 存储配置 + 预览鉴权；`app/utils/` 拆为用途子包；`frontend-react/vite.config.ts` 新增 `/preview` 代理
- **2026-09-14**：前端对接生成模块（见 `frontend-react/docs/proj_progress.md`）；修复 `create` 缺少落库与返回导致的 500；**多文件改用非思考客户端**并强化输出契约；失败任务落盘模型原文；补充 6 个离线 pytest 用例
- **2026-09-15**：**Agent 框架阶段 0（异步任务骨架）**——`POST /api/generation/create` 改为 202 入队即返回；
  新增 Redis + arq 独立 worker 进程（`core/worker.py`）、`AgentStage` 阶段枚举与 `generation_task` 三列、
  worker 启动僵尸回收、前端改轮询 + 进度条；新增 23 个离线用例、`check_arq` 与 `check_http_e2e` 自检脚本、
  `docker-compose.yml`（redis + pgvector）；**Agent 框架重构总方案落盘 `docs/agent_refactor_plan.md`（v2）**
- **2026-09-15（补）**：阶段 1 前置条件就绪 —— Docker 起 `pgvector/pgvector:pg18`（容器 `wgp-pg`，`pg_isready` 通过）、
  `uv add "psycopg[binary]" alembic`；修正 `docker-compose.yml` 的 PG 18 数据卷路径
  （`/var/lib/postgresql/data` → `/var/lib/postgresql`，否则容器启动即崩）；
  修复前端 2 个既有 lint 报错（`react-hooks/set-state-in-effect`），`lint` / `tsc` / `build` 全通过
- **2026-09-15（Agent 框架阶段 1）**：引入 **PostgreSQL 双数据源基座** —— `app/core/pg_config.py` / `pg_db.py`、
  `app/models/agent/` 三表（会话 / 消息 / 内容源，PG 侧 `snake_case` + `timestamptz`）、
  `alembic.ini` + `app/alembic/`（首个迁移 `4e97ff4fef89`，建表 + `CREATE EXTENSION vector`，不建向量表）；
  **Harness 基座** —— `utils/weg_gen/file_store.py`（per-request 虚拟文件系统）、
  `agents/web/tools.py`（write/read/list，永不抛异常）、`agents/common.py` 的 `AgentTrace`；
  新增 `check_pg.py` 与 42 个离线用例（共 65 个）；`file_writer._safe_name` 提升为公开 `safe_name`；
  踩到并记录两个坑：**PG 18 数据卷路径**、**`alembic.ini` 必须纯 ASCII（locale 编码陷阱）**
- **2026-09-15（Agent 框架阶段 2）**：落地**意图路由 + 澄清对话** ——
  `agents/router/intent_router.py`（全 AI 结构化判定 + Python 侧 `apply_readiness_gate` 硬兜底）、
  `agents/chat/chat_agent.py`（只负责说话、不抽取槽位，与 router 职责分离）、
  `agents/state.py`（`RequirementSlots` 的 `need_persistence` 用三态 `bool|None`）、
  `utils/agent/alias.py`（别名正则带前后边界，邮箱不被误判；未解析附件显式占位）、
  `repositories/agent/`（PG 侧 session / message）、`services/agent_chat_service.py`、
  `api/agent.py`（`POST /api/agent/chat`、`GET /api/agent/session/{uuid}`）、
  Alembic 迁移 `eac80e932e7f`（`agent_session.draft_requirement`）；
  配套：`AgentStage` 增加 `CLARIFYING`，僵尸回收增加 `exclude_stages` 以跳过暂停态；
  新增 `check_agent_chat.py` 与 67 个离线用例（共 132 个）；
  修掉一个真 bug：`_SAFE_ALIAS_RE` 漏捕获组导致 `parse_alias_index` IndexError

## 4. 相关文档
- 问答记录：`docs/QA.md`（已积累 Q1–Q24）
- 生成模块设计约定：`docs/generation_module_design.md`（分层、agents 约定、task_uuid、接口命名、DeepSeek 接入约束、边界与已知取舍）
