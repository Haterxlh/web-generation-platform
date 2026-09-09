# Agent Instructions for Web Generation Platform

## 1. 项目概述 (Project Overview)
这是一个帮助用户快速生成 Web 应用的全栈平台，仓库包含三个子项目：
- **backend**：Spring Boot 后端，负责业务逻辑与数据（Java 21, Spring Boot 4.0.8, Spring Data JPA, MySQL 8.0, Maven）。
- **backend-uv-fastapi**：基于 uv 管理的 Python FastAPI 后端。
- **frontend-react**：基于 Vite + React 的前端应用（React 19, Vite, ESLint）。

## 2. backend：环境与设置 (Setup & Environment)
- **JDK 版本**：Java 21
- **构建工具**：Maven
- **数据库**：MySQL 8.0，数据库名 `wgp_db`
- **本地配置文件**：`application-local.yml` 用于本地开发，**不得提交到 Git**。
- **激活本地配置**：在 `application.yml` 中设置 `spring.profiles.active: local`。

## 3. backend：常用命令 (Common Commands)
- **清理并编译**：`mvn clean compile`
- **打包应用**：`mvn clean package`
- **运行测试**：`mvn test`
- **本地启动**：直接运行 `WebGenerationPlatformApplication.java` 中的 `main` 方法。

## 4. backend：编码规范与风格 (Code Style & Conventions)
- **包结构**：遵循标准的 Spring Boot 分层架构。
    - `controller`：处理 HTTP 请求
    - `service`：业务逻辑
    - `repository`：数据访问层（JPA）
    - `entity`：数据库实体类
    - `dto`：数据传输对象
- **命名规范**：
    - 类名使用 `UpperCamelCase`（如 `UserController`）
    - 方法名和变量名使用 `lowerCamelCase`（如 `findUserById`）
    - 常量使用 `UPPER_SNAKE_CASE`（如 `MAX_RETRY_COUNT`）
- **API 设计**：尽量遵循 RESTful 风格。

## 5. backend：数据库与 JPA 规范 (Database & JPA Rules)
- **实体类**：使用 `@Entity` 和 `@Id` 注解，并正确配置映射关系（如 `@OneToMany`）。
- **数据源**：只能连接配置文件中指定的本地数据库 `wgp_db`。
- **禁止操作**：绝对不要修改、删除或查询 `mysql`、`sys`、`performance_schema` 等系统数据库。

## 6. backend：重要安全规则 (Critical Security Rules)
- **敏感信息**：`application-local.yml` 中的数据库密码等敏感信息**严禁硬编码**，必须使用环境变量 `${MYSQL_PASSWORD}` 或确保该文件被 `.gitignore` 忽略。
- **SQL 注入**：使用 JPA 或 `PreparedStatement`，禁止拼接 SQL 字符串。

## 7. frontend-react：前端项目
- **技术栈**：React 19 + Vite 8，ESM 模块，npm 包管理，ESLint 校验。
- **目录结构**：
    - `src/`：源代码（`main.jsx` 入口、`App.jsx` 根组件、`index.css` / `App.css` 样式、`assets/` 静态图片）
    - `public/`：公共静态资源（favicon、icons 等）
    - `vite.config.js`：Vite 构建配置；`eslint.config.js`：ESLint 配置
- **常用命令**（在 `frontend-react/` 下执行）：
    - 安装依赖：`npm install`
    - 本地开发：`npm run dev`
    - 生产构建：`npm run build`（产物输出到 `dist/`，不入库）
    - 本地预览构建产物：`npm run preview`
    - 代码检查：`npm run lint`
- **编码规范**：
    - 组件文件名与导出使用 `PascalCase`（如 `App.jsx`）；变量与函数使用 `camelCase`。
    - 组件采用函数组件 + Hooks 写法，逻辑写在 `.jsx` 中。
    - 样式使用普通 CSS（`index.css` / `App.css`），暂未引入 CSS 框架。
- **注意**：`node_modules/`、`dist/`、`dist-ssr/`、`.vite/`、`.env*.local`、调试日志等运行产物一律不入库，规则统一维护在根目录 `.gitignore`（前端不再单独维护 .gitignore）。

## 8. backend-uv-fastapi：Python 后端
- **技术栈**：uv 管理依赖，Python >= 3.12，FastAPI。
- **目录结构**：应用入口在 `src/backend_uv_fastapi/main.py`（`app = FastAPI()`）。
- **常用命令**（在 `backend-uv-fastapi/` 下执行）：
    - 同步依赖：`uv sync`
    - 本地启动：`uv run fastapi dev`（入口由 `pyproject.toml` 的 `[tool.fastapi]` 指定）
- **注意**：`.venv/`、`__pycache__/` 等运行产物不入库（根 `.gitignore` 已忽略）。

## 9. Git 与提交规范 (Git & Commit Rules)
- **主分支**：`main`
- **提交信息格式**：遵循约定式提交（Conventional Commits）。
    - `feat:` 新功能
    - `fix:` 修复 Bug
    - `docs:` 文档更新
    - `chore:` 构建/工具变动
    - 示例：`feat(user): 添加用户注册接口`
- **提交前**：确认运行产物（`node_modules/`、`dist/`、`target/`、`.venv/`、`__pycache__/`、本地配置文件等）已被 `.gitignore` 忽略，避免误提交。

## 10. AI 助手工作流 (Agent Workflow)
1. **理解需求**：明确要修改或新增的功能（后端接口、前端页面或 Python 服务）。
2. **定位代码**：Spring Boot 后端在 `backend` 找 `controller`、`service`、`repository`、`entity`、`dto`；前端在 `frontend-react/src`；Python 后端在 `backend-uv-fastapi/src`。
3. **编写/修改代码**：遵循对应模块的编码规范。
4. **本地验证**：提供用于测试的 curl 命令或测试用例。
5. **提交代码**：按照规范的格式得到提交信息，待用户确认后再提交。

---
*此文件为 AI 编程助手提供项目上下文，请保持更新。*
