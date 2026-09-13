# 生成模块设计约定 —— backend-uv-fastapi

> 本文件记录"Web 生成"模块（单文件 HTML 模式 / 多文件模式）的设计约定。
> 后续步骤（表设计、agents 编排、接口实现）均以此为准；改动约定请同步更新本文件。
> 创建时间：2026-09-12

## 1. 模块定位与数据流

**一句话**：一次生成 = **一条任务记录 + 一个磁盘目录**。

```
输入   已登录用户(user_id) + 自然语言需求(prompt) + 生成类型(gen_type: single | multi)
  ↓
处理   agents 层编排 deepseek-flash（单文件走一条 LangChain 链，多文件走一张 LangGraph 图）
  ↓
输出   ① 磁盘：generated/{user_id}/{task_uuid}/ 下一批文件
       ② MySQL：generation_task 一条记录（状态 / 类型 / 产物路径）
```

模型：`deepseek-flash`（DeepSeek-V4.1-Flash，2026-09-10 发布，OpenAI 兼容协议）。
框架：LangChain 1.x（链）+ LangGraph 1.x（多文件编排）。

## 2. 目录落位

```
backend-uv-fastapi/
├─ app/
│  ├─ api/generation.py                     # 路由层（表现层）
│  ├─ agents/                               # ★ LLM 编排层（Python 包，有 __init__.py）
│  │  ├─ single_html_flow.py                #   单文件模式：一条 LangChain 链
│  │  └─ multi_file_graph.py                #   多文件模式：LangGraph 状态图
│  ├─ prompts/                              # ★ 提示词目录（只放 md，不是 Python 包）
│  │  ├─ single_html_system.md
│  │  └─ multi_file_system.md
│  ├─ core/
│  │  ├─ settings_base.py                   #   配置基类（.env 定位 + 共用 model_config）
│  │  ├─ llm_config.py                      #   模型配置 Settings
│  │  ├─ llm_client.py                      #   模型客户端（生成用 + 结构化输出用，共两个）
│  │  └─ storage_config.py                  #   产物目录配置 Settings
│  ├─ models/generation_task.py             #   ORM 实体
│  ├─ schemas/generation_schemas.py         #   请求 / 响应 DTO
│  ├─ repositories/generation_repository.py #   数据访问层
│  ├─ services/generation_service.py        #   业务层
│  └─ utils/                                # 通用工具（按用途分子包）
│     ├─ db/                                #   数据库：create_all_table.py（开发期建表）
│     ├─ jwt/                               #   认证：security.py（哈希/JWT）、parse_token.py（登录依赖）
│     ├─ weg_gen/                           #   生成：prompt_loader.py、code_extractor.py、file_writer.py
│     └─ utils_check/                       #   开发期自检：check_llm.py、check_langgraph.py、check_extractor.py
├─ generated/                               # ★ 运行产物目录（git 忽略，代码自动创建）
└─ docs/generation_module_design.md         #   本文件
```

## 3. 分层与调用方向

**规则**：`api → services → {repositories, agents} → {MySQL, LLM / 磁盘}`

| 层 | 负责 | **不负责** |
|---|---|---|
| `api/` | 收 HTTP、鉴权（`get_current_user`）、Pydantic 校验 | 任何业务规则与 LLM 调用 |
| `services/` | 业务规则：状态流转、失败记录、配额/权限 | prompt 拼装、StateGraph、`model.invoke` |
| `repositories/` | 只做 CRUD，查询一律带 `is_delete == 0` | 业务规则 |
| `agents/`（新增层） | **怎么把需求变成文件**：提示词组装、链/图编排 | 不碰数据库、不碰 HTTP、不写磁盘 |
| `utils/` | 无状态工具：读 prompt、解析围栏、安全写文件 | 不编排流程 |

### 3.1 为什么单独开一层 `agents/`

让"换模型 / 改编排策略"只动 `agents/`，"加配额 / 改状态流转"只动 `services/`。
若把 LangChain/LangGraph 直接写进 `services/`，两类改动会缠在一起。

### 3.2 agents 层的接口约定（**硬约定**）

agents 层的函数是**纯**的：给它需求，还它文件字典；**返回之前不落盘、不落库**。

```python
def generate_single_html(prompt: str) -> dict[str, str]:
    """返回 {"index.html": "<!DOCTYPE html>..."}"""

def generate_multi_file(prompt: str) -> dict[str, str]:
    """返回 {"index.html": "...", "style.css": "...", "script.js": "..."}"""
```

落盘（`utils/weg_gen/file_writer.py`）与落库（`repositories`）**都由 `service` 统一做**。
好处：`agents/` 可脱离 MySQL 与 FastAPI 单独测试（步骤 11 的 mock 测试依赖这一点）。

### 3.3 完整调用链

```
POST /api/generation/create
 └─ api/generation.py                       鉴权 + 请求体校验
     └─ services/generation_service.py
         ├─ repositories/generation_repository.py   → 建任务(status=running) / 更新状态
         ├─ agents/single_html_flow.py
         │   或 agents/multi_file_graph.py           → deepseek-flash
         └─ utils/weg_gen/code_extractor.py + file_writer.py → 写 generated/…
```

## 4. 产物目录与 ID 设计

**目录约定**：`generated/{user_id}/{task_uuid}/`

| 模式 | 目录内容 |
|---|---|
| `single` | `index.html` |
| `multi` | `index.html` + `style.css` + `script.js` |

**预览 URL**：`/preview/{user_id}/{task_uuid}/index.html`（由 `StaticFiles` 挂载，见步骤 9）

**ID 设计**：表里 **自增 `id` 主键 + 唯一列 `task_uuid`** 两者并存，**目录名与 API 路径一律用 `task_uuid`**（`uuid4().hex`）。

理由：
1. **安全**：自增 id 可枚举，别人能顺着 `/preview/1/2/index.html` 猜他人作品；uuid 猜不到。
2. **顺序**：自增 id 必须先进库才拿得到，会把"建目录"绑死在"插数据库"之后；uuid 可先在内存生成。
3. **可重试**：同一次生成失败重试可复用同一 uuid 覆盖目录，不产生孤儿目录。

> ⚠️ 文件名必须由**我们**钉死，不能指望模型自觉：实测模型会把 `style.css` 写成 `styles.css`。
> 因此提示词里把文件名写成常量（步骤 4），并在图中加 `validate` 节点校验（步骤 8）。

## 5. 接口约定

沿用用户模块"**模块名做 prefix、动作做子路径**"的风格（`/api/user/register`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/generation/create` | 创建并执行生成（同步返回结果） |
| GET | `/api/generation/list` | 我的生成历史（分页） |
| GET | `/api/generation/{task_uuid}` | 查单个任务详情 |
| GET | `/api/generation/stream/{task_uuid}` | 流式进度（SSE，步骤 10） |

⚠️ **路由顺序**：`/list` 必须声明在 `/{task_uuid}` **之前**，否则 FastAPI 会把 `list` 当成一个 `task_uuid` 匹配掉。

- 路由 `prefix="/generation"`，`tags=["生成"]`；`main.py` 中 `include_router(..., prefix="/api")`
- `gen_type` 枚举值固定为字符串 `"single"` / `"multi"`（不用数字，保证可读）
- 状态机：`running` → `success` / `failed`

## 6. 命名与编码约定（沿用用户模块既有习惯）

- 数据库列名**驼峰**：`genType`、`userId`、`isDelete`、`createTime`…
  Python 属性 snake_case + `mapped_column("genType", ...)` 显式映射
- 表名：`generation_task`
- 逻辑删除：所有查询过滤 `is_delete == 0`
- 三时间列：`editTime` / `createTime` / `updateTime`（`server_default=text("CURRENT_TIMESTAMP")`）
- 建表：`Base.metadata.create_all()`，**ORM 模型是唯一真源**（项目根 `sql/` 不参与建表）
- docstring：Google 风格；注释用中文
- 文件名：模块 `snake_case`；`_config.py` / `_schemas.py` / `_repository.py` 后缀
- 敏感信息：`DEEPSEEK_API_KEY` 等一律走 `.env`（已被根 `.gitignore` 的 `**/.env` 忽略）

## 7. DeepSeek 接入约束（重要 · 实测结论）

模型：`deepseek-flash`。以下内容为 2026-09-12 在本项目实测确认的行为，
**直接决定 `agents/` 层怎么写**，改动相关代码前务必先看这一节。

### 7.1 思考模式默认开启

- 开关（OpenAI 兼容协议）：`{"thinking": {"type": "enabled" | "disabled"}}`；
  经 OpenAI SDK 传递时要放进 `extra_body`。
- 思考强度：`reasoning_effort`，取值 `low` / `high` / `max`（默认 `high`）。
- 思考内容通过 `reasoning_content` 返回，且**计入 output token**，
  因此 `llm_max_tokens` 要给足（本项目默认 16384，避免"代码还没写完就被截断"）。

### 7.2 思考模式下 `temperature` 静默失效

`temperature` / `presence_penalty` / `frequency_penalty` 在思考模式下**不报错但完全无效**。
调参只能调 `reasoning_effort`。因此 `.env` 的 `LLM_TEMPERATURE` **仅对非思考模式客户端生效**。

### 7.3 "强制 schema" 与思考模式互斥

- LangChain `with_structured_output(schema)`（默认 `method="function_calling"`）依赖把
  `tool_choice` 强制指向某个函数，而**思考模式不支持这种形式**，实测报错：
  `400 Thinking mode does not support this tool_choice`。
- 改用 `method="json_mode"` 可绕过该限制，但 DeepSeek JSON 模式要求 prompt 中出现 "json" 字样，
  且**只保证是合法 JSON、不校验字段**：实测模型返回 `{"html":…,"css":…,"js":…}`，
  并不符合 `FilePlan` 的 `{"files": [...]}`。
- 结论：**结构化输出必须使用非思考模式客户端 + 默认的 tool calling**。

### 7.4 结论：两个模型客户端（`app/core/llm_client.py`）

| 客户端 | 思考模式 | 用途 |
|---|---|---|
| `llm_client` | 按 `.env` 配置（默认 `enabled`） | 代码生成（质量优先）：单文件模式、多文件的每个文件 |
| `llm_structured_client` | **强制 `disabled`** | 结构化输出：步骤 8 的 `plan` 节点（生成文件清单） |

## 8. 边界（不属于本模块）

- 不负责前端展示与代码编辑器（前端职责）
- 不负责生成产物的长期存储治理（当前为本地目录，后续可换对象存储）
- 不做对话式多轮修改（V1 只做"一句话 → 一次性产出"）
- **预览静态资源不做鉴权**：`/preview/{user_id}/{task_uuid}/index.html` 只要 URL 不泄露就能打开
  （以 task_uuid 不可猜作为"能力凭证"，类似"知道链接即可访问"）。生产环境应改为带鉴权的路由或签名 URL。

## 9. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-12 | 初版：确定分层、agents 层职责与纯函数接口约定、task_uuid、接口路径与命名约定 |
| 2026-09-12 | 新增 §7 DeepSeek 接入约束（思考模式开关、temperature 失效、双客户端）；§4 补充"文件名必须钉死"；§2 补充 `settings_base.py` |
| 2026-09-12 | §8 补充"预览静态资源不做鉴权"这一已知取舍 |
