# Agent Instructions for Web Generation Platform

## 1. 项目概述 (Project Overview)
本仓库用于**复刻"Web 生成平台"**：帮助用户通过 AI 快速生成 Web 应用。

技术路线（**当前与后续开发均以此为准**）：
- **frontend-react**：前端 —— React 19 + TypeScript + Vite 8（React Router、npm、ESLint）
- **backend-uv-fastapi**：后端 —— Python >= 3.12 + uv + FastAPI

> ⚠️ 遗留说明：仓库中曾规划 Spring Boot + LangChain4j 后端（`backend/` 目录，含 Java/Maven 代码），该方案**已废弃**。`backend/` 已从 git 移除跟踪并在根 `.gitignore` 中忽略，仅本地保留作为参考。**不要**在 `backend/` 上继续开发，也不要将其重新纳入提交。

## 2. 常用命令 (Common Commands)

### 前端（在 `frontend-react/` 下执行）
- 安装依赖：`npm install`（新增依赖后提交 `package-lock.json`）
- 本地开发：`npm run dev`（开发代理 `/api → http://127.0.0.1:8000`，见 `vite.config.ts`）
- 生产构建：`npm run build`（先 `tsc` 类型检查再出产物 `dist/`，不入库）
- 类型检查：`npm run typecheck`
- 本地预览构建产物：`npm run preview`
- 代码检查：`npm run lint`

### 后端（在 `backend-uv-fastapi/` 下执行）
- 同步依赖：`uv sync`
- 本地启动：`uv run fastapi dev`（入口由 `pyproject.toml` 的 `[tool.fastapi]` 指定）
- 依赖变更后：修改 `pyproject.toml` 并重新执行 `uv sync` / `uv lock`，**提交 `uv.lock` 锁定版本**

## 3. 目录与代码定位

### 前端 `frontend-react/`
- `src/main.tsx`：应用入口；`src/App.tsx`：根组件（仅路由装配，`react-router-dom`）
- 分层结构（详见 `frontend-react/README.md`）：
    - `src/pages/`：路由级页面（每页一个目录，如 `Home/`、`Generate/`）
    - `src/api/`：接口调用层（`http.ts` 统一请求封装，一个后端路由模块对应一个文件）
    - `src/components/layout|common`：布局与可复用 UI 组件
    - `src/hooks/`：通用自定义 hooks；`src/types/`：与后端 models 对齐的数据类型
    - `src/utils/`：工具函数；`src/styles/global.css`：全局样式与设计变量
- `public/`：公共静态资源（favicon、icons 等）
- 配置：`vite.config.ts`（别名 `@/ → src/`、`/api` 开发代理）、`eslint.config.js`、`tsconfig*.json`

### 后端 `backend-uv-fastapi/`
- `app/main.py`：FastAPI 应用入口（`app = FastAPI()`）与现有示例 Pydantic 模型（入口由 `pyproject.toml` 的 `[tool.fastapi]` 指向 `app.main:app`）
- 分层结构（调用方向：`api` → `services` → `repositories`）：
    - `app/api/`：API 路由层（表现层）
    - `app/core/`：核心配置与工具（配置、数据库会话、依赖注入）
    - `app/models/`：数据模型层（Pydantic 模型与 ORM 实体）
    - `app/repositories/`：数据访问层（CRUD 操作）
    - `app/services/`：业务逻辑层
    - `app/utils/`：通用工具函数
- `tests/`：pytest 测试目录，运行 `uv run pytest`；测试用 dev 依赖在 `pyproject.toml` 的 `[dependency-groups]` 中声明
- 需求简单时可先直接在 `app/main.py` 内新增路由与模型；随着接口增多，按上述分层拆分到对应目录并保持入口清晰
- 命名：包目录使用 `snake_case`

## 4. 编码规范与风格 (Code Style & Conventions)

### Python / FastAPI
- 命名：模块、变量、函数使用 `snake_case`；类（含 Pydantic 模型）使用 `PascalCase`；常量使用 `UPPER_SNAKE_CASE`
- 路由设计遵循 RESTful 风格；请求体与响应体使用 Pydantic `BaseModel` 做类型校验（参考 `main.py` 中的 `Item`）
- 接口需提供清晰的中文描述（`Field(description=...)`），保持 `/docs` 文档可读
- 如后续接入数据库：一律使用 ORM（如 SQLAlchemy）或参数化语句，**禁止拼接 SQL 字符串**

### React / 前端
- 组件文件与导出使用 `PascalCase`（`.tsx`，如 `HomePage.tsx`）；变量与函数使用 `camelCase`；常量 `UPPER_SNAKE_CASE`
- 组件采用函数组件 + Hooks 写法；与后端交互的类型放 `src/types/`（字段与后端 Pydantic 保持一致）
- 类型导入使用 `import type`；路径别名 `@/` 指向 `src/`
- 样式：设计变量统一在 `src/styles/global.css`，页面样式可同目录建 `.module.css` 或组件内联类

## 5. 敏感信息与安全规则 (Security)
- 数据库密码、API Key 等敏感信息**严禁硬编码**：通过环境变量注入，或放在本地配置文件（如 `.env`、`application-local.yml`），并确保被 `.gitignore` 忽略、不进 git
- 前端 `.env*.local`、后端 `.env`、`.venv/`、`__pycache__/` 等均不入库（根 `.gitignore` 已统一维护）
- 若后端接入 MySQL：数据源仅指向本地业务库，**绝对不要修改、删除或查询 `mysql`、`sys`、`performance_schema` 等系统数据库**

## 6. Git 与提交规范 (Git & Commit Rules)
- **主分支**：`main`
- **提交信息格式**：遵循约定式提交（Conventional Commits）
    - `feat:` 新功能
    - `fix:` 修复 Bug
    - `docs:` 文档更新
    - `chore:` 构建/工具变动
    - 示例：`feat(user): 添加用户注册接口`
- **提交前检查**：确认运行产物与废弃目录（`node_modules/`、`dist/`、`.venv/`、`__pycache__/`、`backend/`、本地配置文件等）未被带入提交

## 7. AI 助手工作流 (Agent Workflow)
1. **理解需求**：明确要修改或新增的功能（前端页面或 FastAPI 接口）。若需求涉及**已有功能的修改、新增、删除**，先查看对应项目的 `docs/proj_progress.md` 进度记录（读取当前模块状态与关键决策，规则见 §10）。
2. **定位代码**：前端在 `frontend-react/src`；后端在 `backend-uv-fastapi/app`（入口 `app/main.py`，分层见 §3）。涉及 `backend/`（废弃 Spring Boot）的需求一律视为无效，不予处理。
3. **编写/修改代码**：遵循上述编码规范；改动依赖时同步 `uv.lock` / `package-lock.json`。
4. **本地验证**：提供用于测试的 curl 命令（FastAPI 默认 `http://127.0.0.1:8000`）或测试用例。
5. **提交代码**：按照规范的格式得到提交信息，待用户确认后再提交。

## 8. 功能教学分步交互流程 (Step-by-Step Feature Guidance)

当你需要指导用户完成某个功能（以下简称"目标功能"）时，请严格遵循以下分步交互流程：

### 第1步：制定初步实施计划

根据你对目标功能的理解，先梳理出完成该功能所需的关键阶段，形成一份从 步骤1 到 步骤N 的概要计划。

每个步骤只需用一两句话说明核心任务或产出，确保用户能快速理解整体路径。

### 第2步：与用户确认计划
给出以下两个选项，等待用户选择：

- **选项A**：用户直接确认，同意按此计划推进。
- **选项B**：用户提出修改意见（如调整顺序、增删步骤、细化某环节等）。

→ 若用户选择 选项B，则根据其反馈修改计划，重新生成新的步骤1～N，并再次展示，重复本步骤，直到用户明确选择 选项A 为止。

### 第3步：按序执行并讲解每个步骤
一旦计划确认，即从 步骤1 开始，依次推进。对当前步骤（记为 步骤X）执行以下操作：

1. 提供该步骤的详细操作指南，包括具体做法、关键要点、注意事项及预期结果。
2. 讲解结束后，给出两个选项供用户选择：
   - **选项1**：用户确认已理解并完成该步骤，可以进入下一步。
   - **选项2**：用户提出疑问或遇到困难，需要进一步解释或调整。

→ 若用户选择 选项2，则：

1. 首先耐心解答其疑问；
2. 如果发现当前步骤的讲解存在遗漏或错误，则修订该步骤的指导内容，并重新讲解；
3. 修订后再次展示选项1和选项2，直到用户选择 选项1 为止。

### 第4步：循环直至全部完成
重复第3步，依次处理步骤2、步骤3……直到步骤N全部被用户确认完成。此时整个功能的教学与实施过程结束。

### 补充规则

- 尽量避免只使用专业术语而不解释。
- 若用户在任何时候提出偏离原计划的需求，可回到第2步重新协商计划。
- 每一步完成后，可主动询问用户是否希望回顾或微调后续计划，以增强灵活性。

## 9. 对话问答记录规则 (Conversation Q&A Logging)

当用户提出"总结 / 记录 / 整理本次对话中的问答（QA）"类需求时，遵循以下规则：

- **存放位置**：根据问答所属的技术栈，写入对应项目目录下的 `docs/QA.md`：
    - 后端（Python/FastAPI/数据库等）问答 → `backend-uv-fastapi/docs/QA.md`
    - 前端（React/TypeScript/Vite 等）问答 → `frontend-react/docs/QA.md`
    - 若涉及前后端通用概念（架构、HTTP、git 等），按问答主要语境归入一侧，或询问用户
    - `docs/` 目录或 `QA.md` 文件不存在时，先创建再写入
- **文件已有内容时**：不要覆盖或删除旧内容，在文件末尾**继续追加**，条目编号接续现有最大编号（如已有 Q1–Q18，则新条目从 Q19 开始）。
- **问题相似时**：若新问答与文件中已有条目主题相似（比对 tag 或问题语义），**合并进已有条目**（在原有答案中补充新信息），避免重复记录同一问题。
- **格式**：沿用文件内已使用的格式（编号 `Qn:` + `tag:` + 简答 `An:`）；文件为空或首次创建时从 `Q1` 开始，每项包含：编号、问题、tag（主题标签）、简答。

## 10. 开发进度记录规则 (Project Progress Logging)

当用户提出"总结 / 记录 / 同步本次对话的开发进度"类需求时，遵循以下规则：

- **存放位置**：根据进度所属的技术栈，写入对应项目目录下的 `docs/proj_progress.md`：
    - 后端进度 → `backend-uv-fastapi/docs/proj_progress.md`
    - 前端进度 → `frontend-react/docs/proj_progress.md`
    - 通用进度（架构、联调等）按主要语境归入一侧，或询问用户
    - `docs/` 目录或 `proj_progress.md` 文件不存在时，先创建再写入
- **已有内容时**：不要整篇覆盖，按下方格式**更新对应模块条目**（修改状态、补充新完成项、更新验证情况），并刷新"最近更新时间"；新增模块则在"模块进度"下追加新条目。
- **查询要求**：用户提出对项目功能进行**修改、新增、删除**等操作时，必须先查看对应项目 `docs/proj_progress.md` 的内容，基于已有进度与关键决策推进；文件不存在时正常处理，并可在总结进度时创建。

### 推荐的进度总结格式（各项目统一沿用此模板）

```markdown
# 项目进度 —— backend-uv-fastapi / frontend-react（按实际填写）

> 本文件用于跨会话同步开发进度。每次总结进度时按此格式更新。
> 最近更新时间：YYYY-MM-DD

## 1. 模块进度

### 模块：<模块名>（如：用户模块）
- **状态**：未开始 / 进行中 / 已完成 / 已暂停
- **功能范围**：一句话说明该模块做什么
- **已交付内容**：
  - 接口 / 页面：列出已完成的路由或页面
  - 核心文件：`app/...` 或 `src/...`（列出关键路径）
- **关键决策**：该模块实现时定下的约定（如：建表用 create_all、密码 bcrypt 哈希、JWT 无状态认证）
- **验证情况**：如何验证过（curl / /docs / pytest / 页面操作），结果如何
- **待办与遗留**：未完成项、已知问题、后续优化点

## 2. 项目级约定（跨模块通用）
- 分层调用：api → services → repositories（后端）；按模块建目录（前端）
- 其他影响全局的决策

## 3. 下一步计划（按优先级）
- [ ] 任务描述（归属模块）

## 4. 相关文档
- 问答记录：docs/QA.md
```

> 模板为最小结构，可按需增删小节，但**模块名、状态、已交付内容、待办与遗留**四项尽量保留，保证跨会话可读。

---
*此文件为 AI 编程助手提供项目上下文，请保持更新。*
