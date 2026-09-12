# frontend-react

Web 生成平台的前端应用，基于 **React 19 + TypeScript + Vite 8**，包管理使用 npm。

## 项目结构

```
frontend-react/
├── src/                        # 源码（TS/TSX）
│   ├── main.tsx                # 应用入口：挂载全局 Provider + Router
│   ├── App.tsx                 # 根组件：只做路由表与布局装配
│   ├── api/                    # 接口调用层 —— 与后端 app/api 对应
│   │   ├── http.ts             # fetch 封装：baseURL、Bearer token、错误处理、401 回调
│   │   └── user_api.ts         # 用户模块接口：注册 / 登录 / 当前用户
│   ├── pages/                  # 路由级页面（每页一个目录）
│   │   ├── Home/
│   │   ├── Generate/
│   │   ├── Projects/           # 需要登录（由 RequireAuth 保护）
│   │   ├── Login/              # 独立页面：无导航壳
│   │   └── Register/           # 独立页面：无导航壳
│   ├── components/             # 跨页面复用组件
│   │   ├── layout/             # AppLayout（导航 + 用户区 + Outlet + 页脚）
│   │   └── common/             # RouteGuards（RequireAuth / GuestOnly）
│   ├── hooks/                  # 通用 hooks 与全局上下文
│   │   ├── useDebounce.ts
│   │   ├── auth_context.ts     # AuthContext + useAuth
│   │   └── AuthProvider.tsx    # 登录状态提供者（挂在 main.tsx）
│   ├── types/                  # 数据模型 —— 与后端 models 对齐
│   │   └── user_types.ts
│   ├── utils/                  # 通用工具函数
│   │   ├── format.ts
│   │   ├── token.ts            # localStorage 中 JWT 的读写
│   │   └── navigation.ts       # intendedPath：从路由 state 取目标路径
│   ├── styles/                 # 全局样式 global.css（设计变量）
│   └── vite-env.d.ts
├── public/                     # 公共静态资源（favicon 等）
├── index.html
├── package.json                # 依赖与脚本
├── tsconfig*.json              # TS 工程配置
├── vite.config.ts              # 别名 @、/api 代理
├── eslint.config.js
└── README.md
```

## 分层约定

| 层 | 职责 | 说明 |
| --- | --- | --- |
| `api/` | 接口调用 | 一个后端路由模块对应一个文件；`http.ts` 统一 baseURL、Bearer token 与错误处理 |
| `pages/` | 页面 | 路由级页面；每页一个目录，业务稍复杂时再提取 `hooks/`、`api/` |
| `components/` | UI 组件 | `layout/` 全局布局；`common/` 可复用组件（含 `RouteGuards` 路由守卫） |
| `hooks/` | 逻辑复用 | 通用自定义 hooks 与全局上下文（防抖、鉴权等） |
| `types/` | 数据模型 | 与后端 Pydantic 模型对应（字段蛇形命名保持一致） |
| `utils/` | 工具函数 | 与业务无关的纯函数（token 读写、路由 state 解析等） |

## 快速启动

```bash
npm install     # 安装依赖
npm run dev     # 开发服务器（默认 http://localhost:5173）
```

开发环境下，代码中的 `/api/**` 请求会由 Vite 代理转发到本地 FastAPI 后端
（`http://127.0.0.1:8000`，规则见 `vite.config.ts`），无需处理跨域。

## 常用脚本

| 命令 | 说明 |
| --- | --- |
| `npm run dev` | 开发模式（HMR） |
| `npm run build` | 类型检查（tsc）+ 生产构建，产物 `dist/` |
| `npm run typecheck` | 仅执行 TypeScript 类型检查 |
| `npm run lint` | ESLint 代码检查 |
| `npm run preview` | 本地预览构建产物 |

## 鉴权与路由守卫

- **登录态**：`AuthProvider`（`src/hooks/AuthProvider.tsx`）挂在 `main.tsx` 最外层，持有 `user` / `initializing` 状态并提供 `login` / `register` / `logout`；页面通过 `useAuth()`（`src/hooks/auth_context.ts`）读取。
- **token**：登录成功后写入 `localStorage`（键名 `wgp_access_token`，读写统一走 `src/utils/token.ts`）。
- **请求鉴权**：`src/api/http.ts` 自动附加 `Authorization: Bearer <token>`；遇到 401 时通过 `setUnauthorizedHandler` 通知 `AuthProvider` 清空登录态。
- **路由守卫**（`src/components/common/RouteGuards.tsx`）：
  - `RequireAuth`：未登录访问受保护页 → 跳 `/login`，并把原路径放入 `location.state.from`；
  - `GuestOnly`：已登录访问 `/login`、`/register` → 跳 `intendedPath(location.state)`（默认首页）；
  - 两者共用 `intendedPath()`（`src/utils/navigation.ts`），保证"登录后回到原目标页"的行为一致。
- **页面归属**：`/`、`/generate` 公开；`/projects` 需要登录；`/login`、`/register` 为独立页面（不套 `AppLayout`，无顶部导航）。

## 代码规范

- 文件名/组件导出 `PascalCase`（`.tsx`），变量与函数 `camelCase`，常量 `UPPER_SNAKE_CASE`
- 类型导入使用 `import type`；与后端交互的数据结构放 `src/types/`
- 页面用函数组件 + Hooks；样式统一在 `src/styles/global.css` 维护设计变量
- 路径别名 `@/` 指向 `src/`
- 同一文件不要同时导出组件与非组件（`react-refresh/only-export-components` 为 error 级）：context / hook 放 `.ts`，组件放 `.tsx`
- Hooks 只能写在组件或自定义 Hook 的顶层（Rules of Hooks）；提交前跑 `npm run lint` + `npm run typecheck`
