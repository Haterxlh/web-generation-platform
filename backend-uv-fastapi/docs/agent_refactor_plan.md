# Agent 框架重构计划 —— backend-uv-fastapi

> 本文件是**实施计划与验收清单**，随步骤推进逐项打勾。它回答"**怎么改**"；
> "改完后的最终约定"在全部完成后回写到 `docs/generation_module_design.md`。
>
> **最近更新时间：2026-09-15**
>
> **版本说明**
> - **v2（本版）**：范围从"generation 模块工具化"扩展为**平台级 Agent 框架** ——
>   意图识别 → 对话澄清 → 文档解析 → 个人 RAG → 规划 → 工具调用生成。
> - v1 中**已经实测验证过的结论原样保留**（见 §2 实测、§3.7 虚拟文件系统与铁律），
>   未经验证的部分已重写。v1 的步骤 1/2 被并入本版**阶段 1**。

---

## 0. 结论先行

### 0.1 一句话定位

**现状不是 Agent，是"固定流水线 + 正则解析"。**
本次改造要把它变成真正的 **Agent = LLM + Harness**：模型自主决策、调用工具、观察结果、纠错闭环；
而 Harness 负责编排、记忆、门禁、可观测与降级。

### 0.2 范围

| 期 | 范围 | 完成线 |
|---|---|---|
| **一期（阶段 0~8）** | 异步骨架 → 双数据源 → 意图/对话 → 文档解析 → **RAG 占位** → 规划 → web-agent 工具调用 → 新旧对照 → 前端 | 阶段 6 端到端跑通 + 阶段 7 对照达标 |
| **二期** | **个人 RAG 真实现**（华为云 BGE-M3 + pgvector + L1/L2 分层） | 独立增量，不阻塞一期 |

### 0.3 决策锁定（2026-09-15 确认，实施时不得擅自变更）

| # | 决策项 | 结论 |
|---|---|---|
| 1 | 异步化 | **做**，作为阶段 0 前置条件 |
| 2 | RAG 检索方式 | **PostgreSQL + pgvector**（Docker 镜像）；embedding 用**华为云 ModelArts Studio 的 BGE-M3**（OpenAI 兼容，1024 维）；**真实现推迟到二期** |
| 3 | 文件清单来源 | **plan 定文件集合 + 强制走 `file_writer._safe_name` 白名单校验** |
| 4 | 旧实现（single / multi） | **保留**为 `simple` 模式兜底，阶段 7 用数据决定是否退役 |
| 5 | PostgreSQL 定位 | **双库并存**：MySQL 存业务（user / generation_task），PG 存对话与知识库 |
| 6 | 附件别名形态 | **系统生成 `@doc1` + `display_name` 展示原文件名** |
| 7 | 异步执行器 | **Redis + arq**（独立 worker 进程）；**Redis 只做队列，任务状态真源在 MySQL**；`max_tries=1` 不自动重试 |
| 8 | 教学模式 | 每个阶段先讲规划与前置条件，用户确认前置条件完成后，助手再编写代码 |

---

## 1. 现状诊断：为什么现在不算 Agent

| 看得像 Agent 的东西 | 实际是什么 | 差距 |
|---|---|---|
| `app/agents/` 目录名 | 只是命名 | 内部是两条写死的流程，没有 LLM 编排 |
| `multi_file_graph.py` | `START → plan → generate → validate`，**边全部硬编码** | 唯一分支 `route_after_validate` 的判据是纯 Python 正则，**不是模型决策** |
| `single_html_flow.py` | LCEL 链 `_build_messages \| llm` | 一次调用、一个产物，连分支都没有 |
| `POST /api/generation/create` | 同步阻塞到生成结束 | 实测复杂需求已 **157 秒**；再叠意图/解析/RAG/规划会到 3~5 分钟，前端必然超时 |
| 任务表 `generation_task` | 只有 `running/success/failed` | 没有**阶段**概念，长流程无法向用户汇报进度 |
| 需求来源 | 只有 `prompt` 一个字段 | 没有会话、没有附件、没有知识库，**无法承载多来源需求** |

### 1.1 三条本质差距

1. **没有决策权**：`_build_user_message()` 里那句
   "交付清单（缺一不可，按此顺序输出）：1. html 2. css 3. js"
   —— **我们替模型做了决定**。模型没有任何选择空间。
2. **没有感知**：模型"行动"的产物被 `extract_multi_files()` 正则抽取后，
   **模型永远看不到抽取结果**，因此无法据此判断下一步该做什么。
   它不是"智能体"，它是"文本生成器 + 事后解析器"。
3. **纠错不是闭环**：失败重试 = 把错误文本拼回 prompt 再整批重生成，
   等价于"重新抽一次彩票"。这正是实测中"三次分别缺 js / 缺 css+js"的根因 ——
   三个文件的交付被当成**一个不可分割的赌注**押在一次生成上。

> 旁证：进度文档里那条"可选改造为**定向补缺**"，本质上就是在往 Agent 方向摸。

---

## 2. 前置实测结论（**已验证，勿重做**）

### 2.1 工具调用探针：思考模式可用（v1 保留）

同一需求，只切换思考开关，均 `bind_tools([write_file])`：

```
思考模式   llm_client              → tool_calls 正常，finish_reason=tool_calls
非思考模式 llm_no_thinking_client  → tool_calls 正常，finish_reason=tool_calls
```

**结论：思考模式下的"自然工具调用"可用。**

`docs/generation_module_design.md` §7.3 记的"思考模式不支持工具"**只适用于强制
`tool_choice`**（`required` / 指定某个函数），而 `with_structured_output` 正是靠强制才踩到该限制。
现象与上游 [DeepSeek 工具调用限制 issue #1376](https://github.com/deepseek-ai/DeepSeek-V3/issues/1376) 一致。

**对既有决策的影响**：`llm_no_thinking_client` 存在的理由是"多文件单次生成时思考模式把 94% 预算
花在思考里"。**该理由在 Agent 模式下不再成立** —— 产物不再依赖"单次输出三个完整代码块"，
改由多次工具调用逐个落库，思考模式的预算压力被天然摊薄。因此 `llm_client`（思考模式）
重新成为候选，`llm_no_thinking_client` 降级为备选，**由阶段 7 的对照数据决定**。

### 2.2 环境事实（2026-09-15 复核）

| 项 | 状态 | 影响 |
|---|---|---|
| `langgraph 1.2.11` / `langchain-core 1.6.3` | 已装（含 `ToolNode`） | 零新依赖即可手写 ReAct 环 |
| **`langchain_openai 1.6.2`** | **已装**（随 langchain-deepseek） | 接 OpenAI 兼容 embedding **零新依赖** |
| `openai` SDK | 已装 | 同上 |
| `torch` / `sentence-transformers` | **未装** | 本地 embedding 是 2~3GB 重依赖，一期不做 |
| MySQL | 运行中，`wgp_db` 可连，`generation_task` 17 列 | 阶段 0 可立即开工 |
| PostgreSQL 18.6 | **已装但服务未运行、且无 pgvector** | 阶段 1 用 Docker 镜像解决 |
| Docker | 已装，**守护进程未运行** | 阶段 1 前需启动 Docker Desktop |
| Redis | **未装，用户可安装** | 采用 **arq v0.28.0**（async-native Redis 队列）；安装方式见 §2.2.1 |
| `mysql.exe` / `psql.exe` | 均在 PATH | DDL 可由用户直接执行 |

#### 2.2.1 Redis 安装（Windows 三选一）

| 方式 | 做法 | 备注 |
|---|---|---|
| **Docker（推荐）** | `docker compose up -d redis` —— 与阶段 1 的 pgvector 写在同一个 `docker-compose.yml` | 需先启动 Docker Desktop（当前守护进程未运行） |
| WSL2 | `wsl --install` → `sudo apt install redis-server` → `sudo service redis-server start` | 不动 Docker，但要额外装 WSL |
| Memurai | 原生 Windows 服务，安装即用 | 商业软件，开发版免费 |

验证：`redis-cli ping` 返回 `PONG`。

### 2.3 环境注意（沿用 v1 §7）

`uv run` 在本会话会因 uv 缓存目录在工作区外被沙箱拒绝而失败，
临时可用 `.venv\Scripts\python.exe -m pytest` 替代。属环境问题，非项目缺陷。

---

## 3. 目标架构

### 3.1 Agent = LLM + Harness：六件套落到本项目

这是本次改造的**核心卖点**，实施时每一件都要在代码里有明确对应物，不能只停留在概念。

| Harness 组件 | 本项目对应物 | 落位 |
|---|---|---|
| **① 编排（Orchestration）** | 外层需求装配图 + 内层 ReAct 环 | `agents/orchestrator.py`、`agents/web/web_agent.py` |
| **② 工具（Actuation）** | `write_file` / `read_file` / `list_files`；外围 `parse_document` / `retrieve_kb` | `agents/web/tools.py` |
| **③ 记忆（Memory）** | 会话 + 阶段产物持久化，跨轮次复用 | PG：`agent_session` / `agent_message` / `generation_source` |
| **④ 门禁（Verification）** | Python 侧完成判定、`_safe_name` 校验、步数/预算刹车、路径安全 | `agents/web/web_agent.py` 收工判定 |
| **⑤ 可观测（Observability）** | 每轮 tool_calls / 工具返回值 / token / 耗时 → trace | `agents/common.py` 的 `AgentTrace` + `_debug_trace.jsonl` |
| **⑥ 降级（Fallback）** | 每个可选阶段失败都不拖垮主链路 | 各节点内部兜底（沿用 `plan_node` 的既有写法） |

### 3.2 两段式嵌套编排

**不是"多智能体自由对话"**（贵且不可控）——一律用**固定图 + 结构化产物**传递。

```
POST /api/agent/chat ──→ ┌─────────────── 外层：需求装配图（LangGraph）───────────────┐
                         │                                                            │
                         │  START → normalize(规则) → intent_router                   │
                         │            │                                             │
                         │      ┌─────┴─────┐                                       │
                         │   chat│           │generate                               │
                         │      ↓           ↓                                       │
                         │  chat_agent   doc_digest?  （仅有附件时）                  │
                         │  (多轮澄清)      ↓                                        │
                         │      │        need_rag? ──hit──→ rag_retrieve?（二期）      │
                         │      │           │                  ↓                      │
                         │      │           └────────→ requirement_merge             │
                         │      │                          ↓                         │
                         │      │                       plan_agent                    │
                         │      │                          ↓                         │
                         │      │              ┌───────────────────────┐              │
                         │      │              │  内层：web_agent       │              │
                         │      │              │  model ⇄ ToolNode 环   │              │
                         │      │              │  （虚拟文件系统）       │              │
                         │      │              └───────────┬───────────┘              │
                         │      │                          ↓                          │
                         │      └──────────────→  gate(文件清单门禁) → END            │
                         └────────────────────────────────────────────────────────────┘
                                                    ↓
                                    {文件名: 内容} → service 落盘 + 落库
```

### 3.3 目录落位

```
backend-uv-fastapi/
├─ app/
│  ├─ agents/
│  │  ├─ common.py              # 扩展：+ AgentTrace / StageBudget（ModelUsage 保留）
│  │  ├─ state.py               # ★新：AgentState / ClarifiedRequirement / RequirementDigest / FilePlan
│  │  ├─ stages.py              # ★新：AgentStage 枚举 + 中文文案 + 进度映射
│  │  ├─ orchestrator.py        # ★新：外层需求装配图
│  │  ├─ router/intent_router.py    # 意图识别（规则优先 + LLM 结构化兜底）
│  │  ├─ chat/chat_agent.py         # 澄清对话 → ClarifiedRequirement
│  │  ├─ source/doc_digest_agent.py # 文档 → RequirementDigest（map-reduce）
│  │  ├─ rag/need_rag.py            # 是否需要检索（一期即做，真实输出）
│  │  ├─ rag/retriever.py           # ★接口 + 桩 provider（一期恒 skipped）
│  │  ├─ plan/plan_agent.py         # 结构化 FilePlan
│  │  ├─ web/tools.py               # 虚拟文件系统工具集
│  │  ├─ web/web_agent.py           # 内层 ReAct 环
│  │  ├─ single_html_flow.py        # 旧实现 → simple 模式兜底（保留）
│  │  └─ multi_file_graph.py        # 旧实现 → simple 模式兜底（保留）
│  ├─ core/
│  │  ├─ agent_config.py        # ★新：AgentSettings（并发/超时/重试/僵尸宽限）
│  │  ├─ redis_config.py        # ★新：Redis 配置 → arq RedisSettings
│  │  ├─ arq_pool.py            # ★新：arq 连接池（FastAPI lifespan 建/关）
│  │  ├─ worker.py              # ★新：arq WorkerSettings + job 函数 + 僵尸回收
│  │  ├─ pg_config.py           # ★新：PG 配置 Settings
│  │  └─ pg_db.py               # ★新：pg_engine / PgBase / get_pg_db()
│  ├─ models/
│  │  ├─ generation_task.py     # 改：+ stage / stageDetail / progress
│  │  └─ agent/                 # ★新：只 import PgBase（见 §5 硬约束 1）
│  │     ├─ agent_session.py
│  │     ├─ agent_message.py
│  │     └─ generation_source.py
│  ├─ prompts/
│  │  ├─ intent_router_system.md    # ★新
│  │  ├─ chat_agent_system.md       # ★新
│  │  ├─ plan_agent_system.md       # ★新
│  │  └─ web_agent_system.md        # ★新（**不含**交付清单）
│  ├─ utils/
│  │  ├─ agent/alias.py         # ★新：别名分配/解析/展开/校验（纯函数）
│  │  ├─ doc/                   # ★新：pdf_parser.py / html_parser.py（无 LLM）
│  │  └─ weg_gen/file_store.py  # ★新：虚拟文件系统（per-request）
│  └─ alembic/                  # ★新：PG 侧迁移（extension + 索引）
├─ sql/scripts/                 # DDL 脚本（沿用根 sql/ 约定）
├─ docker-compose.yml           # ★新：redis + pgvector(pg18) 两个服务
└─ docs/agent_refactor_plan.md  # 本文件
```

### 3.4 数据与存储边界（**硬约定**）

**MySQL 存业务**：`user`、`generation_task`（+ 阶段列）
**PostgreSQL 存对话与知识库**：`agent_session`、`agent_message`、`generation_source`（+ 二期的 `knowledge_chunk`）
**Redis 只做队列，不做真源**：arq 的 job 队列 + job 元数据（`_job_id` 入队去重）

> ⚠️ **Redis 绝不是真源。** 任务状态、阶段、进度、结果一律落 MySQL。
> 若状态只放 Redis，一次 `FLUSHDB`、一次内存淘汰、或没开持久化的一次重启，
> 用户界面就会永远停在"排队中"且无法排查。因此 `keep_result=0`（结果不存 Redis）。

**铁律：跨库不 JOIN，只靠 id 在 service 层组装。** 否则两个库的模型会互相渗透，最终无法分离。

**要认下的代价**（不写下来就是给自己埋雷）：

| 代价 | 应对 |
|---|---|
| 无跨库事务 | PG 建了 session、MySQL 建任务失败 → 会留孤儿 session。**用"允许孤儿 + 定期清理"代替分布式事务**，不硬凑 |
| 双连接池 / 双备份 / 双部署 | 接受，不做抽象层 |
| 两个 declarative Base 不能混注册 | 见 §5 硬约束 1（最阴的坑） |
| `create_all` 不够用 | PG 侧直接上 Alembic（长期待办里本就有这一项，一并用上） |

### 3.5 web-agent 的输入块优先级与预算（**关键**）

五个来源**不能并列拼装** —— 否则模型收到互相矛盾的需求会随机挑一份服从。
按优先级从高到低（冲突时上位覆盖下位）：

| 优先级 | 输入块 | 超预算时的裁剪顺序 |
|---|---|---|
| 1 | 系统提示词（工具语义 + 输出契约 + 安全边界） | **永不裁** |
| 2 | 用户本轮显式要求 | **永不裁** |
| 3 | plan 的 `FilePlan`（文件清单与职责） | 最后裁 |
| 4 | 文档 digest（内容素材 + 风格规范） | 先压缩成摘要 |
| 5 | RAG 检索结果（二期才有内容） | 再裁 |
| 6 | chat-agent 澄清摘要 | 最先裁 |

### 3.6 附件别名机制

**目的**：带文件的对话中，消息里用别名引用文件，**不把文件内容或路径塞进消息**。

**形态**：`alias` 由**系统生成**，形如 `@doc1`，`UNIQUE(session_id, alias)`；
`display_name` 存原文件名（`报告.pdf`），**只用于展示与宽松解析**。

> 为什么不直接用文件名当别名：文件名会重复（两个 `报告.pdf`）、含空格/中文/特殊字符、
> 可被恶意构造（`../../etc/passwd`），直接当引用 token 不安全也不稳定。

**消息里存什么**：

```
agent_message.content     = "用 @doc1 的风格重做，内容参考 @doc2"   ← 含别名的原文
agent_message.attachments = [{"alias":"@doc1","source_uuid":"…","role":"style","display_name":"设计规范.html"}]
```

**永不存文件正文、永不存磁盘路径。** 前端把 `@doc1` 渲染成 chip（悬浮显示文件名）。

**展开时机与形态**：只在 **prompt 装配时**展开，展开内容是 **digest 摘要（几百字），不是全文**：

```
【附件 @doc1｜设计规范.html｜角色：风格源｜摘要：深色背景 #0f172a，主色 #4f46e5，圆角 8px，无衬线字体…】
```

收益：**prompt 短**（省 token）、**不泄露路径**、**文件可重解析而历史消息无需改写**。

#### ⚠️ 3.6.1 最大的坑：`@` 是邮箱字符

`@` 在邮箱（`a@b.com`）和社交 handle（`@张三`）里是高频字符。
若对正文里所有 `@xxx` 都做"展开 + 找不到就报错"，**`联系我 a@b.com` 会被误伤**。

规则：

1. **只有精确命中会话内已注册 alias 的 `@token` 才展开**；未命中 → **原样保留、不报错**
2. alias 正则收紧为 `^doc\d+$`（**不含 `.` `/` `@`**），从格式上就与邮箱错开
3. "用户打错别名"（`@doc9`）**不能**靠正文正则发现，要靠 `attachments` 字段比对 ——
   只在**紧邻附件上下文的轮次**才提示

#### 3.6.2 其余四条规则

| 规则 | 内容 | 理由 |
|---|---|---|
| **不变性** | alias **不可重命名、不可复用** | 否则历史消息里的 alias 会悬空 |
| **失效处理** | 删除附件 → `is_delete=1`，回放显示 `@doc1（已失效）` | **不报错、不阻断历史回放** —— 这是"历史可回放"的前提 |
| **作用域** | alias 作用域 = **会话**；跨会话引用需显式挂载并**分配新 alias** | 避免别名全局唯一带来的分配难题 |
| **安全** | alias 进 prompt 前过白名单校验 | 防 `../`、防换行注入提示词 |

落位：`app/utils/agent/alias.py`（**纯函数：分配 / 解析 / 展开 / 校验**，不碰库、不碰 HTTP）→ 可单测。

### 3.7 虚拟文件系统的铁律（**v1 保留，必须遵守**）

**决策**：工具**不直接写磁盘**，写进一个 per-request 的内存字典；循环结束后由 service 落盘。

**理由**（三条，逐条都踩过坑）：

1. **保住分层与可测试性**：`agents/` 的既有硬约定（设计约定 §3.2）是
   "纯函数 —— 给它需求、还它 `{文件名: 内容}`；不落盘、不落库"。
   虚拟文件系统让这个契约**原样保留**，`service` 层改动最小，且能脱离 MySQL / FastAPI 测试。
2. **避免半成品产物**：直接写盘时，Agent 中途失败会在用户目录留下残缺文件。
   虚拟文件系统天然是"要么全交、要么全不交"。
3. **失败时仍可落盘原文**：循环结束后把虚拟文件系统的最终快照交给 service，
   失败时按现有 `write_debug_raw` 机制留证据。

> **铁律：它必须是 per-request 构造的，绝不能挂在模块级。**
> 若写成模块级全局变量，两个用户同时生成时，**B 会读到 A 的文件、甚至覆盖 A 的产物**。
> 这是本次改造里最隐蔽、也最危险的 bug。做法：每次生成调用一次
> `build_agent_tools()`，工具函数作为闭包捕获自己的 store 实例。

**替代方案（已否决）**：工具直接写磁盘 + 接收 `user_id/task_uuid`。
否决理由：会把 `agents/` 与存储路径耦合死，破坏"可脱离框架测试"这条既有优势。

---

## 4. 分阶段实施计划（一期）

> **每阶段的推进方式**：先讲规划与前置条件 → 用户确认前置条件完成 → 助手编写代码 → 验收。

### 阶段 0｜异步任务骨架（Redis + arq）★前置
- **目的**：把"同步阻塞 30~120 秒（未来 3~5 分钟）"变成"提交即返回 + 轮询阶段"，
  并让任务**不因 API 进程重启而丢失**
- **架构**（Redis 只做队列，不做真源）：

  ```
  FastAPI 进程                                  Worker 进程（arq）
  POST /create                                  arq WorkerSettings
    ├─ 建 generation_task (MySQL, queued)         ├─ 从 Redis 取 job
    ├─ enqueue_job(uuid, _job_id=uuid) ─────────→ ├─ 跑 pipeline（同步 LLM → to_thread）
    └─ 202 + queued                               ├─ 阶段推进写 MySQL（真源）
                                                  └─ 完成/失败写 MySQL
        前端轮询 GET /{task_uuid} ←────────────── 读 MySQL
  ```

- **产出**：
  - `app/core/agent_config.py`（`AGENT_MAX_JOBS` / `AGENT_JOB_TIMEOUT_SECONDS` / `AGENT_JOB_MAX_TRIES` / `AGENT_ZOMBIE_GRACE_SECONDS`）
  - `app/core/redis_config.py`（`REDIS_HOST/PORT/DB/PASSWORD` → arq `RedisSettings`）
  - `app/core/arq_pool.py`（arq 连接池，由 `lifespan` 建/关）
  - `app/core/worker.py`（`WorkerSettings` + `run_generation(ctx, task_uuid)` + 启动僵尸回收）
  - `app/agents/stages.py`（`AgentStage` 枚举 + 中文文案 + 进度映射）
  - `app/models/generation_task.py` +3 列：`stage` / `stageDetail` / `progress`
  - `sql/scripts/alter_generation_task_stage.sql`、`docker-compose.yml`
  - `app/services/generation_service.py` 改造（`create` 改为入队后立即返回）
  - `app/api/generation.py`（`create` 改 async + 202）+ `app/main.py`（`lifespan` 管理 arq 池）
  - `tests/test_task_pipeline.py`、`app/utils/utils_check/check_arq.py`
- **要点**：
  - **`status` 与 `stage` 是两个正交字段**：`status` 是生命周期（running/success/failed），
    `stage` 是进度（queued/routing/digesting/retrieving/planning/generating/done）。
    不要用一个字段表达两件事。
  - ⚠️ **必须设 `max_tries=1`**（arq 默认是 **5**）。arq 采用**悲观执行**：任务在成功/失败前
    **不会离开队列**，worker 中途关闭时任务会在重启后被重跑；`max_tries` 就是重跑上限。
    LLM 任务按 token 计费，自动重试 5 次 = 烧 5 次钱。
    **宁可标 `failed` 让用户手动重试，也不自动重跑。**
  - ⚠️ **job 参数只传 `task_uuid`，不传 payload**。arq 默认用 `pickle` 序列化 job；
    只传字符串既避开序列化风险，也让队列里的任务体保持最小。
  - ⚠️ **langchain 的 `.invoke` 是同步阻塞的** → job 函数内必须 `await asyncio.to_thread(...)`，
    否则会阻塞 worker 的事件循环，同一 worker 的其它任务全部卡住。
  - ⚠️ **worker 与 API 是两个进程**：worker 不能复用 API 的 `db` Session，
    必须自己 `MysqlSessionLocal()` 开（Session 非线程安全）。
  - **僵尸回收移到 worker `on_startup`**：Redis 队列解决了"重启丢任务"，
    但永久失败 / `_expires` 过期 / worker 崩溃留下的 `running` 记录仍需回收。
  - **阶段 0 不依赖 PG**：阶段列先建在 MySQL 的 `generation_task` 上。
  - **单机假设**：产物写本地磁盘，而 worker 是独立进程 —— 同机没问题，
    **未来 worker 与 API 分机器部署时必须换对象存储**（记入 §5）。
- **契约变更**：`POST /api/generation/create` 由"同步返回 success 结果"变为"**202 + queued**"，
  轮询复用已有的 `GET /api/generation/{task_uuid}`。
  ⚠️ **前端 `GeneratePage.tsx` 必须同步改为轮询**，否则界面会显示"生成成功"但内容是空的。
- **运行方式**（开发期需**两个进程**）：
  - API：`uv run fastapi dev`
  - Worker：`arq app.core.worker.WorkerSettings`
- **前置条件**：Redis 已启动并可 `PING`（§2.2.1）；MySQL 可连
- **验收**：
  - **提交即返回**：`create` 百毫秒内返回 202，任务处于 `queued`
  - **阶段推进可查**：假流水线（5 阶段各 sleep 0.1s）逐步写入 `stage` / `progress`
  - **API 重启不丢**：入队后立刻重启 API 进程，任务仍被 worker 跑完
  - **重复提交去重**：同一 `task_uuid` 二次入队返回 `None`，不产生第二个 job
  - **僵尸回收**：人为造一条超时 `running` 记录 → worker 启动后被标 `failed`
  - **异常路径**：流水线抛错 → 任务标 `failed` + `error_msg` 落库，**不吞异常**
  - **前端全链路**：提交 → 轮询看到阶段推进 → 成功可预览 / 失败有提示
  - `pytest` 全绿

### 阶段 1｜双数据源基座 + Harness 基座 —— ✅ 已完成（2026-09-15）
- **产出**：
  - `app/core/pg_config.py`、`app/core/pg_db.py`（`pg_engine` / `PgSessionLocal` / `PgBase` / `get_pg_db()`）
  - `app/models/agent/`（`agent_session` / `agent_message` / `generation_source`）
  - `app/alembic/`（PG 侧迁移；含 `CREATE EXTENSION vector`，**但不建向量表**，见要点）
  - `app/utils/weg_gen/file_store.py` + `app/agents/web/tools.py`（v1 步骤 1 并入）
  - `app/agents/common.py` + `AgentTrace` / `StageBudget`
- **要点**：
  - ⚠️ **一期不建向量表**：华为云 BGE-M3 是 1024 维，若一期把维度写死进表，
    二期换模型（768/1536 维）就要迁移数据。一期只把 `vector` extension 开好（零成本），
    二期用 Alembic 加表。
  - 两个 declarative Base **物理隔离**：`app/models/agent/*` 只 import `PgBase`，
    `app/models/*.py` 只 import `MysqlBase`。
- **前置**：✅ 已完成（2026-09-15）—— Docker 已起，`pgvector/pgvector:pg18` 容器 `wgp-pg` 已就绪
  （`pg_isready` 通过）；`psycopg[binary]` 与 `alembic` 已 `uv add`。
  ⚠️ 卷挂载见 §5 硬约束 19
- **验收**：
  - 双库能同时连通；`import app.main` 正常
  - **"故意在错库查表"用例**：用 MySQL session 查 `agent_session` 必须报错（证明没串库）
  - 并发隔离：两个独立 tools 实例往各自 store 写同名文件，内容互不串
  - 非法文件名（`../x`、`a/b.css`）返回错误字符串而不是抛异常
- **实际交付与偏差（2026-09-15）**：
  - 交付：`app/core/pg_config.py`、`app/core/pg_db.py`、`app/models/agent/`（三表）、
    `alembic.ini` + `app/alembic/`（首个迁移 `4e97ff4fef89`，含 `CREATE EXTENSION vector`）、
    `app/utils/weg_gen/file_store.py`、`app/agents/web/tools.py`、
    `app/agents/common.py`（+`AgentTrace` / `ToolCallRecord`）、
    `app/utils/utils_check/check_pg.py`、`tests/test_web_tools.py`、`tests/test_pg_metadata_isolation.py`
  - **偏差 1：`StageBudget` 未产出，推迟到阶段 5** —— 它的唯一消费者是"难度分级 → 步数/预算映射"，
    阶段 1 产出它就是无人使用的代码。
  - **偏差 2：`app/repositories/agent/` 未产出，推迟到阶段 2** —— 阶段 2 的 chat 会话是它的第一个消费者。
  - **偏差 3：`file_writer._safe_name` 改名为公开的 `safe_name`** —— 工具集与虚拟文件系统都要复用它，
    继续用 `_` 前缀就得跨模块 import 私有名。
  - 应用户决策，PG 侧列名统一 `snake_case`；时间列用 `timestamptz`（避免 naive 时间被按会话时区解释）；
    `is_delete` 沿用 0/1 与 MySQL 保持一致，让两库查询写法统一。

### 阶段 2｜意图路由 + chat-agent + 对话持久化 + 别名机制
- **产出**：`agents/router/intent_router.py`、`agents/chat/chat_agent.py`、
  `app/prompts/intent_router_system.md`、`app/prompts/chat_agent_system.md`、
  `app/utils/agent/alias.py`、`POST /api/agent/chat`、`agents/state.py`
- **要点**：
  - **意图与完备度是两个正交维度**：

    | `intent` | `readiness` | 动作 |
    |---|---|---|
    | `chat` | — | → chat-agent 对话，**不建生成任务** |
    | `generate` | `needs_clarification` | → chat-agent **带着生成目标**追问槽位 |
    | `generate` | `ready` | → 直接进需求装配流水线 |

  - **规则优先、LLM 兜底**：有附件 / 命中生成动词 / 会话里已有已确认需求 → 规则直接判；
    都不命中才走 `llm_structured_client` 结构化输出。既省钱又稳。
  - **Router 每轮都跑**，不是只在第一轮 —— 多轮里 `chat → generate` 是常态。
  - **槽位显式枚举**：`site_kind` / 核心功能 / 目标用户 / 风格 / 是否需数据持久化；
    缺关键槽位 → `needs_clarification`。
  - L1 **历史回放**：按 `session_id` 顺序读消息注入上下文（否则用户澄清完、下一轮拿不到）。
- **验收**：
  - 三类输入（纯咨询 / 模糊需求 / 明确需求）路由正确
  - **纯咨询不产生任何生成任务**（用 DB 断言，不是看日志）
  - `a@b.com` **不被误展开**；`@doc1` 正确展开为摘要；失效别名回放不报错
  - 多轮对话落 PG 可完整回放

### 阶段 3｜文档解析
- **产出**：`app/utils/doc/pdf_parser.py`、`html_parser.py`（**无 LLM**）、
  `app/agents/source/doc_digest_agent.py`、`POST /api/agent/source/upload`、
  `app/services/source_service.py`、上传目录 `uploads/{user_id}/`
- **要点**：
  - **解析与理解分两层**：解析层无 LLM 可单测 → 理解层出 `RequirementDigest` 结构化结果
  - PDF：按页抽文本；**扫描版（无文本层）必须明确报错**，不能静默产出空需求让模型瞎编
  - HTML：抽 `{正文文本, 区块骨架, 设计令牌}`；设计令牌 = 配色/字体族/字号阶梯/圆角/间距/布局方式；
    **行内大段重复样式直接丢弃**
  - 大文档走 **map-reduce**（分块摘要 → 归并），每块固定 token 预算
  - 附件角色显式标注：`content` | `style` | `both`
  - **多附件归并成一份 digest**，不是各生成一份（否则 web-agent 收到互相冲突的需求）
  - 上传即分配 `@docN`
- **验收**：真实 PDF / HTML 各一份跑通；扫描版报错可复现；设计令牌人工核对；多附件归并正确

### 阶段 4｜个人 RAG **占位**（不是实现）
- **产出**：`agents/rag/need_rag.py`（**真实现**）、`agents/rag/retriever.py`（接口 + 桩 provider）
- **要点**：
  - `need_rag` 判定逻辑一期就写完并产出**真实结果**，只是"查"这一步不接
  - **三态语义从第一天起就是真的**：`hit` / `miss`（查了但没有） / `skipped`（判定不需要）
  - `requirement_merge` 必须把 RAG 当成**可选的第 5 个输入块**，桩阶段恒为空，
    merge 逻辑不感知它是否实现 → **二期只替换 provider，不动图结构、不动 prompt 骨架**
- **验收**：`skipped` 路径可复现；把 provider 换成假实现后，`hit` / `miss` 两条路径也能跑通

### 阶段 5｜plan-agent
- **产出**：`agents/plan/plan_agent.py`、`app/prompts/plan_agent_system.md`、`FilePlan` 模型
- **要点**：
  - 产出结构化 `FilePlan`：`difficulty`(easy/medium/hard)、`files[]{name, role, depends_on, summary}`、
    `tech_constraints[]`、`assets[]`
  - **难度必须落地**：`difficulty` 直接映射 web-agent 的**步数上限与 token 预算**，
    否则这个字段是装饰品
  - **规划只出"做什么"，不出代码** —— 否则 plan 变成一次昂贵的预生成，且会与 web-agent 打架
  - ⚠️ **与原约定的冲突已解决**：文件集合与命名以 plan 为准，
    但必须过 `file_writer._safe_name` 白名单校验；web-agent 的完成门禁**以 plan 声明的清单为准**
    （不再硬编码三件套）。原"单文件模式"降为 `difficulty=easy` 的特例。
- **验收**：同一需求跑 3 次文件清单稳定；`easy` 需求不应产出 6 个文件（难度判定有效性）

### 阶段 6｜web-agent + 外层编排图 ★主流程打通
- **产出**：`agents/web/web_agent.py`、`agents/orchestrator.py`、
  `app/prompts/web_agent_system.md`（**去掉交付清单**，只留角色 + 工具语义）、统一入口接口
- **要点**：
  - 手写 `StateGraph`：`model`（bind_tools）→ 条件边（有 `tool_calls`？）→ `ToolNode` → 回 `model`
  - 每次生成**新建 store + 新建图**，杜绝跨请求状态残留（§3.7 铁律）
  - **完成判定权在 Python 侧**，模型说"我写完了"不算数
  - 工具**永不抛异常**，失败原因作为**字符串返回值**交给模型 —— 感知闭环的关键
  - 步数上限 + 超时 + 连续无进展检测
  - 用量**累加**（复用 `ModelUsage.__add__`）
  - trace 落 `_debug_trace.jsonl`
- **验收（三层观察，v1 保留）**：
  1. **是否自主调工具**：没有"必须调用工具"指令时，模型是否主动调 `write_file`
  2. **是否自主拆解**：是否自己决定"先 html，再 css，再 js"，而不是要求一次性全给
  3. **是否响应失败**：故意让一次 `write_file` 返回错误，模型是否读了错误并改正重试
- **验收（门禁）**：模型只写 1 个文件时任务判 `failed`，而不是 `success`
- **验收（端到端）**：create → 轮询阶段 → success → preview-ticket → 打开产物；`pytest` 全绿

### 阶段 7｜新旧对照 + 退役 ★关键判断
- **要点**：用**同一个需求**分别跑旧实现（`generate_multi_file`）与新的 web-agent
- **验收**：记录并对比 —— 成功率 / 产物完整性 / 总 token / 耗时。
  **只有数据支持才退役旧实现，否则回退并重新评估**；旧实现保留为 `simple` 模式兜底
- **收尾**：回写 `docs/generation_module_design.md` 与 `docs/proj_progress.md`

### 阶段 8｜前端对接
- **产出**：会话式 Generate 页（聊天区 + 附件上传 + 需求确认卡片 + 阶段进度条 + 别名 chip）、
  `src/api/agent_api.ts`、`src/types/agent_types.ts`
- **要点**：阶段 0 已先把轮询做掉，本阶段做的是**交互形态升级**

---

## 5. 风险与硬约束（实施时逐条对照）

1. ⚠️ **两个 declarative Base 不能混注册** —— 最阴的坑：`PgBase` 与 `MysqlBase` 的模型
   若被同一处 import 混淆，`create_all` 会**在错误的库里建表，而且不报错**。
   必须物理隔离 + 用"故意在错库查表"用例验证（阶段 1 验收）。
2. ⚠️ **虚拟文件系统绝不能是全局变量** —— 见 §3.7。并发下会串文件。
3. ⚠️ **`@` 会误伤邮箱** —— 见 §3.6.1。只有命中已注册 alias 才展开。
4. ⚠️ **pgvector 查询必须带 `user_id` 过滤**（二期）—— 漏了就是 A 能检索到 B 的私人文档。
   这是整条链路里最严重的安全边界。
5. ⚠️ **双库无跨库事务** —— 允许孤儿 + 幂等清理，不硬凑分布式事务。
6. ⚠️ **同步 LLM 调用会阻塞 asyncio 事件循环** —— worker 内必须 `asyncio.to_thread`。
7. ⚠️ **SQLAlchemy Session 非线程安全** —— worker 不能复用请求的 `db`。
8. **token 成本会上升，不是下降** —— 多轮工具往返每轮都要重发整段历史。
   对冲手段：`write_file` 允许一次传多文件，让模型自行合并调用；每阶段设预算上限。
9. **完成判定权不能交给模型** —— 模型可能在只写了 1 个文件时宣布完成。门禁必须在 Python 侧。
10. **必须设步数上限** —— 否则模型可能陷入"读文件→改文件→再读"的死循环，把接口拖死。
11. **思考模式的取舍需实测** —— 见 §2.1，`llm_client` 重新成为候选，但必须用数据决定。
12. **工具返回值即提示词** —— 工具返回的字符串会进入模型上下文，
    措辞要"面向模型、可执行"，不要塞开发者诊断（沿用 2026-09-14 已踩过的教训）。
13. **对话入向量库需保留策略**（二期）—— 不是每句都入，只入"用户明确表述需求/偏好/约束"的轮次
    （`is_memorable` 标记），否则向量库会被"你好""谢谢"灌满。
14. **L1 与 L2 不要混**（二期）—— "用户说过的话"与"用户上传的资料"证据强度不同，
    用 `knowledge_chunk.source_type`(`document`/`conversation`) 区分，不混在一个集合里。
15. **`.env` 与本机环境** —— 见 §2.3。
16. ⚠️ **Redis 不是真源** —— 任务状态/阶段/进度/结果必须落 MySQL，Redis 只做队列。
    若状态只在 Redis，一次 `FLUSHDB` 或内存淘汰就让任务永久消失。
17. ⚠️ **arq `max_tries` 必须设 1** —— 默认 5，且悲观执行会在 worker 重启后重跑；
    LLM 任务按 token 计费，自动重试等于重复烧钱。详见阶段 0 要点。
    **已用源码判定确认（2026-09-15）**：`arq/worker.py:549-550` 的 `job_try > max_tries` 判定
    发生在调用函数体（L595）**之前** —— 被取消的任务重入队后只会被判失败，**不会二次调用模型**。
18. ⚠️ **产物落盘 + 独立 worker = 单机假设** —— 未来 worker 与 API 分机器部署时，
    本地磁盘不共享会让预览失效，**必须换对象存储**。
19. ⚠️ **PG 18+ 镜像的数据卷要挂 `/var/lib/postgresql`，而不是 `/var/lib/postgresql/data`** ——
    PG 18 起镜像期望挂载整个 `/var/lib/postgresql`，并在其下自建 `18/docker` 子目录存放数据；
    若沿用旧版的 `/var/lib/postgresql/data`，容器启动时会检测到目录结构不对而**直接崩溃退出**
    （2026-09-15 实测踩到并已修正，见 `docker-compose.yml`）。
20. ⚠️ **`alembic.ini` 必须保持纯 ASCII（注释用英文）** —— Alembic 用 `encoding="locale"` 读它
    （见 `alembic/util/compat.py`）。中文 Windows 的 locale 是 GBK，文件里任何一个 UTF-8 非 ASCII 字节
    都会让**所有 alembic 命令**在启动前就 `UnicodeDecodeError` 挂掉。
    注意 `-X utf8` / `PYTHONUTF8=1` **救不了**：`locale.getencoding()` 不受 UTF-8 模式影响。
    中文理由放在 `app/alembic/env.py`（.py 永远是 UTF-8）与本文件里。

---

## 6. 实验记录（边做边填）

| 日期 | 实验 | 输入 | 结果 | 结论 |
|---|---|---|---|---|
| 2026-09-15 | 工具调用探针：思考 vs 非思考 | "调用 write_file 写入 index.html" | 两者均 `finish_reason=tool_calls` | 思考模式**可**自然调用工具；旧约束仅限强制 tool_choice |
| 2026-09-15 | 环境复核（PG / Docker / Redis / langchain_openai） | — | PG18 无 pgvector、Docker 未启动、无 Redis、langchain_openai 已装 | 见 §2.2 |
| 2026-09-15 | arq 取消重跑探针（**源码判定**） | `max_tries=1` 的 worker 被中途关闭 | `arq/worker.py:549-550` 在**调用函数体之前**判定 `job_try > max_tries`（函数体直到 L595 才被 create_task）；被取消的任务以 `job_try=2` 重入队后直接判失败（`max 1 retries exceeded`），模型不会被二次调用 | **`max_tries=1` 确实能防止重复烧 token**；但被杀死那一次的产物不会落库，DB 里仍是 `running` → **僵尸回收依然必需** |
| 2026-09-15 | 阶段 0：异步骨架 HTTP 端到端（真实 LLM） | 注册 → 登录 → create → 轮询 → 终态 | 提交 **202 仅 0.09 秒**（原同步 30~157 秒）；1.6s 观测到 `generating 70%`，28.8s 到 `success/done 100%`；产物 `['index.html']`、`preview_url` 正确、token 用量落库 | **通过** |
| 2026-09-15 | 阶段 0：僵尸回收（真实数据） | worker 启动 | 历史僵尸记录 id=5（createTime 2026-09-14 18:30）被回收：`updateTime` 刷新为 2026-09-15 14:45:47，`errorMsg` 写入"任务超时或执行进程中断，已自动标记为失败" | **通过** |
| 2026-09-15 | 阶段 1：跨库隔离（**实库验收**） | MySQL 会话查 `agent_session` / PG 会话查 `generation_task` | 两者均按预期报 `ProgrammingError`；而 PG 查自己的 `generation_source` **正常可查** | **通过** —— 后者排除了"表不存在"造成的假阳性，证明运行时确实没串库 |
| 2026-09-15 | 阶段 1：`write_file` 的自由对象参数能否被 DeepSeek 接受（真实调用） | `bind_tools` + "请交付一个只有 index.html 的最小页面" | `finish_reason=tool_calls`，返回 `write_file({"files": {"index.html": "<!DOCTYPE html>…"}})`，仅 337 output tokens | **通过** —— `dict[str, str]` 这个最不确定的假设成立，`tools.py` 的主要风险排除 |
| ✅ | 阶段 0：异步骨架 —— **已完成（2026-09-15）**，见上方两条实测记录 | — | — | — |
| | 阶段 2：意图路由正确率（三类输入） | | | |
| | 阶段 3：PDF / HTML 解析质量 | | | |
| | 阶段 6：模型自主性三层验证 | | | |
| | 阶段 7：新旧实现对照 | | | |

---

## 7. 相关文档

- 现有设计约定：`docs/generation_module_design.md`（§3.2 agents 契约、§7 DeepSeek 约束）
- 进度记录：`docs/proj_progress.md`
- 问答记录：`docs/QA.md`
- 上游 issue：[DeepSeek-V3 #1376 tool_choice 限制](https://github.com/deepseek-ai/DeepSeek-V3/issues/1376)
- 上游 issue：[DeepSeek-V3 #806 无 embedding 接口](https://github.com/deepseek-ai/DeepSeek-V3/issues/806)
- arq 官方文档（v0.28.0）：[arq-docs.helpmanual.io](https://arq-docs.helpmanual.io/)
  —— 关键：**"Jobs may be called more than once!"**（悲观执行，任务在成功/失败前不离开队列）
