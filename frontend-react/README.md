# frontend-react

Web 生成平台的前端应用，基于 **React 19 + TypeScript + Vite 8**，包管理使用 npm。

## 项目结构

```
frontend-react/
├── src/                        # 源码（TS/TSX）
│   ├── main.tsx                # 应用入口：挂载全局 Provider + Router
│   ├── App.tsx                 # 根组件：只做路由表与布局装配
│   ├── api/                    # 接口调用层 —— 与后端 app/api 对应
│   │   ├── http.ts             # fetch 封装：baseURL、JSON、错误处理
│   │   └── items.ts            # 示例：一个后端路由模块 ↔ 一个 api 模块
│   ├── pages/                  # 路由级页面（每页一个目录）
│   │   ├── Home/
│   │   ├── Generate/
│   │   └── Projects/
│   ├── components/             # 跨页面复用组件
│   │   ├── layout/             # AppLayout（导航 + Outlet + 页脚）
│   │   └── common/             # （待填充：Button/Input/Modal…）
│   ├── hooks/                  # 通用自定义 hooks（useDebounce 等）
│   ├── types/                  # 数据模型 —— 与后端 models 对齐
│   ├── utils/                  # 通用工具函数（format 等）
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
| `api/` | 接口调用 | 一个后端路由模块对应一个文件；`http.ts` 统一 baseURL/错误 |
| `pages/` | 页面 | 路由级页面；每页一个目录，业务稍复杂时再提取 `hooks/`、`api/` |
| `components/` | UI 组件 | `layout/` 全局布局；`common/` 可复用基础组件 |
| `hooks/` | 逻辑复用 | 通用自定义 hooks（请求、防抖、鉴权等） |
| `types/` | 数据模型 | 与后端 Pydantic 模型对应（字段蛇形命名保持一致） |
| `utils/` | 工具函数 | 与业务无关的纯函数 |

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

## 代码规范

- 文件名/组件导出 `PascalCase`（`.tsx`），变量与函数 `camelCase`，常量 `UPPER_SNAKE_CASE`
- 类型导入使用 `import type`；与后端交互的数据结构放 `src/types/`
- 页面用函数组件 + Hooks；样式统一在 `src/styles/global.css` 维护设计变量
- 路径别名 `@/` 指向 `src/`
