# Agent 框架重构计划 —— backend-uv-fastapi

> 本文件是**实施计划与验收清单**，随步骤推进逐项打勾。它回答"**怎么改**"；
> "改完后的最终约定"在全部完成后回写到 `docs/generation_module_design.md`。
>
> **最近更新时间：2026-09-15（阶段 8 完成，一期全部落地）**
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
| 4 | 旧实现（single / multi） | **保留**为 `simple` 模式兜底，阶段 7 用数据决定是否退役 —— **阶段 7 结论（2026-09-15）：`multi` 退役（deprecated，仅保留兼容），`single` 保留为单页极速路径，`agent` 转正为默认；依据见阶段 7 实测数据** |
| 5 | PostgreSQL 定位 | **双库并存**：MySQL 存业务（user / generation_task），PG 存对话与知识库 |
| 6 | 附件别名形态 | **系统生成 `@doc1` + `display_name` 展示原文件名** |
| 7 | 异步执行器 | **Redis + arq**（独立 worker 进程）；**Redis 只做队列，任务状态真源在 MySQL**；`max_tries=1` 不自动重试 |
| 8 | 教学模式 | 每个阶段先讲规划与前置条件，用户确认前置条件完成后，助手再编写代码 |
| 9 | 意图路由 | **完全交给 AI**（结构化输出），**不做"关键词命中就跳过大模型"的规则前置**（2026-09-15 决策，覆盖 §阶段 2 的原"规则优先"设计） |
| 10 | 推理产物落位 | `FinalRequirement` / `FilePlan` 放 **PG**（阶段 5 新建 `generation_plan` 表），不塞进 MySQL 的 JSON 列 |
| 11 | 需求槽位 slots | 暂定 5 项：`site_kind` / `features` / `audience` / `style` / `need_persistence`（字段名即 API 契约） |
| 12 | `create` 入口 | **也过 AI 完备度判定**；判定信息不足时任务停在 `stage=clarifying`（human-in-the-loop 暂停），**不标 failed** |

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

#### 3.2.1 职责单元清单（agent 与节点）

> 2026-09-15 与用户确认。**按"有没有循环 / 工具调用"严格划分，一期只有 `generation-agent` 是真 agent**，
> 其余都是"结构化输出节点"。这不影响都叫 agent，但两者的**测试方式完全不同**（见本节末）。

| 单元 | 类型 | 职责 | 何时跑 | 落位 |
|---|---|---|---|---|
| `intent_router` | 节点 | 意图 + 完备度 + slots 抽取 | 每轮对话；`create` 的 worker 内 | 阶段 2 |
| `chat-agent` | 节点 | 澄清与建议 → 需求草稿 | `intent=chat` 或 `needs_clarification` | 阶段 2 |
| `digest-agent` | 节点 | **仅文件** → `RequirementDigest`（含 content/style/both 判定） | 上传后，**结果可缓存、跨轮复用** | 阶段 3 实现 / 阶段 6 入图 |
| `merge` | 节点 | **对话摘要 + 四来源冲突消解** → `FinalRequirement` | 需求装配 ⑪ | 阶段 3 实现 / 阶段 6 入图 |
| `need_rag` + retriever | 节点 + 二期 provider | 判定是否需要私人知识库 | 需求装配 ⑩ | 阶段 4 |
| `plan-agent` | 节点 | `FinalRequirement` → `FilePlan` | 循环前 ⑫ | 阶段 5 |
| **`generation-agent`** | **真 agent** | ReAct 循环：工具调用写文件 | 循环 ⑬ | 阶段 6 |

**三条划界理由（都影响实现，不是命名洁癖）**：

1. **digest 与 merge 必须拆开**：digest 的输入是**不变的文件**，可以在上传时算一次并缓存进
   `generation_source.digest`；merge 的输入是**一直在变的对话**，每次生成都要重算。
   合成一个节点，就会把"本可不重算"的活每轮重做一遍 —— 附件越多越浪费。
2. **merge 不只是"摘要"，更是"决策"**：用户显式要求 / 对话摘要 / 文档 digest / RAG 结果
   四者可能互相矛盾（例：用户说"极简"，文档却给"深色大面积渐变"），必须按 §3.5 的优先级裁决。
   做成可评审、可单测的独立步骤，远好过让 `generation-agent` 临场裁决 ——
   后者既不可观测，也无法回归测试。
3. **plan 保持独立节点（方案 A）**：GATE 需要一份**事先声明的文件清单**才能判定"交付完整"。
   若把规划并进循环，完成判定就只能退回"模型说完了就算完" ——
   那正是 §1 诊断出的原始毛病。附带好处：难度 → 步数/预算的映射可在**进入循环之前**确定，
   规划错了也能立刻失败，不必等整个循环跑完。

**测试方式的差别（阶段 1 已按这个思路在写）**：

- **节点**：喂假模型 → 断言结构化产物。纯离线、快、稳（`llm_structured_client` 可注入）。
- **agent**：必须断言**循环行为** —— 步数上限是否生效、工具调用序列、
  "工具报错后是否改正"、以及门禁是否拦住"只写 1 个文件就宣布完成"。这是阶段 6 的验收重点。

### 3.3 目录落位

```
backend-uv-fastapi/
├─ app/
│  ├─ agents/
│  │  ├─ common.py              # 扩展：+ AgentTrace / StageBudget（ModelUsage 保留）
│  │  ├─ state.py               # ★新：AgentState / ClarifiedRequirement / RequirementDigest / FilePlan
│  │  ├─ stages.py              # ★新：AgentStage 枚举 + 中文文案 + 进度映射
│  │  ├─ orchestrator.py        # ★新：外层需求装配图
│  │  ├─ router/intent_router.py    # 意图识别（全 AI 结构化判定 + Python 兜底）
│  │  ├─ chat/chat_agent.py         # 澄清对话（只说话，不抽槽位）
│  │  ├─ source/role_policy.py      # ★新：角色裁决（只有 HTML 能当风格源；纯函数）
│  │  ├─ source/doc_digest_agent.py # **仅文件** → RequirementDigest（map-reduce，结果可缓存）
│  │  ├─ merge/requirement_merge.py # 对话摘要 + 四来源冲突消解 → FinalRequirement
│  │  ├─ rag/need_rag.py            # 是否需要检索（真实现，产出真实判定）
│  │  ├─ rag/retriever.py           # 接口 + 桩 provider（判定不需要→skipped；需要→miss 且自报未接入）
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
│  │     ├─ generation_source.py
│  │     └─ generation_plan.py   # ★新（阶段 5）：FinalRequirement / FilePlan + 预估与实际对照
│  ├─ prompts/
│  │  ├─ intent_router_system.md    # ★新
│  │  ├─ chat_agent_system.md       # ★新
│  │  ├─ doc_digest_system.md       # ★新（整篇理解）
│  │  ├─ doc_chunk_digest_system.md # ★新（分块理解，map 阶段）
│  │  ├─ requirement_merge_system.md# ★新（四来源冲突消解）
│  │  ├─ plan_agent_system.md       # ★新
│  │  └─ web_agent_system.md        # ★新（**不含**交付清单）
│  ├─ repositories/agent/           # ★新：PG 侧 session / message / source / plan
│  ├─ utils/
│  │  ├─ agent/alias.py         # ★新：别名分配/解析/展开/校验（纯函数）
│  │  ├─ agent/source_store.py  # ★新：附件落盘（展示名与磁盘名分离）
│  │  ├─ doc/                   # ★新：base / text_parser / html_parser / pdf_parser（无 LLM）
│  │  └─ weg_gen/file_store.py  # ★新：虚拟文件系统（per-request）
│  └─ alembic/                  # ★新：PG 侧迁移（extension + 索引）
├─ sql/scripts/                 # DDL 脚本（沿用根 sql/ 约定）
├─ docker-compose.yml           # ★新：redis + pgvector(pg18) 两个服务
└─ docs/agent_refactor_plan.md  # 本文件
```

### 3.4 数据与存储边界（**硬约定**）

**MySQL 存业务**：`user`、`generation_task`（+ 阶段列）
**PostgreSQL 存「Agent 域」**：对话、知识库、**推理产物** —— `agent_session`、`agent_message`、
`generation_source`（+ 阶段 5 的 `generation_plan`、二期的 `knowledge_chunk`）

> **两域的划分口径**（2026-09-15 与用户确认）：
> - **MySQL = 业务域**：用户、**任务生命周期**（状态 / 阶段 / 进度 / 用量）、产物元数据（目录 / 文件名）
> - **PG = Agent 域**：对话、附件与别名、知识库，以及 `FinalRequirement` / `FilePlan` 这类**推理产物**
>
> 推理产物刻意放 PG，而不是塞进 `generation_task` 的 JSON 列：它们属于 Agent 的"思考过程"，
> 与"业务元数据"是两类数据，混在一起会让 `generation_task` 变成杂物间。
> 代价是"读一次完整生成决策要查两个库" —— 可接受，因为**跨库不 JOIN** 本来就是既定铁律。
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

### 3.5 输入块优先级与预算（**关键**：MERGE 与 web-agent 是两处）

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

> ⚠️ **上面这张表是 web-agent 的输入优先级，不是 MERGE 的。** 两者是不同节点、不同输入集：
>
> **⑪ MERGE 的输入优先级**（产出唯一的 `FinalRequirement`）：
>
> | 优先级 | 输入块 | 超预算裁剪顺序 |
> |---|---|---|
> | 1 | 用户显式要求 | **永不裁** |
> | 2 | chat-agent 澄清摘要 | 最先裁 |
> | 3 | 文档 digest（内容素材 + 风格规范） | 压缩成摘要 |
> | 4 | RAG 检索结果（二期才有内容） | 再裁 |
>
> **为什么必须先在 MERGE 收敛成一份需求**：如果让 web-agent 直接面对四个可能互相矛盾的原始来源，
> 它只能自己临场裁决 —— 而那个裁决既不可观测、也无法回归测试。
> MERGE 把"冲突消解"变成一个**可评审、可单测**的独立步骤（完整流程见 §3.8）。

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

### 3.8 端到端工作流（用户提交需求 → 拿到网页）

> 这是把 §3.1~§3.7 串起来的**总流程**。实施任何阶段之前，先在这一节确认自己动的环节。
> 2026-09-15 与用户确认。

#### 3.8.1 总览

```
用户（前端）
  │ ① POST /api/agent/chat   { session_uuid?, message, attachments[] }
  ▼
┌───────────────────────── FastAPI 进程 ─────────────────────────┐
│ ② 落用户消息        → PG agent_message                          │
│ ③ 本轮带附件？      → 上传 → 解析 → 分配别名 @docN               │
│                       (PG generation_source + uploads/)         │
│ ④ 装配上下文        → L1 历史回放 + @docN 展开成 digest 摘要      │
│ ⑤ intent_router（AI 结构化输出）                                 │
│      intent    : chat | generate                                │
│      readiness : ready | needs_clarification                    │
│      slots     : {site_kind, features, audience, style,         │
│                   need_persistence}                             │
│      missing_slots / reason                                     │
└────────────────────────────┬────────────────────────────────────┘
                 ┌───────────┴───────────┐
        chat / needs_clarification   generate + ready
                 │                       │
                 ▼                       ▼
   ⑥ chat-agent 回复            ⑦ 建任务(MySQL queued) + 入队(Redis)
      写回需求草稿                        │ 202 立即返回
      → 回到 ①（多轮）                    ▼
                              ┌──────── arq worker 进程 ────────┐
                              │ ⑧ ROUTING   归一化 + 完备度复核  │
                              │ ⑨ DIGESTING 附件解析与归并       │
                              │ ⑩ RETRIEVING RAG 判定与检索      │
                              │ ⑪ MERGE     → FinalRequirement   │
                              │ ⑫ PLANNING  → FilePlan           │
                              │ ⑬ GENERATING web-agent ReAct 环  │
                              │ ⑭ GATE      完成门禁（不过则补缺）│
                              │ ⑮ 落盘 + 落库                    │
                              └───────────────┬─────────────────┘
                                              ▼
                        前端轮询 → 签发票据 → 打开 /preview/.../index.html
```

#### 3.8.2 第一段：入口（两条路径，共用同一套 AI 判定）

**入口 1 —— 对话路径 `POST /api/agent/chat`**（阶段 2 实现）

| 步 | 动作 | 落位 |
|---|---|---|
| ① | 无 `session_uuid` → 新建会话 | PG `agent_session` |
| ② | 落用户消息（**含 `@docN` 原文**，绝不存文件正文或磁盘路径） | PG `agent_message` |
| ③ | 新附件 → 上传落盘 + 解析 + 分配别名 | PG `generation_source` + `uploads/{uid}/{source_uuid}/` |
| ④ | 装配上下文：按 `session_id` 回放最近 N 轮；`@docN` 展开为 **digest 摘要**（不是全文） | 内存 |
| ⑤ | **intent_router**（`llm_structured_client` + 结构化输出） | — |
| ⑥a | `chat` / `needs_clarification` → **chat-agent** 产出澄清问题或建议；更新需求草稿 | PG `agent_message` + `agent_session.draft_requirement` |
| ⑥b | `generate` + `ready` → 进入第二段 | — |

**router 跑在 API 进程内**（一次 LLM 调用，约 2~5 秒）。对话场景用户本来就预期等待，
这与 `create` 的"0.09 秒返回"不冲突 —— 那是另一条路径。

**为什么要"展开成摘要"而不是塞全文**：一份页面 HTML 轻松几万 token，而 digest 只有几百字。
这是 prompt 能同时容纳多个附件的前提，也是 §3.6 别名机制存在的意义。

**入口 2 —— 直达路径 `POST /api/generation/create`**

用户已经明确说"生成"，所以这里 router 的**"意图"判定没有价值，"完备度"判定才有**。
处理方式：

- 建任务（`status=running` / `stage=queued`）→ 入队 → **立即 202**（维持 0.09 秒）
- **完备度判定挪进 worker**（⑧ ROUTING）。若判定"信息严重不足，生成必然跑偏"：
  任务停在 `stage=clarifying` / `status=running`，`stageDetail` 写面向用户的追问，**不标 failed**
- 用户在对话里回答后**重新入队**，带着新信息从 ⑧ 重跑

> 这是真正的 **human-in-the-loop 暂停**，而不是"任务失败了"。
> V1 用"重新入队重跑前段"实现（router + merge 都是小调用，重跑很便宜），
> 不做 LangGraph checkpoint 断点恢复 —— 那是后续优化。
>
> ⚠️ 配套改动见 §阶段 2 要点：`AgentStage` 要加 `CLARIFYING`，
> 且**僵尸回收必须跳过 `stage='clarifying'`**。

#### 3.8.3 第二段：需求装配（worker 内，⑧~⑪）

| 阶段 | 输入 | 处理 | 产出 |
|---|---|---|---|
| ⑧ ROUTING | 用户 prompt + 会话上下文 | 完备度复核；不足 → `clarifying` 暂停 | 归一化需求 |
| ⑨ DIGESTING | 附件（若有） | 解析层（**无 LLM**）→ 理解层 map-reduce | `RequirementDigest` |
| ⑩ RETRIEVING | 需求 | 判定是否需要个人知识库；**一期只判定，检索是桩** | `hit` / `miss` / `skipped` |
| ⑪ MERGE | 上面全部 | 按 §3.5 的 MERGE 优先级归并 + 冲突消解 | `FinalRequirement` |

`FinalRequirement` 结构（**后两个字段是关键设计**）：

```
summary        : 一段人可读的需求陈述
slots          : {site_kind, features[], audience, style, need_persistence}
content_points : 来自文档的内容素材
style_spec     : 来自文档的风格规范
constraints[]  : 硬约束
sources[]      : [{kind: user|chat|doc|rag, ref}]   ← 证据可追溯
uncertainty[]  : 不确定项 / "个人知识库未命中"        ← 把不确定性显式传给下游
```

> `uncertainty` 是刻意设计的：RAG 未命中时**绝不能沉默**，否则模型会当成"用户资料里没有"然后编。
> 它必须一路传到 plan 与 web-agent，必要时回头追问。

#### 3.8.4 第三段：规划与生成（⑫~⑮）

| 阶段 | 输入 | 产出 / 关键约束 |
|---|---|---|
| ⑫ PLANNING | `FinalRequirement` | `FilePlan`：`difficulty` + `files[{name, role, depends_on, summary}]` + `tech_constraints` + `assets` |
| ⑬ GENERATING | 系统提示词 + `FinalRequirement` + `FilePlan` + 工具语义 | web-agent 的 **ReAct 环**（`model ⇄ ToolNode`），写进 **per-request 虚拟文件系统** |
| ⑭ GATE | `FilePlan.files` + store | **Python 侧**用 `store.missing(plan 声明的清单)` 判定；不过 → 定向补缺（最多 N 轮）→ 仍不过则 failed |
| ⑮ 落盘 | store 快照 | `generated/{uid}/{task_uuid}/` + MySQL 状态 / 用量 |

- ⑫ 必须落地两条：**`difficulty` 要真的映射** web-agent 的步数上限与 token 预算（否则是装饰品）；
  文件名单要过 `safe_name` 白名单。
- ⑬ 三条铁律：工具**永不抛异常**（失败原因作为字符串回给模型，这是感知闭环）；
  **步数上限**由 `difficulty` 决定；**用量累加**（复用 `ModelUsage.__add__`）。
- ⑭ **门禁必须在 Python 侧**：模型完全可能在只写了 1 个文件时说"我完成了"。
  所以刻意**不提供** `finish` / `done` 工具。

#### 3.8.5 产物落位总表（实施时的对照表）

| 产物 | 存储位置 | 写入时机 | 谁消费 |
|---|---|---|---|
| 会话 | PG `agent_session` | 首次对话 | router / 前端 |
| 需求草稿（slots） | PG `agent_session.draft_requirement`（**阶段 2 待加列**） | 每轮澄清后 | router / MERGE |
| 消息 | PG `agent_message` | 每轮 | L1 历史回放 |
| 附件与别名 | PG `generation_source` + `uploads/` | 上传时 | digest / 别名展开 |
| `RequirementDigest` | PG `generation_source.digest` | 解析后（**可缓存，跨轮复用**） | MERGE |
| 任务生命周期 | MySQL `generation_task` | 入队 + 每阶段 | 前端轮询 |
| `FinalRequirement` / `FilePlan` | PG `generation_plan`（阶段 5 已建，迁移 `b7c1d2e3f4a5`） | MERGE / PLANNING 后 | web-agent / GATE |
| 预估 vs 实际（难度/步数/文件数） | PG `generation_plan` 同一行（预估在 PLANNING 写入，实际在生成结束时回填） | 规划时 + 生成结束时 | 优化提示词 / 调参 |
| 产物文件 | `generated/{uid}/{task_uuid}/` | 循环结束后**一次性**落盘 | 预览 |
| `AgentTrace` | `_debug_trace.jsonl` | 失败时（可选总是） | 排查 |
| token 用量 | MySQL `generation_task` 三列 | 结束时（**失败也记**） | 前端 / 统计 |

#### 3.8.6 失败与降级矩阵

| 故障点 | 行为 | 是否阻塞主流程 |
|---|---|---|
| Redis 不可用 | 建任务后当场标 failed → 503 | 阻塞（无法执行） |
| 附件解析失败（如扫描版 PDF） | `parse_status=failed` + 明确原因 | **不阻塞**：跳过该附件，`uncertainty` 写明 |
| 文档 digest 失败 | 降级为"仅用正文片段" | 不阻塞 |
| RAG 未命中 | `miss` + `uncertainty` 标注 | 不阻塞 |
| router 调用失败 | **按路径降级**：对话路径 → `chat`；直达路径（create）→ `generate + ready` | 不阻塞 |
| 完备度不足（`create` 入口） | 停在 `stage=clarifying`，等用户补充 | 暂停（非失败） |
| plan 失败 | 降级为"单文件 `index.html`"启发式规划 | 不阻塞 |
| web-agent 超步数 / 超时 | 用当前快照走 GATE；缺文件则 failed | 阻塞（但有完整 trace） |
| GATE 不通过 | 定向补缺 N 轮 → 仍不过则 failed | 阻塞 |
| worker 被强杀 | 任务留在 `running` → 僵尸回收标 failed | 由回收兜底 |

#### 3.8.7 V1 明确不做的事（边界）

1. **不做增量修改**：用户说"把主色改成蓝色"，V1 走**重新生成**，不做"基于已有产物改代码"。
2. **不做多智能体自由对话**：agent 之间一律用**固定图 + 结构化产物**传递，
   不让它们互相聊天（贵且不可控）。
3. **RAG 真检索不做**：一期只做判定 + 桩 provider，二期接华为云 BGE-M3 + pgvector。
4. **不做对话式流式输出**：先用同步返回 + 轮询，SSE 后置。
5. **循环中途不向用户提问**：面向用户的提问一律发生在 **⑧ ROUTING 之前**（或经 `clarifying` 暂停）。
   循环一旦开始就只允许"回到 ⑫ 重规划"或失败，不停下来等人 ——
   见 §阶段 6 要点中的理由。

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

### 阶段 2｜意图路由 + chat-agent + 对话持久化 + 别名机制 —— ✅ 已完成（2026-09-15）
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

  - **意图与完备度全部交给 AI 结构化判定**（2026-09-15 决策，覆盖原"规则优先"设计）：
    不再做"关键词命中就跳过大模型"的规则前置 —— 规则判据难维护，且遇到反讽 / 隐含意图必然判错。
    统一走 `llm_structured_client` 结构化输出；代价是每轮一次调用，换来"判定口径只有一处"。
  - **Router 每轮都跑**，不是只在第一轮 —— 多轮里 `chat → generate` 是常态。
  - **槽位显式枚举**（暂定 5 项，字段名即 API 契约）：`site_kind` / `features`（核心功能列表）/
    `audience`（目标用户）/ `style`（风格）/ `need_persistence`（是否需数据持久化）；
    缺关键槽位 → `needs_clarification`。
    缺关键槽位（`site_kind` / `features`）→ `needs_clarification`；其余槽位缺失只记入 `uncertainty`，不阻塞。
  - L1 **历史回放**：按 `session_id` 顺序读消息注入上下文（否则用户澄清完、下一轮拿不到）。
  - ⚠️ **配套改动（human-in-the-loop 暂停）**：`create` 入口也过 AI 完备度判定，
    信息不足时任务停在 `stage=clarifying` / `status=running`（**不标 failed**），等用户在会话里补充后重新入队。
    因此需要两处改动：
    1. `AgentStage` 增加 `CLARIFYING`（阶段 0 的枚举里没有）；
    2. **僵尸回收必须跳过 `stage='clarifying'`** —— 否则用户思考超过宽限期，任务会被回收误标成 failed。
       这需要改 `GenerationTaskRepository.list_stale_running()` 的过滤条件。
- **验收**：
  - 三类输入（纯咨询 / 模糊需求 / 明确需求）路由正确
  - **纯咨询不产生任何生成任务**（用 DB 断言，不是看日志）
  - `a@b.com` **不被误展开**；`@doc1` 正确展开为摘要；失效别名回放不报错
  - 多轮对话落 PG 可完整回放
- **实际交付与偏差（2026-09-15）**：
  - 交付：`app/agents/state.py`（`RequirementSlots` / `RouterDecision` / `RouterResult` / `ChatResult`）、
    `app/agents/router/intent_router.py`、`app/agents/chat/chat_agent.py`、
    `app/prompts/intent_router_system.md`、`app/prompts/chat_agent_system.md`、
    `app/utils/agent/alias.py`、`app/repositories/agent/`（session / message）、
    `app/services/agent_chat_service.py`、`app/api/agent.py`、`app/schemas/agent_schemas.py`、
    `app/utils/utils_check/check_agent_chat.py`、Alembic 迁移 `eac80e932e7f`（+`draft_requirement`）
  - 配套改动（§阶段 2 要点里预告的两条）：`AgentStage` 增加 `CLARIFYING`；
    `list_stale_running()` 增加 `exclude_stages` 参数，`recover_zombies()` 传入 `clarifying`
  - **偏差 1：`ready` 时不调用 chat-agent**。此时确认摘要的内容由槽位唯一确定，
    模型发挥不了额外价值 —— 省一次调用、少一份不确定性（`_confirmation_reply` 用模板拼）。
  - **偏差 2：`generate + ready` 之外的路径才调 chat-agent**，即 `intent=chat` 与
    `generate + needs_clarification` 两种；这与 §3.8.2 的 ⑥a/⑥b 分支一致。
  - **偏差 3：router 失败时的降级方向是"路径相关"的**（见 §3.8.6 的修订）：
    对话路径降级为 `chat`（意图不明时继续对话比擅自生成安全），
    直达路径降级为 `generate + ready`（那里用户已经明确点了生成）。
    已在 `route(..., on_failure_intent=...)` 上开出口子。
  - **修掉一个真 bug**：`_SAFE_ALIAS_RE` 漏了捕获组，而 `parse_alias_index()` 用 `group(1)`
    —— 冒烟测试第一轮就抓到（`IndexError`），随后补上测试。

### 阶段 3｜文档解析 —— ✅ 已完成（2026-09-15）
- **产出**：`app/utils/doc/pdf_parser.py`、`html_parser.py`、`text_parser.py`（**无 LLM**）、
  `app/agents/source/doc_digest_agent.py`、`POST /api/agent/source/upload`、
  `app/services/source_service.py`、上传目录 `uploads/{user_id}/{source_uuid}/`
  （磁盘文件名固定为 `source{白名单后缀}`，原始文件名只存库里的 `display_name`）
  - 同时实现（但到阶段 6 才入图）：`app/agents/merge/requirement_merge.py` ——
    对话摘要 + 四来源冲突消解 → `FinalRequirement`（见 §3.2.1 的划界理由）
- **要点**：
  - **解析与理解分两层**：解析层无 LLM 可单测 → 理解层出 `RequirementDigest` 结构化结果
  - PDF：按页抽文本；**扫描版（无文本层）必须明确报错**，不能静默产出空需求让模型瞎编
  - HTML：抽 `{正文文本, 区块骨架, 设计令牌}`；设计令牌 = 配色/字体族/字号阶梯/圆角/间距/布局方式；
    **行内大段重复样式直接丢弃**
  - 大文档走 **map-reduce**（分块摘要 → 归并），每块固定 token 预算
  - 附件角色显式标注：`content` | `style` | `both`
  - **多附件归并成一份 digest**，不是各生成一份（否则 web-agent 收到互相冲突的需求）
  - **支持的上传类型（2026-09-15 增补）**：`.pdf` / `.html` / `.htm` / `.txt` / `.md`
    - `.txt` / `.md` 的用意是让用户能**把需求直接写在文件里**上传，省去在输入框里粘贴长文；
    - ⚠️ **编码回退必须做**：中文 Windows 记事本默认存 GBK/GB18030，按 UTF-8 硬读会
      `UnicodeDecodeError`，用 `errors="ignore"` 则整篇变乱码。顺序：UTF-8 → GB18030 → 明确报错；
    - `.md` **保留 Markdown 结构**（标题 / 表格 / 代码块）直接给模型 —— 结构本身就是信息，不要预先压平。
  - ⚠️ **只有 HTML 能当风格源**（由"解析器的能力"决定，不由模型猜）：
    - `.html` / `.htm` → 可 `content` / `style` / `both`（我们抽得出设计令牌）；
    - `.pdf` / `.txt` / `.md` → **只能 `content`**（`pypdf` 只出文本，抽不出配色 / 字体 / 圆角）；
    - 模型若把 `.md` 判成风格源，由 **Python 侧强制改回 `content`**
      —— 沿用"判定权归模型、否决权归 Python"的既有原则。
  - **`.md` / `.txt` 常常本身就是"需求说明书"，而不是"待展示的素材"**：
    digest 提示词要能区分两者 —— 若文件读起来是对网页的**要求**
    （"要有登录""必须响应式"），应提取进 `constraints` 等需求性字段，
    而不是塞进 `content_points`（那会让 web-agent 把要求当成页面文案）。
  - **分块策略按类型区分**：`.txt` / `.md` 一般很小，**单次摘要即可**；
    只有 PDF 与超长 HTML 才需要 map-reduce 分块（省一次归并调用）。
  - **上传文件仍需配一句话**（2026-09-15 决策）：**不允许**"只传文件、一句话不说"。
    `AgentChatRequest.message` 保持 `min_length=1`（后端不改），前端也要在输入框为空时禁用提交
    —— 哪怕已经选了附件。理由：这一句话既让 router 能判断意图，
    也能在"文件内容与用户本意不符"时留下兜底线索（纯靠文件猜意图风险太大）。
  - 上传即分配 `@docN`
- **验收**：真实 PDF / HTML / `.md` / **GBK 编码的 `.txt`** 各一份跑通；扫描版 PDF 报错可复现；
  设计令牌人工核对；**把 `.md` 误判成风格源时被 Python 改回 `content`**；多附件归并正确
- **实际交付与偏差（2026-09-15）**：
  - 交付：`app/utils/doc/`（`base.py` / `text_parser.py` / `html_parser.py` / `pdf_parser.py` / `__init__.py`）、
    `app/agents/source/role_policy.py`、`app/agents/source/doc_digest_agent.py`、
    `app/agents/merge/requirement_merge.py`、`app/agents/state.py`（+`StyleSpec` / `RequirementDigest` /
    `RequirementSource` / `FinalRequirement` / `digest_one_line`）、
    `app/prompts/doc_digest_system.md` / `doc_chunk_digest_system.md` / `requirement_merge_system.md`、
    `app/utils/agent/source_store.py`、`app/repositories/agent/source_repository.py`、
    `app/services/source_service.py`、`app/api/agent.py`（+两个接口）、
    `app/utils/utils_check/check_source_upload.py`、`tests/conftest.py` 与 6 个新测试文件（+161 用例）
  - **偏差 1：新增 `GET /api/agent/source/list`** —— 计划里只有 upload。理由：消息正文只存 `@doc1`，
    "别名 → 文件名"的映射在附件表里；没有这个接口，前端（阶段 8）无法渲染 chip，
    上传的附件在界面上等于不存在。
  - **偏差 2：`digest_one_line()` 上提到 `state.py`**，`AgentChatService._digest_summary` 改为复用 ——
    同一条摘要既进提示词又给前端看，两套渲染规则一旦漂移，用户看到的与模型看到的就不一致。
  - **偏差 3：`_load_attachment_targets` 不再采用请求里的 `role`**（以库里那一行为准）——
    否则前端带 `role=style` 就能绕过 Python 的能力否决。
  - **偏差 4：`use_document_style` 由模型判定**（merge 契约里新增的字段）——
    "是否采用文档风格"是需要理解用户意图的判断（"不要用文档配色"），适合模型；
    但**说不要时 Python 真的不注入令牌**，避免用户的话被静默忽略。
  - **偏差 5：digest 结果里的风格结构化取值由 Python 覆盖而非合并** ——
    模型给的色值一律丢弃（只保留 `notes`）。一个幻觉出来的 `#1e293b` 混进风格规范，
    会一路传到 web-agent 的提示词里，而风格复刻里"接近但不对"就是错。
  - **偏差 6：`tests/conftest.py` 覆盖了 pytest 的 `tmp_path`** —— 本机沙箱下
    `%TEMP%/pytest-of-<user>/` 不可写（`WinError 5`），会让所有用 `tmp_path` 的用例在 setup 阶段报错。
    改到工作区内的 `tests/.pytest_tmp/`（已 gitignore），用例代码零改动、沙箱内外行为一致。
  - **实测结论（真实模型 + 真实 HTTP）**：
    `.md` → `@doc1` / `role=content`，且**要求进了 `constraints`、素材进了 `content_points`**
    （"需实现「添加」功能""页面必须响应式"没有被当成页面文案）；
    `.html` → `@doc2` / `role=style`，配色 `['#0f172a', '#e2e8f0']` 与源文件一致（来自解析器）；
    **两页真实 PDF** → `@doc3` 解析成功；**GBK 的 `.txt`** → `@doc4` 解析成功；
    扫描版 PDF → **HTTP 200 + `parse_status=failed`**（不是 5xx）；
    离线段：GBK 解码正确、坏编码明确报错、`.md` 骨架不含代码块里的 `#`、设计令牌六类齐全
  - **已知代价**：upload 是同步接口，长文档会串行多次模型调用（块预算 4000 字符 / 上限 12 块）；
    阶段 6 接进 worker 后应改为异步。附件删除 / 重传接口未做（列已就绪，`list_aliases` 已按不可复用实现）

### 阶段 4｜个人 RAG **占位**（不是实现）—— ✅ 已完成（2026-09-15）
- **产出**：`agents/rag/need_rag.py`（**真实现**）、`agents/rag/retriever.py`（接口 + 桩 provider）
- **要点**：
  - `need_rag` 判定逻辑一期就写完并产出**真实结果**，只是"查"这一步不接
  - **三态语义从第一天起就是真的**：`hit` / `miss`（查了但没有） / `skipped`（判定不需要）
  - `requirement_merge` 必须把 RAG 当成**可选的第 5 个输入块**，桩阶段恒为空，
    merge 逻辑不感知它是否实现 → **二期只替换 provider，不动图结构、不动 prompt 骨架**
- **验收**：`skipped` 路径可复现；把 provider 换成假实现后，`hit` / `miss` 两条路径也能跑通
- **实际交付与偏差（2026-09-15）**：
  - 交付：`app/agents/state.py`（+`RagChunk` / `RagResult`）、`app/agents/rag/need_rag.py`、
    `app/agents/rag/retriever.py`、`app/prompts/need_rag_system.md`、
    `app/utils/utils_check/check_rag.py`、`tests/test_need_rag.py`、`tests/test_retriever.py`，
    以及 `requirement_merge` 的 `rag` 三态入口与提示词"来源 4"段落（+42 用例，共 335 个）
  - **偏差 1：merge 的入口从 `rag_context: str` 换成 `rag: RagResult | None`** ——
    计划里说"桩阶段恒为空"，但**空字符串表达不了三态**：`miss`（需要但没查到）必须变成不确定项，
    `skipped`（本来就不需要）必须什么都不加。用字符串时这两件事长得一模一样，
    而这正是"需求跑偏"最常见的起点。merge 尚未入图，因此这次契约变更没有外部调用方。
  - **偏差 2：桩 provider 在"需要检索"时返回 `miss` 而不是 `skipped`，并且自报可用性** ——
    `RetrieverProvider` 协议要求 provider 声明 `unavailable_reason`（None=能查）。
    这样"检索未接入"与"用户资料里没有"是**两句不同的话**：
    前者用户该等我们做完，后者用户该补资料。桩若伪装成"查了但没有"，
    就是把我们的缺失说成用户的缺失（也正是本次改造一路在防的静默失败）。
  - **偏差 3：`need_rag` 的降级方向做成参数（`on_failure_need`，默认 False）** ——
    判定失败却报"需要检索"，在一期会立刻变成一条"知识库未命中"的告警，把用户引向错误的排查方向。
    二期若希望"宁可多查一次"（漏检索比多检索更伤质量），改这个参数即可 ——
    降级方向是一处显式决策，而不是散落在代码里的隐式行为。
  - **偏差 4：`need=true` 却没给检索词时由 Python 兜底补 query** ——
    放行空串会让二期拿空关键词去检索（必然查不到），而用户看到的是"你的资料里没有"。
    兜底值由槽位/用户原话拼成并**记警告**（兜底质量不如模型，不声不响地兜底会让检索质量无从归因）。
  - **实测结论**：离线六项全通过 —— `skipped` 时 provider **调用次数为 0**（不是"调了返回空"）、
    桩 `miss` 自报未接入、`hit` 保留 L1 来源层级、`user_id=0` 被拒绝、
    merge 的 `miss→uncertainty` / `skipped→静默` 分派正确、工厂返回桩；
    **真实模型两例**：通用需求（番茄钟）→ `need=False`；"按我们公司的品牌色和 VI 规范" →
    `need=True` 且 `query='公司 品牌色 VI规范 产品名'`（关键词而非问句），单次约 1k in / 100 out
  - **已知代价**：一期无法端到端验证检索质量（`hit` 只能用假 provider 跑通）；
    `need_rag` 每次生成多一次模型调用（约 1k in），只在生成路径跑，对话路径不跑

### 阶段 5｜plan-agent —— ✅ 已完成（2026-09-15）
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
- **实际交付与偏差（2026-09-15）**：
  - 交付：`app/agents/common.py`（+`StageBudget` / `BUDGET_BY_DIFFICULTY` / `budget_for` /
    `difficulty_for_file_count` / `resolve_difficulty`）、`app/agents/state.py`（+`PlannedFile` / `FilePlan`）、
    `app/agents/plan/plan_agent.py`、`app/prompts/plan_agent_system.md`、
    `app/models/agent/generation_plan.py`、Alembic 迁移 `b7c1d2e3f4a5`、
    `app/repositories/agent/plan_repository.py`、`app/utils/utils_check/check_plan.py`、
    `tests/test_stage_budget.py`、`tests/test_plan_agent.py`（+52 用例，共 387 个）
  - **预算档位（与人确认，唯一真源在 `BUDGET_BY_DIFFICULTY`）**：

    | difficulty | 文件数上限 | 步数上限 | 输出 token 预算 |
    |---|---|---|---|
    | easy | 1 | 6 | 12k |
    | medium | 4 | 12 | 40k |
    | hard | 8 | 20 | 80k |

  - **偏差 1：`FilePlan` 增加 `entry_file`**（原计划只有 files / difficulty / tech_constraints / assets）——
    预览必须知道打开哪个文件；且它必须在清单里，否则多页面计划会"预览打不开任何东西"。
    入口不在清单时由 Python 改用第一个 markup 文件（再不行用兜底名）。
  - **偏差 2：`resolve_difficulty()` 对"模型没给 / 给了无效难度"按文件数推算并留痕**，
    而不是套用默认档位 —— 否则模型把 `hard` 拼错成 `harder`，预算会被静默压到 medium。
  - **偏差 3：`mark_outcome()` 显式刷新 `update_time`** —— `generation_plan` 没有 ON UPDATE，
    `server_onupdate` 不会出现在 UPDATE 语句里（`generation_task` 上已踩过同一坑）。
  - **增补（2026-09-15 用户要求）：每次生成必须留下「预估 vs 实际」，用于后续优化提示词。**

    | 侧 | 字段 | 来源 |
    |---|---|---|
    | 预估 | `difficulty_declared` / `difficulty` / `planned_file_count` / `budget_steps` / `budget_output_tokens` / `validation_warnings` | plan-agent（阶段 5 写入） |
    | 实际 | `actual_steps` / `actual_file_count` / `outcome_status` / `finished_at` | web-agent 循环（阶段 6 用 `mark_outcome()` 回填） |

    ⚠️ 两侧刻意放在**同一行**：否则"最近 20 次规划准不准"要跨库拼两次查询，
    日常不会有人去查，这张表就白建了。`GenerationPlanRepository.list_recent()` 即对账入口。
  - **实测结论**：离线六项全通过（非法名丢弃 / 难度上调 / 全非法兜底 / 悬空依赖剔除 /
    预算递增 / 兜底契约）；**真实模型**：简单需求（待办清单）**3 次清单完全一致**
    （`index.html`+`style.css`+`script.js`，medium）；复杂需求（企业官网四页 + 数据文件）
    3 次均 hard、7 个文件（仅数据文件名在 `products.json`/`data.json` 间波动）；
    **PG 往返**：写入 → 读回 JSONB 保真 → 回填实际值 → `update_time` 刷新 → 打出对账表 → 清理无残留
  - **已知代价**：`StageBudget` 与 `plan-agent` 都还没有真实消费者（阶段 6 才入图）；
    兜底计划固定单文件 `index.html`（对"多页面但规划失败"只能给一个页面）；
    难度是否长期偏保守需要阶段 6/7 的真实数据（对账表就是为此准备的）

### 阶段 6｜web-agent + 外层编排图 ★主流程打通 —— ✅ 已完成（2026-09-15）
- **产出**：`agents/web/web_agent.py`、`agents/orchestrator.py`、
  `app/prompts/web_agent_system.md`（**去掉交付清单**，只留角色 + 工具语义）、统一入口接口
- **要点**：
  - 手写 `StateGraph`：`model`（bind_tools）→ 条件边（有 `tool_calls`？）→ `ToolNode` → 回 `model`
  - ⚠️ **循环中途不向用户提问**（2026-09-15 决策）：一旦进入循环，只允许两种出口 ——
    **回到 ⑫ 重规划** 或 **失败**，不停下来等人。理由：
    - 循环中途插人，Harness 复杂度陡增：几万 token 的上下文要 checkpoint 持久化、
      要处理"用户永远不回"、还要与 worker 超时 / 僵尸回收 / 并发抢跑协调；
    - 而"信息不足"这件事在 **⑧ ROUTING 就能判定** —— 跑到一半才发现缺信息，
      说明前段判定没做好，应当回头加强 ROUTING，而不是在循环里开一个逃生口。
    - 真正的 LangGraph checkpointer 中断恢复留作后续优化。
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
- **实际交付与偏差（2026-09-15）**：
  - 交付：`app/agents/web/web_agent.py`、`app/agents/orchestrator.py`、
    `app/prompts/web_agent_system.md`、`app/services/agent_generation_service.py`、
    `gen_type="agent"`（`app/schemas/generation_schemas.py`、`app/services/generation_service.py`）、
    `generation_task.sessionUuid` 列 + `sql/scripts/alter_generation_task_session.sql`、
    `app/utils/utils_check/check_web_agent.py`、`tests/test_web_agent.py`、
    `tests/test_orchestrator.py`、`tests/test_agent_generation_service.py`（+51 用例，共 438 个）
  - **偏差 1：MERGE 不单独上报阶段** —— `AgentStage` 没有 `MERGING`，新增枚举值要同步改前端类型；
    归并很快，并入 `PLANNING` 的 `stage_detail`（"正在归并需求并规划文件结构"）。
    新增枚举值留到阶段 8 前端对接时一起做。
  - **偏差 2：`tool_wrapper` 而不是直接注入工具集** —— 工具必须绑定**本次请求的 store**
    （铁律 1）。自检第一版把 `build_agent_tools(另一个 store)` 传进去，
    结果产物写进了那个游魂 store、本轮 store 始终为空、门禁永远不过 ——
    现象看起来像"模型根本不会写文件"。改成包装器 `(store, tools) -> tools` 后，
    故障注入仍可行（"第一次 write_file 必失败"），且委托的是同一个 store。
  - **偏差 3：V1 不做"回到 ⑫ 重规划"** —— 门禁不过时优先**定向补缺**（保留已写文件，最多 2 轮），
    仍不过则失败。重规划要重跑 plan 并丢掉已写文件，代价高且没有数据支持它更有效；
    留给阶段 7 用实测决定。
  - **偏差 4：`on_plan` 回调签名是 `(PlanResult, FinalRequirement)`** ——
    写 `generation_plan` 需要"计划"与"最终需求"两样东西，而它们出自不同节点；
    只给计划的话，写进去的需求字段就只能编一个。
  - **默认客户端由实测确定**（用户要求"先两种都跑一遍再定默认"）：
    同一份交付清单 + 同一次故障注入，两种客户端各跑一次 ——

    | 客户端 | 门禁 | 步数 | 输入 token | 输出 token | 思考 token | 耗时 |
    |---|---|---|---|---|---|---|
    | **思考模式**（默认） | ✅ | 3 | 13462 | **6585** | 217 | 20.0s |
    | 非思考模式 | ✅ | 4 | 19433 | 7614 | 0 | 22.1s |

    两者都做到了三层自主性（自主调工具 / 自主拆解 / 看懂注入的错误并改正）；
    **思考模式更省**（输出少 1029、步数少 1），因此 `DEFAULT_THINKING = True`。
  - **实测（进程内流水线，真模型 + 真 MySQL/PG + 真落盘）**：
    `status=success`、阶段 `done`、19.8s、in=13120 out=4917；产物 `['index.html']` 落盘、
    `_debug_trace.jsonl` 落盘；对账行 = 预估 `easy / 1 文件 / 预算 6 步` ↔ 实际 `2 步 / 1 文件 / success`；
    脚本结束清理无残留。中途还顺带验证了阶段 4 的越权防线：探针用 `user_id=0` 时被检索层当场拒绝。
  - ✅ **HTTP 端到端（③）已跑通**（2026-09-15，重启 worker 后）：
    `create(gen_type=agent)` → 202 / `queued` → 轮询 `retrieving(40%) → planning(55%) → generating(70%) → done(100%)`
    → `status=success`、19246ms、产物 `['index.html']` →
    预览 `GET /preview/13/<task_uuid>/index.html` **HTTP 200（10928 字符）** →
    对账行 预估 `easy / 1 文件 / 预算 6 步` ↔ 实际 `2 步 / 1 文件 / success`。
    （轮询间隔 2s 时可能漏看只持续约 1 秒的 `routing` 格 —— 轮询粒度的正常现象。）
    ⚠️ 前提：**arq worker 必须重启**（它不热重载）；否则 agent 任务会立刻失败并报"生成类型 agent 尚未实现"。
  - **已知代价**：agent 模式尚未进前端（阶段 8）；无 checkpoint 断点恢复；
    提示词与预算档位还需阶段 7 的真实数据校正。

### 阶段 7｜新旧对照 + 退役 ★关键判断 —— ✅ 已完成（2026-09-15）
- **产出**：`app/utils/utils_check/check_compare.py`（真实 HTTP 对照实验 + `--dry-run` 成本护栏 +
  **`--recheck` 离线重判**）、`tests/test_compare_checks.py`、报告 `docs/experiments/stage7_compare_*.json`
- **要点**：用**同一个需求**分别跑旧实现（`generate_multi_file`）与新的 web-agent
- **验收**：记录并对比 —— 成功率 / 产物完整性 / 总 token / 耗时。
  **只有数据支持才退役旧实现，否则回退并重新评估**；旧实现保留为 `simple` 模式兜底
- **收尾**：回写 `docs/generation_module_design.md` 与 `docs/proj_progress.md`
- **实际交付与偏差（2026-09-15）**：
  - **规模（与用户确认后选的"轻量"档）**：3 需求 × 3 模式 × 1 次 = **9 次真实生成**，
    同一账号、**纯文本需求、不带附件**（附件只有 agent 支持，带上等于让旧实现直接弃权）；
    客户端一律按**出厂配置**（single 思考 / multi 非思考 / agent 思考），不人为拉平 —— 否则测的
    就不是用户实际拿到的东西。这一点写进了报告的 `meta.note`。
  - **需求梯度**：r1 单页待办清单 / r2 本地记账本（localStorage）/ r3 企业官网四页 + 数据文件。
  - **数据（重判后的权威口径）**：

    | 模式 | 成功 | 产物完整 | 文件数达预期 | 平均输入 token | 平均输出 token | 每份可用交付输出 token | 平均端到端 |
    |---|---|---|---|---|---|---|---|
    | `single` | 3/3 | 3/3 | **1/3** | **510** | 17642 | 17642 | 59.8s |
    | `multi` | 2/3 | 1/3 | 1/3 | 2303 | 5265 | 15794 | **20.1s** |
    | `agent` | 3/3 | **3/3** | **3/3** | 39055 | **14203** | **14203** | 50.0s |

  - **退役判断（数据支持，不是偏好）**：
    1. **`multi` 退役** —— 它在 **r1（最简单的单页需求）就硬失败**
       （"上一次回答只给出了 index.html，缺少 style.css、script.js"），
       在 r3 上则交出**导航指向 3 个从未生成的页面**的破损产物（`products.html` / `about.html` /
       `contact.html` 全是死链）。根因就是 §1 诊断的那条：**把三个文件当成一次不可分割的赌注**。
       → 代码与 `gen_type` 保留（兼容历史调用），但标注 deprecated，**不再作为前端选项**。
    2. **`agent` 转正为默认** —— 唯一在 r3 交出完整 7 文件（4 个页面 + `products.js` +
       `products.json` + `style.css`）的实现，且每份可用交付的输出 token 最低。
    3. **`single` 保留为"单页极速"路径** —— 结构完整度 3/3，输入 token 仅 agent 的 1/76；
       但对多页需求只能"把一切塞进一个文件"（r3 烧 24651 输出 token、81.4s）。
  - **难度档位校正（用对账数据）**：3 例的 `difficulty_declared == difficulty`（easy/medium/hard），
    `planned_file_count == actual_file_count`（1/3/7），`outcome_status` 全 success；
    **实际步数 2/4/3 远低于预算 6/12/20 —— 预算一次都没触发刹车**。
    结论：**暂不下调预算档位**（它的职责是"防跑飞"的安全网；实测数据不支持收紧，
    收紧反而会让 hard 需求被误掐）。省钱要从"合并工具调用 / 精简提示词"入手。
    另记一条观察：r1 在阶段 5 自检里被判 `medium`、本次被判 `easy` —— 难度判定的档位边界
    会随措辞波动，**3 例不足以定论长期准确性**，继续靠对账表累积。
  - **偏差 1：判据第一版有假阳性，因此加了 `--recheck`**。第一版 `_local_refs()` 把
    `<script>` 里的 JS 模板串（`<img src="${esc(p.image)}">`）也当成静态引用，
    把 `r3/single` 一个**完全正常**的产物判成"引用的本地资源缺失"。
    修正后**新增离线重判模式**：产物还在磁盘上，所以判据变更**不必重跑模型**（否则"修判据"
    就等于再烧一次钱，人就会倾向于将错就错）。同时把那次假阳性固化成
    `tests/test_compare_checks.py`（9 例，含"`<script src=...>` 的 src 必须保留"这个反向约束
    —— 第一版修法若直接删掉整个 script 标签，会把真引用一起删掉）。
  - **偏差 2：`scope`（文件数是否达预期）单列，不计入"产物完整度"**。完整度是**客观缺陷**
    （缺件 / 死链 / 截断 / 预览不可用）；"4 页需求只给了 1 页"是**规模缩水**，
    属于主观预期 —— 两者混在一起会让"single 天生只出一个文件"污染完整度指标。
- **已知代价 / 局限**：
  - **每个组合只跑 1 次**（用户选的轻量档）：成功率只是初步信号，n=1 在统计上不硬。
    但 `multi` 的两条证据是**结构性**的（最简单需求失败 + 死链），不是随机波动，判断仍站得住。
  - **成本账只算了 token，没折算成钱**：agent 输入 token 是 single 的 76 倍，
    而输入单价通常远低于输出 —— 折算后差距会缩小，具体是否可接受**要看真实账单**，
    本次不编造价格。
  - 报告只存指标不存产物内容；产物仍在 `generated/15/`（已被 gitignore）。
  - 本次留下临时账号 `cmp7407438c72`（user_id=14，冒烟）与 `cmpb7b1ad8703`（user_id=15，正式）
    及其 10 条任务与产物，如需清理请手动处理。

### 阶段 8｜前端对接 —— ✅ 已完成（2026-09-15）
- **产出**：会话式 Generate 页（聊天区 + 附件上传 + 需求确认卡片 + 阶段进度条 + 别名 chip）、
  `src/api/agent_api.ts`、`src/types/agent_types.ts`
- **要点**：阶段 0 已先把轮询做掉，本阶段做的是**交互形态升级**
- **阶段 7 带入的两条硬要求**（前端必须一起改，否则退役结论等于没落地）：
  1. **默认 `gen_type` 切到 `agent`**，界面上的"单文件 / 多文件"改成
     "Agent 生成（默认）/ 单页极速（single）"；**不要再暴露 `multi`**（已 deprecated）；
  2. 提交后要能区分**失败**与**停在 `clarifying` 等用户补充**（后者不是失败，不能报红）。
- **实际交付与偏差（2026-09-15）**：
  - 交付：`src/pages/Generate/`（`GeneratePage` + `ChatPanel` + `AttachmentChips` + `RequirementCard` +
    `GenerationProgress` + `QuickGenerateForm` + 各自 `*.module.css` + `generate_utils.ts`）、
    `src/api/agent_api.ts`、`src/types/agent_types.ts`、`src/hooks/useGenerationRunner.ts`、
    `src/utils/agent_session.ts`、`src/components/common/{Button,AuthCard,AuthField}.tsx`；
    两条硬要求均已落地（默认 agent；`clarifying` 用警示色渲染并**停止轮询**）
  - **补齐的过期契约**（不补就写不下去）：`generation_types.ts` 的 `GenType` 缺 `'agent'`、
    `AgentStage` 缺 `'clarifying'`、`GenerateRequest` 缺 `session_uuid`；
    `http.ts` 只支持 JSON body，**multipart 需要一条新分支**（走 FormData 时不能手写 `Content-Type`，
    否则 boundary 丢失、后端 422）
  - **偏差 1：附件上传必须"先有会话"** —— 别名作用域是会话，后端 `upload` 要求 `session_uuid` 存在。
    界面上如实反映：没有会话时上传按钮禁用 + 提示"先发一条消息"（不假装可以上传再静默失败）
  - **偏差 2：刷新回放的"可生成"判据只能前端重算** —— 会话详情接口不返回 `ready_to_generate`，
    因此按后端 `REQUIRED_SLOTS = ("site_kind","features")` 同一口径重算。**这是两处硬编码**，
    后端改必备槽位时前端必须同步（已在代码注释标注）
  - **偏差 3：会话恢复不新增后端接口** —— 后端没有"会话列表"，V1 把 `session_uuid` 存 localStorage
    + `GET /agent/session/{uuid}` 回放；代价是只能恢复"最近一个"会话，登出时与 token 一起清
  - **偏差 4（用户要求）：把 `global.css` 按组件拆分** —— 它已涨到 575 行、混着 5 个页面的类名。
    现在只留设计变量 / 重置 / 页面通用排版，其余下沉到 `*.module.css`（详见 `frontend-react/README.md`）
  - **偏差 5（用户实测发现，前端 bug，同日修复）：提交的 `prompt` 不能夹带闲聊** ——
    前端最初把"第一条用户消息"附在 prompt 末尾当上下文，而用户开场正是"你能做什么？"；
    **worker 的 ROUTING 只读 `task.prompt`（拿不到会话历史，见 `agent_generation_service.execute`）**，
    于是把整段读成能力咨询、判定信息不足，任务停在 `clarifying` ——
    用户刚对着确认卡片点过"开始生成"却被说"信息不够"。
    修复：prompt 只由槽位拼成自洽完整句子（与确认卡片同源），用户原话仅在槽位拼不出东西时兜底，
    且取**最后一条**（`lastUserText`）。已用真实模型复现验证：同一输入由 `clarifying` 变为
    `success`（66629ms）。
    **沉淀的契约**：`create` 的 `prompt` 会被 ROUTING 当作需求本身参与完备度判定，
    调用方必须让它**独立自洽**，不能塞"上下文"类内容。
  - **偏差 6（按用户决定，同日实施）：把会话需求草稿接进 worker 侧** ——
    `orchestrator.run` 一直有 `slots` / `draft_summary` 两个参数，但 `AgentGenerationService.execute`
    **从来没传过**，于是：① worker 的 ROUTING 只看到一句 prompt；② **⑩ need_rag 与 ⑪ merge
    也一直拿到空的草稿摘要**（§3.5 里"MERGE 输入优先级第 2 位 = chat-agent 澄清摘要"从未生效）。
    本次改动：新增 `_load_session()`（会话只查一次，含排队期间的归属复核）、`_load_draft()`
    （把草稿解析成 slots + summary，脏数据按"没有草稿"处理并记警告）、
    `_load_sources()` 改为接收已载入的会话；解析口径抽到 `state.parse_draft()`，
    与 `AgentChatService` 共用一份（避免两处漂移）。
    **实测结论（三案例对照，进程内真模型）**：

    | 案例 | prompt | 会话草稿 | 结果 |
    |---|---|---|---|
    | ① | 被污染（含"你能做什么？"） | 不传（= 修复前） | `clarifying`（失败模式可从后端单独复现） |
    | ② | 被污染 | **传**（本次改动） | **仍 `clarifying`** |
    | ③ | 干净（前端修复后的形态） | 传 | `success`（4 文件 / medium / 2 步 / 74.3s） |

    ⚠️ **诚实结论：本次改动对齐了上下文，但**不能单独**挡住被污染的 prompt** ——
    router 的设计就是要识别"用户在问能力"，而文本里确实还写着"你能做什么？"。
    所以那个 bug 的**实际修复是前端把 prompt 写成自洽需求**（案例③）；
    本改动的独立价值在于让 ⑩ need_rag 与 ⑪ merge 拿到用户已确认的 slots/summary。
    **据此不追加"草稿齐备就放行"的 Python 兜底**：它会削弱生成路径唯一的完备度防线，
    而触发条件（prompt 夹带闲聊）已在前端消除。真正对症的规范是下面这条契约。
  - **沉淀的契约（写进 `generation_module_design.md` §5.2）**：`create` 的 `prompt` 会被
    worker 的 ROUTING 当作**用户需求原文**参与完备度判定；worker 只有它 + 会话草稿 + 附件 digest，
    **没有对话历史**。因此调用方必须让 prompt **独立自洽**，不得夹带闲聊或"上下文"类内容。
  - **验证情况**：`npm run lint` / `typecheck` / `build` 全绿（62 modules，313.87 kB JS / 14.49 kB CSS）；
    Vite dev server 逐个编译新模块均 200；
    **HTTP 层端到端（按前端实际调用顺序与载荷，真实模型）**：
    模糊需求 chat → `needs_clarification`；multipart 上传 `.md`（字段名 `session_uuid`/`file`）→ `@doc1`；
    `source/list` 返回别名与文件名；带附件 chat → `ready=True` 且 **style 槽位取自文档**
    （"极简风格，白色背景，元素统一 8px 圆角"）；会话回放 4 条消息角色正确；
    `create(gen_type=agent, session_uuid)` **202** → `routing 10% → retrieving 40% → planning 55% →
    generating 70% → done 100%` → `success` / 20552ms / 3 文件 → 签票 + 预览 **HTTP 200**
  - **已知代价 / 遗留**：浏览器内的交互与观感**仍需人工点一遍**（人工验证清单在
    `frontend-react/docs/proj_progress.md` 的「会话式生成页」模块）；
    历史消息里的 `@docN` chip 不会在刷新后重现（会话详情接口不返回消息级 attachments，设计如此）；
    无会话列表接口；前端仍无自动化测试


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
21. ⚠️ **`.txt` 的编码不能假定是 UTF-8** —— 中文 Windows 记事本默认存 GBK/GB18030，
    按 UTF-8 硬读会抛 `UnicodeDecodeError`；而用 `errors="ignore"` 更糟：整篇变乱码、
    还"成功"解析出一份垃圾 digest。必须按 **UTF-8 → GB18030 → 明确报错** 的顺序回退
    （GB18030 是 GBK 的超集，覆盖绝大多数简体中文遗留文件），
    都失败才报错 —— 绝不静默产出空需求。

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
| 2026-09-15 | 阶段 2：三类输入的意图路由（真实模型） | 纯咨询 / 模糊需求（"你看着办"）/ 明确需求 | 分别得到 `chat` / `generate+needs_clarification` / `generate+ready`（5 个槽位全中，`need_persistence=True` 三态正确）；单次 1.4~2.0s，`reasoning=0` 证实用的是非思考客户端 | **通过**。模型的 `reason` 甚至正确区分了"授权决定具体方案"与"连主题都没定" |
| 2026-09-15 | 阶段 2：槽位跨轮累积（真实模型） | 已有 `site_kind=单页展示, features=[添加待办]`，再补"界面要极简一点，白底就行" | 合并结果保留旧槽位，并新抽取到 `style=极简白底` | **通过** —— 证明"新抽取为空时不覆盖旧值"的合并语义有效 |
| 2026-09-15 | 阶段 2：多轮对话落库与回放（真实 HTTP + PG） | 两轮对话（模糊 → 补齐） | 4 条消息、角色序列 `user/assistant/user/assistant`、顺序正确；会话草稿 summary 正确；`ready_to_generate` 由 false 变 true | **通过** |
| ✅ | 阶段 2：意图路由 + chat-agent —— **已完成（2026-09-15）** | — | — | — |
| 2026-09-15 | 阶段 3：解析层（离线，真实字节） | GBK `.txt` / 坏编码 `.txt` / `.md` / HTML / 扫描版 PDF / 手写两页 PDF | GBK 正确解码为 `gb18030`；坏编码**明确报错**（不静默产乱码）；`.md` 骨架不含代码块里的 `#`；HTML 令牌 `#0f172a`/`Inter`/`8px`/`@media×1` 与源文件一致；扫描版与坏 PDF 各自报出可执行的原因 | **通过** —— 解析层脱离模型即可断言，这是"解析与理解分两层"的直接收益 |
| 2026-09-15 | 阶段 3：上传全链路（真实模型 + 真实 HTTP） | `.md`（需求说明书）/ 设计规范 `.html` / 两页真实 PDF / GBK `.txt` / 扫描版 PDF | `@doc1` content、`@doc2` style（配色取自解析器）、`@doc3`/`@doc4` success；扫描版 **HTTP 200 + `parse_status=failed`**；列表返回别名与文件名 | **通过** —— 要素材/要求分流正确（要求进了 `constraints`），且坏文件不产生 5xx |
| ✅ | 阶段 3：文档解析 + digest + 上传 + merge —— **已完成（2026-09-15）** | — | — | — |
| 2026-09-15 | 阶段 4：三态语义与安全边界（离线） | 假 provider / 桩 provider / 非法 user_id / merge | `skipped` 时 provider 调用次数 **0**；桩 `miss` 自报"尚未接入"；`hit` 保留 L1/L2；`user_id=0` 被拒绝；merge `miss→uncertainty`、`skipped→无提示` | **通过** —— "不适用"与"失败"在代码层面被彻底分开 |
| 2026-09-15 | 阶段 4：检索必要性判定（真实模型） | 通用需求（番茄钟）/ 指代私人资料（"按我们公司的品牌色和 VI 规范"） | 前者 `need=False`（理由：通用功能页面，需求已完整写在输入中）；后者 `need=True`、`query='公司 品牌色 VI规范 产品名'` | **通过** —— 检索词是关键词而非问句；单次约 1k in / 100 out |
| ✅ | 阶段 4：个人 RAG 占位 —— **已完成（2026-09-15）** | — | — | — |
| 2026-09-15 | 阶段 5：文件清单稳定性与难度判定（真实模型，各 3 次） | 待办清单（简单）/ 企业官网四页 + 数据文件（复杂） | 简单：3 次均 `medium` 且清单完全一致（`index.html`+`style.css`+`script.js`）；复杂：3 次均 `hard`、7 个文件，仅数据文件名在 `products.json`/`data.json` 间波动 | **通过** —— 清单稳定是门禁可用的前提；难度与文件数匹配（未出现 easy 交 6 个文件） |
| 2026-09-15 | 阶段 5：`generation_plan` 往返与对账（真实 PG） | 规划写入 → 读回 → 回填实际值 → 对账查询 | JSONB 保真；`mark_outcome()` 写入实际步数/文件数/结果/完成时间并刷新 `update_time`；`list_recent()` 打出"模型难度 vs 最终难度 vs 计划文件 vs 预算步数 vs 实际步数"对照表；脚本结束清理无残留 | **通过** —— 预估与实际同行的结构可用，这是后续优化提示词的证据来源 |
| ✅ | 阶段 5：plan-agent + `StageBudget` + `generation_plan` —— **已完成（2026-09-15）** | — | — | — |
| 2026-09-15 | 阶段 6：三层自主性 + 客户端对照（真实模型，同一清单 + 故障注入） | "第一次 write_file 必失败" + 待办清单需求 | 思考模式：门禁 ✅、3 步、out 6585、20.0s；非思考：门禁 ✅、4 步、out 7614、22.1s；两者都自主调工具、自主拆解、看懂错误并改正 | **通过** —— 默认客户端定为**思考模式**（数据支持，而非偏好） |
| 2026-09-15 | 阶段 6：进程内完整流水线（真模型 + 真 MySQL/PG + 真落盘） | 一个待办清单需求，`gen_type=agent` | `success` / `done` / 19.8s / in=13120 out=4917；产物 `['index.html']` 与 `_debug_trace.jsonl` 落盘；对账行 预估 `easy,1 文件,6 步` ↔ 实际 `2 步,1 文件,success`；清理无残留 | **通过** —— "编排 → 落库 → 落盘 → 回填实际值"整条链路可用 |
| 2026-09-15 | 阶段 6：越权防线（真实链路顺带验证） | 探针用 `user_id=0` | 检索层当场 `ValueError`："检索必须带上有效的 user_id（越权风险）" | **通过** —— 阶段 4 的安全边界在真实链路上确实拦得住 |
| 2026-09-15 | 阶段 6：**端到端 HTTP（③）**（真实 API + worker + Redis） | `create(gen_type=agent)` + 待办清单需求 | 202/`queued` → 轮询 `retrieving→planning→generating→done` → `success` 19246ms → 产物 `['index.html']` → 预览 **HTTP 200（10928 字符）** → 对账 预估 `easy,1 文件,6 步` ↔ 实际 `2 步,1 文件,success` | **通过** —— 主流程彻底打通（前提：worker 必须重启，arq 不热重载） |
| ✅ | 阶段 6：web-agent + 编排图 + 接线 + 端到端 —— **全部完成（2026-09-15）** | — | — | — |
| ✅ | 阶段 0：异步骨架 —— **已完成（2026-09-15）**，见上方两条实测记录 | — | — | — |
| 2026-09-15 | 阶段 7：三模式对照（真实 API + worker + Redis，9 次生成） | 3 需求（单页/多文件/多页+数据文件）× `single`/`multi`/`agent` × 1 次 | `single` 3/3 成功、完整 3/3、文件数达预期 1/3、in 510 / out 17642；`multi` **2/3**（r1 硬失败）、完整 **1/3**、in 2303 / out 5265；`agent` **3/3 成功**、完整 **3/3**、文件数达预期 **3/3**、in 39055 / out 14203 | **通过** —— 数据支持"`multi` 退役、`agent` 转正、`single` 留作单页路径" |
| 2026-09-15 | 阶段 7：`multi` 复杂需求产物诊断 | r3（企业官网四页 + 数据文件） | 交付 `index.html`+`style.css`+`script.js`，但入口里 9 处 `href` 指向 `products.html` / `about.html` / `contact.html` —— **三个页面从未生成**，导航全是死链（预览 HTTP 200 也掩盖不了） | **通过（判为缺陷）** —— 证明"HTTP 200 + 有产物"不等于交付可用，必须查引用完整性 |
| 2026-09-15 | 阶段 7：判据假阳性与离线重判 | 第一版 `_local_refs()` 扫全文本 | `r3/single`（单文件官网）把 JS 模板串 `${esc(p.image)}` 当成缺失文件 → 误判不完整；修正为"只扫标记语言、跳过 script/style 正文与模板表达式"后重判为**完整**，且 `multi` 的死链结论不变 | **通过** —— 并因此把"判据可离线重判"做成 `--recheck`：修正判据不必重跑模型 |
| | 阶段 8：前端对接（会话式 Generate 页） | | | |
| 2026-09-15 | 阶段 8：前端契约与端到端（真实 API + worker + 真实模型） | 按前端**实际调用顺序与载荷**：chat → multipart 上传 `.md` → `source/list` → 带附件 chat → 会话回放 → `create(agent, session_uuid)` → 轮询 → 签票预览 | 模糊需求 `needs_clarification`；上传得 `@doc1`（`parse_status=success`）；带附件 chat `ready=True` 且 **style 槽位取自文档**；回放 4 条消息角色正确；create **202** → `routing→retrieving→planning→generating→done` → `success` 20552ms / 3 文件 → 预览 **HTTP 200** | **通过** —— 前端契约（含 multipart 字段名）与后端一致；`lint`/`typecheck`/`build` 全绿 |
| ✅ | 阶段 8：前端对接 —— **已完成（2026-09-15）** | — | — | — |
| 2026-09-15 | 阶段 8：**两处 router 的上下文核查**（用户提出） | 读码 + 三案例实测（进程内真模型） | API 侧 `intent_router` 与 `chat-agent` 拿到**同一份 history**（同一对象、同一预算）；worker 侧 ROUTING **只拿 prompt**（history 空、attachments 空、slots 此前也没传）。案例①污染 prompt+无草稿→`clarifying`；②污染+有草稿→**仍 `clarifying`**；③干净+有草稿→`success` | **部分通过** —— 会话草稿已接上（need_rag/merge 受益），但它**不能单独**挡住被污染的 prompt；bug 的实际修复在前端 prompt |

---

## 7. 相关文档

- 现有设计约定：`docs/generation_module_design.md`（§3.2 agents 契约、§7 DeepSeek 约束）
- 进度记录：`docs/proj_progress.md`
- 问答记录：`docs/QA.md`
- 上游 issue：[DeepSeek-V3 #1376 tool_choice 限制](https://github.com/deepseek-ai/DeepSeek-V3/issues/1376)
- 上游 issue：[DeepSeek-V3 #806 无 embedding 接口](https://github.com/deepseek-ai/DeepSeek-V3/issues/806)
- arq 官方文档（v0.28.0）：[arq-docs.helpmanual.io](https://arq-docs.helpmanual.io/)
  —— 关键：**"Jobs may be called more than once!"**（悲观执行，任务在成功/失败前不离开队列）
