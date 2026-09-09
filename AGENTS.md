# Agent Instructions for Web Generation Platform

## 1. 项目概述 (Project Overview)
本仓库用于**复刻"Web 生成平台"**：帮助用户通过 AI 快速生成 Web 应用。

技术路线（**当前与后续开发均以此为准**）：
- **frontend-react**：前端 —— React 19 + Vite 8（npm、ESLint）
- **backend-uv-fastapi**：后端 —— Python >= 3.12 + uv + FastAPI

> ⚠️ 遗留说明：仓库中曾规划 Spring Boot + LangChain4j 后端（`backend/` 目录，含 Java/Maven 代码），该方案**已废弃**。`backend/` 已从 git 移除跟踪并在根 `.gitignore` 中忽略，仅本地保留作为参考。**不要**在 `backend/` 上继续开发，也不要将其重新纳入提交。

## 2. 常用命令 (Common Commands)

### 前端（在 `frontend-react/` 下执行）
- 安装依赖：`npm install`
- 本地开发：`npm run dev`
- 生产构建：`npm run build`（产物输出到 `dist/`，不入库）
- 本地预览构建产物：`npm run preview`
- 代码检查：`npm run lint`

### 后端（在 `backend-uv-fastapi/` 下执行）
- 同步依赖：`uv sync`
- 本地启动：`uv run fastapi dev`（入口由 `pyproject.toml` 的 `[tool.fastapi]` 指定）
- 依赖变更后：修改 `pyproject.toml` 并重新执行 `uv sync` / `uv lock`，**提交 `uv.lock` 锁定版本**

## 3. 目录与代码定位

### 前端 `frontend-react/`
- `src/main.jsx`：应用入口；`src/App.jsx`：根组件；`src/index.css` / `src/App.css`：样式；`src/assets/`：静态图片
- `public/`：公共静态资源（favicon、icons 等）
- `vite.config.js`：构建配置；`eslint.config.js`：ESLint 配置

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
- 组件文件名与导出使用 `PascalCase`（如 `App.jsx`）；变量与函数使用 `camelCase`
- 组件采用函数组件 + Hooks 写法，逻辑写在 `.jsx` 中
- 样式使用普通 CSS（`index.css` / `App.css`），暂未引入 CSS 框架

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
1. **理解需求**：明确要修改或新增的功能（前端页面或 FastAPI 接口）。
2. **定位代码**：前端在 `frontend-react/src`；后端在 `backend-uv-fastapi/app`（入口 `app/main.py`，分层见 §3）。涉及 `backend/`（废弃 Spring Boot）的需求一律视为无效，不予处理。
3. **编写/修改代码**：遵循上述编码规范；改动依赖时同步 `uv.lock` / `package-lock.json`。
4. **本地验证**：提供用于测试的 curl 命令（FastAPI 默认 `http://127.0.0.1:8000`）或测试用例。
5. **提交代码**：按照规范的格式得到提交信息，待用户确认后再提交。

---
*此文件为 AI 编程助手提供项目上下文，请保持更新。*
