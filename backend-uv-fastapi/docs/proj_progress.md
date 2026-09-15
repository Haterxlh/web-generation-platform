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

### 模块：Agent 框架（阶段 0~6）
- **状态**：进行中（阶段 0~6 已完成；阶段 7 新旧对照、阶段 8 前端对接与二期 RAG 见 `docs/agent_refactor_plan.md`）
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
  - **阶段 3（2026-09-15）**：`pytest` **293 个用例全绿**（132 → 新增 161，全程离线、不连模型/库/Redis）；
    `check_source_upload` 两段全通过 ——
    ① **解析层离线五项**：GBK `.txt` 正确解码（`encoding=gb18030`）、坏编码明确报错（不是乱码）、
    `.md` 骨架正确且代码块里的 `#` 不算标题、HTML 设计令牌六类齐全（`#0f172a`/`Inter`/`8px`/`@media×1`，
    人工核对与源文件一致）、扫描版 PDF 按预期报错、落盘字节与上传一致；
    ② **HTTP 全链路（真实模型）**：`.md` → `@doc1` / `role=content`（**要求进了 `constraints`，
    没有混进 `content_points`**）、`.html` → `@doc2` / `role=style` 且配色来自解析器实测值、
    **真实两页 PDF** → `@doc3` 解析成功、**GBK `.txt`** → `@doc4` 解析成功、
    扫描版 PDF → **HTTP 200 + `parse_status=failed`**（不是 5xx）、附件列表返回别名与文件名
  - **阶段 4（2026-09-15）**：`pytest` **335 个用例全绿**（293 → 新增 42，全程离线）；
    `check_rag` 两段全通过 ——
    ① **离线三态与安全边界六项**：`skipped` 时 provider **调用次数为 0**、桩 `miss` 自报"尚未接入"、
    `hit` 保留片段与 L1 来源层级、`user_id=0` 被拒绝、merge 的 `miss→uncertainty` 与
    `skipped→无提示` 分派正确、工厂返回桩且自报不可用；
    ② **真实模型判定**：通用需求（番茄钟）→ `need=False`（理由"通用功能页面，需求已完整写在输入中"）、
    指代私人资料（"按我们公司的品牌色和 VI 规范"）→ `need=True` 且
    `query='公司 品牌色 VI规范 产品名'`（**关键词而不是问句**），单次 0.97~0.98k in / 71~105 out
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
- **阶段 3 交付（2026-09-15）**：
  - **解析层（无 LLM，可整块离线单测）**：`app/utils/doc/`
    - `base.py`：`ParsedDocument`（正文 / `native_blocks` 天然分块 / 骨架 / 设计令牌 / 警告）+ `DocParseError`
    - `text_parser.py`：**编码回退 UTF-8 → GB18030 → 明确报错**（`.txt` / `.md`）；
      `decode_bytes()` 被 HTML 解析复用，保证同一文件从两个入口读出的结论一致
    - `html_parser.py`：bs4（标准库 `html.parser`，零额外依赖）→ 正文（先收集 `<style>` 再剥标签）+ 骨架 +
      **设计令牌**（配色/字体/字号/圆角/间距/布局）；重复与超长行内样式**直接丢弃**并记警告
    - `pdf_parser.py`：pypdf 按页抽文本；**无文本层（扫描版）明确报错**（阈值 20 字符）；
      加密 / 无页面 / 坏文件各自给出可执行的中文原因
    - `__init__.py`：`detect_source_type()`（**扩展名优先于 MIME**）、`parse_document()` 统一入口、
      `MAX_PARSE_BYTES`（10 MB）、`can_be_style_source()`（**解析器能力判据**）
  - **契约与角色否决权**：`app/agents/state.py` 新增 `StyleSpec` / `RequirementDigest` /
    `RequirementSource` / `FinalRequirement` 与共用的 `digest_one_line()`；
    `app/agents/source/role_policy.py`（纯函数）—— 模型可判 `style`/`both`，
    但**非 HTML、或没有设计令牌的 HTML 一律否决为 `content`**，并给出可执行的原因
  - **digest-agent**：`app/agents/source/doc_digest_agent.py` + 两个提示词
    （`doc_digest_system.md` / `doc_chunk_digest_system.md`）；
    分块按类型区分（`.txt` / `.md` 单次；PDF / 超长 HTML 走 map-reduce，块预算 4000 字符，
    超 12 块时**保留首尾 + 显式警告**）；**设计令牌由 Python 覆盖**（模型只写 `style_spec.notes`）；
    分层降级（单块失败跳过并记警告 → 全部失败退正文片段 → 归并失败用 Python 合并各段结果）
  - **存储与仓库**：`app/core/storage_config.py` 增加 `uploads_path`；
    `app/utils/agent/source_store.py`（**展示名与磁盘名分离**：磁盘固定 `source{白名单后缀}`，
    中文名/空格/`../` 都无法逃出目录）；
    `app/repositories/agent/source_repository.py`（`list_aliases` **刻意包含已删除行** —— 别名不可复用）
  - **接口**：`POST /api/agent/source/upload`（multipart）与 `GET /api/agent/source/list`；
    `app/services/source_service.py`（**先入库再解析**：`pending → parsing → success/failed`；
    别名冲突靠 `UNIQUE(session_id, alias)` + 重试；解析/理解失败返回 `parse_status=failed` 而非 5xx）
  - **merge 节点（阶段 3 实现、阶段 6 才入图）**：`app/agents/merge/requirement_merge.py` +
    `requirement_merge_system.md` —— 四来源按优先级冲突消解 → `FinalRequirement`；
    `sources` 证据与风格令牌由 **Python** 生成；`use_document_style=false` 时**真的不注入**文档令牌；
    槽位"只增不减"（复用 `RequirementSlots.merged_with` 语义）
  - **配套**：`.gitignore` 增加 `uploads/` 与 `**/.pytest_tmp/`；新增 `tests/conftest.py`
    把 `tmp_path` 重定向到工作区内（本机沙箱下系统临时目录不可写）；
    新增 `check_source_upload.py` 自检脚本（离线解析段 + 真实 HTTP/模型段）；
    顺手修掉 `check_agent_chat.py` 在 GBK 控制台打印 emoji 时 `UnicodeEncodeError` 崩溃的问题
  - **偏差 / 计划外增补**（均已在上方写明理由）：
    1. **新增 `GET /api/agent/source/list`**（计划里只有 upload）—— 消息正文只存 `@doc1`，
       没有它前端无法把别名渲染成文件名 chip，附件会"传了但看不见"；
    2. `digest_one_line()` 上提到 `state.py`，`AgentChatService._digest_summary` 改为复用，
       避免"进提示词的摘要"与"给前端看的摘要"两套渲染规则漂移；
    3. `_load_attachment_targets` 不再读请求里的 `role`（以库里那一行为准）——
       否则前端可带 `role=style` 绕过 Python 的能力否决
- **阶段 4 交付（2026-09-15）**：
  - **契约**：`app/agents/state.py` 新增 `RagChunk`（`source_type` 区分 **L1 conversation / L2 document**）、
    `RagResult`（三态 `hit` / `miss` / `skipped` + query + reason + warnings + usage + `as_prompt_text()`）
  - **`need_rag` 节点（真实现）**：`app/agents/rag/need_rag.py` + `app/prompts/need_rag_system.md` ——
    一次结构化调用产出 `need` / `query` / `reason`；
    **Python 两处兜底**：① `need=true` 却没给检索词 → 用槽位/用户原话补一个可用的关键词串并记警告
    （放行空串会让二期检索必然空转，而用户看到的是"你的资料里没有"）；
    ② 调用失败 → 默认判 **不需要**（`on_failure_need=False`，一处可配置的显式决策）并记降级
  - **retriever 接口 + 桩 provider**：`app/agents/rag/retriever.py` ——
    `RetrieverProvider` 协议要求 provider **自报可用性**（`unavailable_reason`：None=能查，
    非 None=查不了），因此桩返回的 `miss` 能区分"未接入"与"真的没查到"（措辞完全不同：
    前者用户该等我们做完，后者用户该补资料）；
    `skipped` 时**连 provider 都不构造、不调用**；`build_retriever_provider()` 是**二期唯一切换点**
  - **merge 契约升级**：`merge(..., rag_context: str)` → `merge(..., rag: RagResult | None)` ——
    `hit` → 留下 rag 证据（note 里带检索词与命中数）+ 片段进提示词；
    `miss` → **写入 `uncertainty`**（面向用户、带可执行动作"补充说明或直接上传文件"）；
    `skipped` → **什么都不加**（给每个通用页面挂一条"未命中"只会让用户学会忽略所有提示）；
    提示词"来源 4"段落按三态分别写明（miss 时明确禁止编造用户的事实）
  - **安全边界**：`retrieve(user_id, ...)` 的 `user_id` 是**必填位置参数、无默认值**，
    且函数入口拒绝非正整数（硬约束 4：pgvector 查询漏过滤 = A 能检索到 B 的私人文档），
    并有回归用例断言 user_id 原样透传到 provider
  - **自检与测试**：新增 `check_rag.py`（离线三态/安全边界六项 + 真实模型判定两例）、
    `tests/test_need_rag.py`、`tests/test_retriever.py`，并向 `test_requirement_merge.py` 追加 RAG 三态用例
  - **偏差 / 计划外增补**：
    1. `RagResult` 增加 `warnings` 字段（与其它节点对齐）：provider 返回空片段、
       检索能力不可用这类信息不该被吞掉；
    2. `need_rag` 的降级方向做成**参数**（`on_failure_need`）而不是硬编码：
       一期默认"不需要"（失败却报"需要"会立刻变成一条误导用户的告警），
       二期若要"宁可多查一次"只需改这个参数；
    3. 顺带修掉 `test_requirement_merge.py` 中两处把**系统提示词**也拼进断言的写法 ——
       提示词为解释规则同样会提到"来源 4"，会让"位置顺序"断言失去意义（改为只检查用户消息）
- **阶段 5 交付（2026-09-15）**：
  - **`StageBudget`（阶段 1 刻意推迟的那一块）**：`app/agents/common.py` 新增
    `StageBudget`（`max_steps` / `max_output_tokens` / `max_files`）、`BUDGET_BY_DIFFICULTY`
    （easy 6 步/12k/1 文件、medium 12 步/40k/4、hard 20 步/80k/8）、`budget_for()`、
    `difficulty_for_file_count()`、`resolve_difficulty()`；
    **难度只能上调、不能低报**（低报会让循环被预算掐断，高报只是多花钱），
    且档位阈值与预算**同表派生**，不会出现"判成 hard 却按 easy 给预算"
  - **契约**：`app/agents/state.py` 新增 `PlannedFile`（`name` / `role` 枚举
    `markup|style|script|data|asset` / `depends_on` / `summary`）与 `FilePlan`
    （`difficulty` / `entry_file` / `files` / `tech_constraints` / `assets` + `names()` / `as_prompt_text()`）
  - **plan-agent**：`app/agents/plan/plan_agent.py` + `app/prompts/plan_agent_system.md` ——
    输入 `FinalRequirement` → 结构化 `FilePlan`（一次调用）；
    **Python 四道关**：① 文件名逐个过 `safe_name()` 白名单，非法名丢弃并记警告；
    ② 同名合并、`depends_on` 悬空依赖剔除（保留文件本身）；③ 入口不在清单里则改用第一个
    markup 文件；④ 全部非法或调用失败 → **启发式兜底计划**（单文件 `index.html`，难度 easy）并标降级
  - **`generation_plan` 表**（PG，Alembic `b7c1d2e3f4a5`）：存推理产物（`final_requirement` /
    `file_plan` JSONB）与**「预估 vs 实际」对照字段**；`app/models/agent/generation_plan.py`、
    `app/repositories/agent/plan_repository.py`（`create` / `get_by_uuid` / `get_latest_by_task_uuid` /
    `list_recent` / **`mark_outcome()`** 回填实际值）
  - **按你追加的要求，每个任务同时记录两组数据**（用于后续优化提示词）：
    - 预估侧：`difficulty_declared`（**模型原始声明**）/ `difficulty`（Python 复核后）/
      `planned_file_count` / `budget_steps` / `budget_output_tokens` / `validation_warnings`
    - 实际侧：`actual_steps`（web-agent 真正的工具调用步数）/ `actual_file_count` /
      `outcome_status` / `finished_at` —— 由**阶段 6 的生成循环结束时**调用 `mark_outcome()` 回填
    - 两侧同一行：`list_recent()` 一次查询就能出对账表，不必跨库拼两次查询
  - **偏差 / 计划外增补**：
    1. `FilePlan` 增加 `entry_file`（计划里没写）：预览必须知道打开哪个文件，
       且它必须在清单里 —— 否则"多页面"计划的预览会打不开任何东西；
    2. `resolve_difficulty()` 对"模型没给/给了无效难度"的处理是**按文件数推算并留痕**，
       而不是套用默认档位 —— 否则一个拼错的字符串会静默把预算压到 medium；
    3. `mark_outcome()` 显式刷新 `update_time`：本表没有 ON UPDATE，
       `server_onupdate` 不会出现在 UPDATE 语句里（与 `generation_task` 同一教训）；
    4. 自检脚本对 PG 的写入采用**物理删除**收尾（与业务无关，不必留逻辑删除标记）
  - **验证情况**：`pytest` **387 个用例全绿**（335 → 新增 52，全程离线）；
    `check_plan` 三段全通过 —— ① 离线六项（非法名丢弃 / 难度上调 / 全非法兜底 / 悬空依赖 /
    预算递增 / 兜底契约）；② **真实模型规划质量**：简单需求（待办清单）**3 次文件清单完全一致**
    （`index.html`+`style.css`+`script.js`，medium），复杂需求（企业官网四页 + 数据文件）3 次均为
    hard、7 个文件（仅数据文件名在 `products.json`/`data.json` 间波动）；③ **PG 往返与对账**：
    写入 → 读回 JSONB 保真 → 回填实际值 → `update_time` 刷新 → 打出对账表，结束后清理无残留
- **待办与遗留**：
  - ✅ **`npm run lint` / `tsc -b` / `npm run build` 已于 2026-09-15 全部通过**；此前 2 个 `react-hooks/set-state-in-effect` 报错（位于
    `hooks/AuthProvider.tsx` 与 `pages/Projects/ProjectsPage.tsx`）已修复，详见上方"本轮修复（2026-09-15）"
  - 阶段 0 验证在库里留下 1 个临时账号 `e2e9754b32245` 与 1 条任务（如需清理请手动处理）
  - 进度只到"阶段级"，尚无 SSE 实时推送（当前靠前端轮询，间隔 1500ms）
  - 产物落本地磁盘 + worker 独立进程 = **单机假设**；将来 worker 与 API 分机器部署必须换对象存储
  - API 与 worker 需**分别启动**，目前仅靠 `USEFUL_COMMAND.md` 说明，未做进程守护 / 一键脚本
  - **阶段 3 遗留**：
    - `POST /api/agent/source/upload` 是**同步**接口：理解长文档会串行多次模型调用
      （块预算 4000 字符、上限 12 块），耗时随篇幅增长；阶段 6 接进 worker 编排图后应改为异步
    - **附件删除 / 重传 / 重新解析接口未做**（只上传与列表）：`is_delete` 与 `parse_status` 列已就绪，
      且 `list_aliases` 已按"别名不复用"实现，补接口时不必改数据模型
    - **merge 节点已实现并单测，但尚未入图**（按计划到阶段 6；阶段 4 的 RAG 结果也是它的输入之一）
    - 上传附件目前**只落本地磁盘**（`uploads/`），与产物同一"单机假设"：将来换对象存储要一起迁移
    - 自检与端到端验证在库里留下若干临时账号（`agent*` / `src*`）与其会话/附件行，
      以及 `backend-uv-fastapi/uploads/{user_id}/...` 下的文件（已被 gitignore，如需清理请手动处理）
    - `check_source_upload.py` 的 PDF 造数（`_text_pdf`）与 `tests/test_doc_parsers.py` 的
      `_build_text_pdf` 是两份等价实现：自检脚本刻意不 import 测试代码，若将来 PDF 造数逻辑变化需同步改两处
  - **阶段 4 遗留**：
    - **检索本身仍未实现**（一期就不做）：没有向量表、没有 embedding、没有 pgvector 查询 ——
      二期要做的全部工作是把 `build_retriever_provider()` 换成真实现（华为云 BGE-M3 + pgvector），
      图结构与提示词骨架不需要动
    - **`need_rag` 是每轮生成路径各一次模型调用**（约 1k in / 100 out）：只在生成路径（阶段 6 的 worker）里跑，
      对话路径不跑；若将来嫌贵，可先按"会话内是否出现过疑似私人指代"做前置筛（代价是判定口径会出现两处）
    - **L1/L2 的入库与保留策略仍是空白**：`agent_message.is_memorable`（阶段 2 已就位）只是标记，
      二期的 `knowledge_chunk.source_type` 必须与 `RagChunk.source_type` 对齐，否则检索结果无法按层级调权
    - 一期**无法端到端验证检索质量**（`hit` 只能用假 provider 跑通）—— 刻意接受的范围限制
    - merge 已支持 `rag` 三态，但**尚未入图**（阶段 6 orchestrator 才把 `need_rag → retrieve → merge` 串起来）
  - **阶段 5 遗留**：
    - **plan-agent 尚未入图**（按计划到阶段 6：`merge → plan → web-agent ReAct 环 → gate`）；
      `StageBudget` 也还没有真实消费者 —— 阶段 6 的 web-agent 必须真的按 `max_steps` /
      `max_output_tokens` 刹车，否则难度依旧只是标签
    - `generation_plan` 的**写入与回填都在阶段 6**：本阶段的 repository 只被自检脚本使用，
      这是刻意的（与阶段 1 推迟 `StageBudget` 同一考量：不留无人使用的代码）
    - **难度判定的长期准确性待观察**：当前只跑了两类需求各 3 次；
      "模型是否长期偏保守地判 medium"要靠阶段 6/7 的真实数据（对账表就是为这个准备的）
    - 复杂需求的**数据文件名会在 `products.json` / `data.json` 之间波动**：
      不影响交付完整性（门禁只认"清单里的文件是否都写了"），但若将来要做产物对比，需要先归一
    - 兜底计划固定为单文件 `index.html`：对"多页面需求但规划失败"的场景只能给出一个页面，
      属于已知取舍（生成链路不阻塞优先于兜底质量）
  - **阶段 6 遗留**：
    - ✅ **端到端 HTTP 段已跑通**（2026-09-15，重启 worker 后）：
      `queued → retrieving → planning → generating → done`、预览 HTTP 200、对账两侧齐全。
      注意轮询间隔 2s 时**可能看不到 `routing` 那一格**（它只持续约 1 秒）——
      这是轮询粒度的正常现象，不是阶段没上报
    - ⚠️ **`gen_type="agent"` 依赖"worker 是新代码"**：FastAPI dev 会热重载，
      但 **arq worker 不会** —— 改了 worker 涉及的代码（agents/、services/）必须重启
      `arq app.core.worker.WorkerSettings`，否则 agent 任务会立刻失败并报"生成类型 agent 尚未实现"
    - **agent 模式没有写进前端**：当前只有 `/api/generation/create` 的 `gen_type` 支持它，
      前端仍是 single/multi（阶段 8 做会话式 Generate 页时一起改）
    - **循环中途不向用户提问**、**不做 LangGraph checkpoint 断点恢复**：都是刻意的 V1 边界
    - 本次验证留下若干临时账号（`wagent*` / `probe*` / `poll*`）与若干探针任务/产物
      （含 1 条因旧 worker 报"尚未实现"的失败任务、1 条成功的 `07fd418f…`），如需清理请手动处理
- **阶段 6 交付（2026-09-15）★主流程打通**：
  - **内层 ReAct 环**：`app/agents/web/web_agent.py` —— 手写 `StateGraph`
    （`model`(bind_tools) → 条件边 → `ToolNode` → 回 `model`）；每次生成新建 store + 新建图；
    用量逐轮累加；**三道刹车**（`StageBudget` 步数 / 输出 token / 连续无进展）；
    **门禁 = `store.missing(plan 清单)`**，不过则带"缺件清单"**定向补缺（最多 2 轮）**，仍不过则失败；
    `tool_wrapper` 注入点用于**故障注入**（自检验证"工具报错后能否改正"）；
    `on_step` 回调推进阶段；trace 只记轮次与文件名、**不记文件正文**
  - **提示词改写**：`app/prompts/web_agent_system.md` —— **去掉硬编码交付清单**，
    只留角色 + 工具语义 + 收工纪律（清单改由 `FilePlan` 注入消息；单测断言提示词里不再出现 `index.html`）
  - **外层编排图**：`app/agents/orchestrator.py` —— `ROUTING →（DIGESTING）→ RETRIEVING →
    PLANNING(含 MERGE) → GENERATING → GATE`；**纯函数**（不碰库、不落盘），
    通过 `on_stage` / `on_plan` 回调把阶段与规划交给 service；
    完备度不足时停在 `clarifying`（暂停等人，**不标 failed**）；四个节点链全部可注入（离线测试的前提）
  - **接线与落库**：新增 `gen_type="agent"`（+ 可选 `session_uuid`，用于带附件）、
    `app/services/agent_generation_service.py`（阶段推进、写 `generation_plan` 预估侧、
    循环结束 `mark_outcome()` 回填实际值、落盘产物与 trace、三种终态各自的处理）；
    `execute_pipeline` 增加 agent 分支（局部 import 避免与服务层成环）；
    `generation_task` 新增 `sessionUuid` 列 + `sql/scripts/alter_generation_task_session.sql`
  - **偏差 / 计划外增补**：
    1. **MERGE 不单独上报阶段**（并入 PLANNING 明细）：`AgentStage` 没有 MERGING，
       新增枚举值会要求前端同步改类型 —— 留到阶段 8 前端对接时一起做；
    2. **`tool_wrapper` 而不是直接注入 tools**：工具必须绑定**本次请求的 store**，
       外部传一个绑定别的 store 的工具集会让产物写进游魂 store、门禁永远不过
       （自检第一版就这么错过，现象看起来像"模型不会写文件"）；
    3. **V1 不做"回到 ⑫ 重规划"**：门禁不过时优先**定向补缺**（保留已写文件），
       重规划要重跑 plan 并丢掉已写文件，代价高且没有数据支持它更有效 —— 留给阶段 7 用实测决定
  - **验证情况**：`pytest` **438 个用例全绿**（387 → 新增 51，全程离线）；
    `check_web_agent` 四段 —— ① 离线（门禁拦 1/3、故障注入后改正、步数刹车保留产物）全通过；
    ② **真实模型三层自主性 + 思考/非思考对照**（用同一份清单、同一故障注入）：

    | 客户端 | 门禁 | 步数 | 输入 token | 输出 token | 思考 token | 耗时 |
    |---|---|---|---|---|---|---|
    | 思考模式 | ✅ | 3 | 13462 | 6585 | 217 | 20.0s |
    | 非思考模式 | ✅ | 4 | 19433 | 7614 | 0 | 22.1s |

    两者都"自主调工具 + 自主拆解 + 看懂注入的错误并改正"；**思考模式更省**（输出少 1029、步数少 1），
    故 `DEFAULT_THINKING` 保持 `True`（**默认客户端由这次数据确定**）；
    ③ **端到端 HTTP 段（worker 重启后已跑通）**：
    `create(agent)` → 202 / `queued` → 轮询到 `retrieving(40%) → planning(55%) → generating(70%) → done(100%)`
    → `status=success`、耗时 19246ms、产物 `['index.html']` →
    预览 `GET /preview/13/<task_uuid>/index.html` **HTTP 200（10928 字符）** →
    对账行 = 预估 `easy / 1 文件 / 预算 6 步` ↔ 实际 `2 步 / 1 文件 / success`；
    ④ **进程内流水线**（真模型 + 真 MySQL/PG + 真落盘）通过 ——
    `status=success` / 阶段 `done` / 19.8s / in=13120 out=4917 /
    产物 `['index.html']` 落盘且 trace 落盘 /
    对账行：预估 `easy, 1 文件, 预算 6 步` ↔ 实际 `2 步, 1 文件, success`，
    结束后任务行、规划行、产物目录全部清理

## 2. 项目级约定（跨模块通用）
- 后端分层调用方向：`api → services → repositories → 数据库`，禁止跨层调用；LLM 编排统一放 `agents/`
- 依赖管理：uv（依赖变更后提交 `uv.lock`）
- 敏感信息一律走 `.env`（`.env` 不入 git）；`.env` 的定位与读取规则只在 `app/core/settings_base.py` 定义
- 接口出入参使用 Pydantic 模型校验，字段带中文 `description`（保持 /docs 可读）
- 数据库操作一律 ORM/参数化，禁止拼接 SQL；只连本地业务库
- 若接入 MySQL：绝不操作 `mysql`、`sys`、`performance_schema` 等系统库
- docstring 采用 Google 风格
- `app/utils/` 按用途分子包（`db` / `jwt` / `weg_gen` / `agent` / `doc` / `utils_check`），不再平铺新文件
- **文档解析的统一口径**（阶段 3 起）：文本类编码按 **UTF-8 → GB18030 → 明确报错** 回退；
  类型判断**扩展名优先于 MIME**；`app/utils/doc/__init__.py` 是"支持哪些类型 / 谁能当风格源"的唯一真源
- **附件别名**：形如 `@docN`，作用域是会话（`UNIQUE(session_id, alias)`），**不可重命名、不可复用**；
  消息里只存别名，**绝不存文件正文或磁盘路径**；别名进 prompt 前必须过白名单校验
- **上传附件与生成产物分开存放**（`uploads/` ↔ `generated/`），两者都已 gitignore；
  上传文件的**展示名（原名）与磁盘名（`source{后缀}`）分离**，磁盘名永远由后端决定
- **推理产物落 PG**（`generation_plan`）：`FinalRequirement` / `FilePlan` 不塞进 MySQL 的
  `generation_task`；跨库只靠 `task_uuid` 在 service 层组装，**不 JOIN**
- **难度→预算的唯一真源**在 `app/agents/common.py` 的 `BUDGET_BY_DIFFICULTY`
  （步数 / 输出 token / 文件数上限），且 `resolve_difficulty()` **只允许上调难度**
- **每次生成都要留下「预估 vs 实际」**（同一行）：预估 = 模型声明难度 / 复核后难度 /
  计划文件数 / 预算步数与 token；实际 = 实际步数 / 实际文件数 / 结果 / 完成时间 ——
  这是后续优化提示词与新档位调参的证据来源（`check_plan.py` 的对账表即读它）
- **agent 模式（`gen_type="agent"`）的三条边界**：循环中途不向用户提问（信息不足只在 ROUTING 判）、
  完成判定权在 Python 侧（`store.missing`，模型说"我完成了"不算数）、
  每次生成新建 store 与新图（**绝不做模块级全局变量**）
- ⚠️ **改了 worker 代码必须重启 arq worker**（它不热重载；FastAPI dev 会）：
  否则新 `gen_type` 会在旧 worker 上报"尚未实现"

## 3. 下一步计划（按优先级）
- [x] **Agent 框架阶段 2**（已完成 2026-09-15，见上方模块进度）
- [x] **Agent 框架阶段 3**（已完成 2026-09-15）：文档解析 + digest-agent + 上传接口 + merge 节点
      （merge 已实现并单测，**阶段 6 才入图**；见上方模块进度）
- [x] **Agent 框架阶段 4**（已完成 2026-09-15）：个人 RAG 占位 —— `agents/rag/need_rag.py`（真实现）、
      `agents/rag/retriever.py`（接口 + 桩 provider）、`merge` 的 `rag` 三态入口；
      **二期只替换 provider**（见上方模块进度）
- [x] **Agent 框架阶段 5**（已完成 2026-09-15）：plan-agent —— `StageBudget`（难度→步数/token/文件数）、
      `FilePlan` 契约、`agents/plan/plan_agent.py`、`generation_plan` 表（含**预估 vs 实际**对照字段）
      与迁移 `b7c1d2e3f4a5`（见上方模块进度）
- [x] **Agent 框架阶段 6（★主流程打通）**（已完成 2026-09-15）：`web_agent.py`（ReAct 环 + 三道刹车 +
      门禁 + 定向补缺）、`orchestrator.py`（外层装配图）、`web_agent_system.md`、
      `gen_type="agent"` 与 `agent_generation_service.py`（落库 + 回填实际值 + 落盘 trace）；
      ⚠️ **需重启 arq worker 后**才能通过 HTTP 端到端（见上方遗留）
- [ ] **Agent 框架阶段 7（新旧对照 + 退役判断）**：用**同一需求**分别跑
      `single` / `multi` / `agent` 三种模式，记录并对比**成功率 / 产物完整度 / 总 token / 耗时**；
      同时用 `generation_plan` 的"预估 vs 实际"数据校正难度档位与提示词
      —— **只有数据支持才退役旧实现**，否则回退并重新评估（归属：Agent 框架）
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
- **2026-09-15（Agent 框架阶段 3）**：落地**文档解析 → 理解 → 上传**与 merge 节点 ——
  `utils/doc/`（`base` / `text_parser` / `html_parser` / `pdf_parser` / 统一入口，**无 LLM**；
  `.txt` 走 UTF-8→GB18030 回退、扫描版 PDF 明确报错、HTML 抽设计令牌）、
  `agents/source/role_policy.py`（只有 HTML 能当风格源，非 HTML 的 style 判定被 Python 否决）、
  `agents/source/doc_digest_agent.py`（分块理解 + map-reduce；**设计令牌由 Python 覆盖**，模型只写 notes）、
  `agents/state.py`（+`StyleSpec` / `RequirementDigest` / `RequirementSource` / `FinalRequirement` / `digest_one_line`）、
  `agents/merge/requirement_merge.py`（四来源冲突消解；**阶段 6 才入图**）、
  `utils/agent/source_store.py`（展示名与磁盘名分离）、`repositories/agent/source_repository.py`、
  `services/source_service.py`、`api/agent.py`（`POST /source/upload` + `GET /source/list`）、
  三个提示词；新增 `check_source_upload.py` 与 **161 个离线用例（共 293 个）**；
  配套：`.gitignore` 增加 `uploads/`、`tests/conftest.py` 重定向 `tmp_path`、
  修掉 `check_agent_chat.py` 在 GBK 控制台打印 emoji 崩溃的问题
- **2026-09-15（Agent 框架阶段 4）**：落地**个人 RAG 占位**（真判定、假检索）——
  `agents/state.py`（+`RagChunk` / `RagResult`，三态 `hit`/`miss`/`skipped`，`source_type` 区分 L1/L2）、
  `agents/rag/need_rag.py`（真实现：结构化判定 + 两处 Python 兜底）、
  `app/prompts/need_rag_system.md`、
  `agents/rag/retriever.py`（`RetrieverProvider` 协议 + **自报可用性**的桩 provider + 二期唯一切换点
  `build_retriever_provider()`；`user_id` 必填且拒绝非正数）、
  `merge` 的入口从 `rag_context: str` 升级为 `rag: RagResult | None`
  （`hit`→证据+片段、`miss`→uncertainty、`skipped`→静默）；
  新增 `check_rag.py` 与 42 个离线用例（共 335 个）
- **2026-09-15（Agent 框架阶段 5）**：落地**交付规划与难度预算** ——
  `agents/common.py`（+`StageBudget` / `BUDGET_BY_DIFFICULTY` / `budget_for` /
  `difficulty_for_file_count` / `resolve_difficulty`，难度只上调）、
  `agents/state.py`（+`PlannedFile` / `FilePlan`，含 `entry_file` 与 `as_prompt_text()`）、
  `agents/plan/plan_agent.py` + `app/prompts/plan_agent_system.md`
  （文件名单白名单校验、悬空依赖剔除、入口回退、启发式兜底计划）、
  `models/agent/generation_plan.py` + Alembic `b7c1d2e3f4a5` + `repositories/agent/plan_repository.py`
  （含 `mark_outcome()` 回填实际值与 `list_recent()` 对账查询）、
  新增 `check_plan.py` 与 52 个离线用例（共 387 个）
- **2026-09-15（Agent 框架阶段 6）★主流程打通**：落地**内层 ReAct 环 + 外层编排图 + 接线** ——
  `agents/web/web_agent.py`（LangGraph `StateGraph`：model ⇄ ToolNode；三道刹车；
  **门禁 = `store.missing(plan 清单)`**；定向补缺最多 2 轮；`tool_wrapper` 故障注入点；
  trace 不记正文）、`app/prompts/web_agent_system.md`（**去掉硬编码交付清单**）、
  `agents/orchestrator.py`（需求装配图，纯函数 + 阶段/规划回调；信息不足停在 clarifying）、
  `services/agent_generation_service.py`（阶段推进 + 写 `generation_plan` 预估侧 +
  `mark_outcome()` 回填实际值 + 落盘产物与 trace + 三态终态）、
  `gen_type="agent"` + 可选 `session_uuid`、`generation_task.sessionUuid` 列与 DDL 脚本；
  新增 `check_web_agent.py` 与 51 个离线用例（共 438 个）；
  **默认客户端由实测确定**：思考模式 6585 输出 token / 3 步，非思考 7614 / 4 步（两者都通过门禁）；
  **端到端 HTTP 段于同日 worker 重启后补跑通过**：
  `queued → retrieving → planning → generating → done`、预览 HTTP 200（10928 字符）、
  对账 预估 `easy,1 文件,6 步` ↔ 实际 `2 步,1 文件,success`（19246ms）

## 4. 相关文档
- 问答记录：`docs/QA.md`（已积累 Q1–Q24）
- 生成模块设计约定：`docs/generation_module_design.md`（分层、agents 约定、task_uuid、接口命名、DeepSeek 接入约束、边界与已知取舍）
