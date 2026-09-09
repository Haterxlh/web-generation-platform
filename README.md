# Web Generation Platform

复刻"Web 生成平台"：帮助用户通过 AI 快速生成 Web 应用。

## 技术路线

前后端分离架构，**React 前端 + FastAPI 后端**：

| 模块 | 目录 | 技术栈 |
| --- | --- | --- |
| 前端 | `frontend-react/` | React 19, Vite 8, npm, ESLint |
| 后端 | `backend-uv-fastapi/` | Python >= 3.12, uv, FastAPI |

> 📌 早期规划曾使用 Spring Boot + LangChain4j 作为后端（`backend/` 目录，Java/Maven），该方案**已废弃**：`backend/` 已移出版本控制并在 `.gitignore` 中忽略，仅本地保留作为参考，不再维护。

## 目录结构

```
web-generation-platform/
├── frontend-react/              # 前端（React + Vite）
│   ├── src/                     # 源码（main.jsx 入口、App.jsx 根组件、样式）
│   ├── public/                  # 公共静态资源
│   ├── index.html
│   ├── package.json
│   └── vite.config.js
├── backend-uv-fastapi/          # 后端（FastAPI，uv 管理）
│   ├── pyproject.toml           # 依赖与入口配置
│   ├── uv.lock                  # 依赖锁文件
│   └── src/backend_uv_fastapi/
│       └── main.py              # FastAPI 应用入口（app）
├── sql/                         # 数据库脚本（历史 MySQL 脚本）
├── backend/                     # ⚠️ 已废弃（原 Spring Boot 后端，本地遗留，不入库）
├── AGENTS.md                    # AI 编程助手上下文说明
└── .gitignore                   # 统一的忽略规则
```

## 快速启动

### 1. 后端（FastAPI）

```bash
cd backend-uv-fastapi
uv sync               # 首次：同步并创建虚拟环境
uv run fastapi dev    # 启动开发服务器
```

- 默认地址：<http://127.0.0.1:8000>
- 接口文档（Swagger UI）：<http://127.0.0.1:8000/docs>

### 2. 前端（React）

```bash
cd frontend-react
npm install           # 首次：安装依赖
npm run dev           # 启动开发服务器
```

- 默认地址：<http://localhost:5173>

## 常用脚本

| 模块 | 命令 | 说明 |
| --- | --- | --- |
| 前端 | `npm run build` | 生产构建（产物 `dist/`） |
| 前端 | `npm run lint` | ESLint 代码检查 |
| 前端 | `npm run preview` | 本地预览构建产物 |
| 后端 | `uv run fastapi dev` | 开发模式启动（自动重载） |

## 提交规范

遵循约定式提交（Conventional Commits），如 `feat:` / `fix:` / `docs:` / `chore:`。完整说明见 `AGENTS.md`。
