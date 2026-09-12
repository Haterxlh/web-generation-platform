# 项目进度 —— frontend-react

> 本文件用于跨会话同步开发进度。每次总结进度时按此格式更新。
> 最近更新时间：2026-09-12

## 1. 模块进度

### 模块：前端基础设施（请求层 / 类型 / 鉴权状态）
- **状态**：已完成
- **功能范围**：统一请求封装（含鉴权头与错误处理）、token 存储、全局登录状态
- **已交付内容**：
  - 核心文件：
    - `src/api/http.ts`（fetch 封装：自动附 Bearer token、提取 FastAPI 错误 detail、422 数组解析、401 回调）
    - `src/utils/token.ts`（localStorage 中 JWT 的唯一读写入口，键名 `wgp_access_token`）
    - `src/types/user_types.ts`（与后端 `user_schemas.py` 对齐的 4 个类型）
    - `src/api/user_api.ts`（注册 / 登录 / 当前用户三个接口函数）
    - `src/hooks/auth_context.ts`（AuthContext + AuthContextValue + useAuth）
    - `src/hooks/AuthProvider.tsx`（user / initializing 状态，login / register / logout）
    - `src/utils/navigation.ts`（intendedPath：从路由 state 取"原本想去的路径"）
    - `vite.config.ts`（修正代理：不再 rewrite 掉 `/api` 前缀）
  - 已删除的历史遗留：`src/api/items.ts`、`src/types/index.ts`（对应后端已移除的示例路由）
- **关键决策**：
  - 前端统一请求 `/api/...`；开发环境由 Vite 代理**原样转发**到 `127.0.0.1:8000`（后端 `include_router(prefix="/api")`）
  - 字段保持后端 snake_case，不做驼峰转换
  - token 存 localStorage（键名 `wgp_access_token`）；代价是 XSS 可读，生产可换 httpOnly Cookie
  - 401 处理用**回调注入**（`setUnauthorizedHandler`）而非 `http.ts` 反向 import AuthProvider，避免循环依赖
  - `AuthProvider` 挂在 `main.tsx`（Router 之外），因此自身不做跳转，跳转交给声明式守卫
  - 拆 `auth_context.ts`（`.ts`，含 context 与 hook）与 `AuthProvider.tsx`（`.tsx`，只导出组件），规避 `react-refresh/only-export-components`（error 级且只扫 `.tsx`）
  - `initializing` 状态用于"用本地 token 恢复登录态"期间避免误跳登录页
- **验证情况**：`npm run typecheck` / `npm run lint` 通过；Network 实测：无 token 不发 `/current`、真 token 返回 200 且带 `Authorization`、假 token 返回 401 且本地 token 被自动清除
- **待办与遗留**：可选升级为 `AbortController` 取消在途请求（需给 api 函数加 `signal?` 参数）

### 模块：用户模块（注册 / 登录 / 当前用户）
- **状态**：已完成
- **功能范围**：前端对接后端 `/api/user/*`，提供注册、登录、登出、登录态展示与路由守卫
- **已交付内容**：
  - 页面：
    - `src/pages/Register/RegisterPage.tsx`（路由 `/register`，独立页面无导航壳）
    - `src/pages/Login/LoginPage.tsx`（路由 `/login`，独立页面无导航壳）
  - 组件：
    - `src/components/common/RouteGuards.tsx`（`RequireAuth` / `GuestOnly`）
    - `src/components/layout/AppLayout.tsx`（新增头部用户区：登录/注册 ↔ 账号 + 退出）
  - 其他：
    - `src/App.tsx`（路由接线：登录注册为兄弟路由，`/projects` 用 `RequireAuth` 包裹）
    - `src/styles/global.css`（新增 `.auth-*`、`.app-user*` 样式，复用设计变量）
- **关键决策**：
  - 登录/注册为独立页面（`AppLayout` 的兄弟路由），因此没有顶部导航
  - 前端校验与后端 `Field` 约束一致（账号 2~32、密码 6~64）；**登录页只校验非空**，不按注册规则拦老账号
  - 注册即登录：`register` 成功后自动再调 `login`（注册流程共 2 个请求）
  - 守卫采用组件包装式：`<RequireAuth><ProjectsPage /></RequireAuth>`；`/` 与 `/generate` 保持公开
  - 两个守卫共用 `intendedPath(location.state)`，解决"GuestOnly 写死跳首页"与登录页跳转目标冲突的问题
  - 提交期间 `submitting=true` 禁用按钮，防重复提交；跳转用 `replace: true`，避免后退回到登录/注册页
  - 表单为受控组件（`value` + `onChange`）；提交事件类型用 `SubmitEvent<HTMLFormElement>`（`FormEvent` 在 @types/react 已标记 deprecated）
  - Hooks 必须写在组件顶层（Rules of Hooks）——曾因把 `useLocation()` 写进事件处理函数，导致点击登录无任何反应
- **验证情况**：
  - 未登录访问 `/projects` → 跳 `/login`；登录后 → 回到 `/projects`；直接打开 `/login` 登录 → 回首页
  - 已登录访问 `/login`、`/register` → 自动跳首页；登录后按"后退"不回登录页
  - 注册：空/短/不一致均有前端提示；重复账号显示后端 409 文案；新账号自动登录成功
  - 登录：错误密码显示 400 文案并停留原页
  - 登出：清除本地 token、头部切回"登录/注册"；在 `/projects` 登出被自动送回登录页
  - `npm run typecheck` / `npm run lint` / `npm run build` 通过
- **待办与遗留**：
  - 生成 / 项目模块尚无对应后端接口，页面仍为占位
  - 无自动化测试（可考虑 Vitest + Testing Library）
  - 可选增强：字段级错误提示、"记住我"、"忘记密码"、注册页也回跳原目标页

## 2. 项目级约定（跨模块通用）
- 分层调用：`pages → api → http.ts`；类型放 `types/*_types.ts`；全局状态与 hook 放 `hooks/`
- 组件文件用 PascalCase（`.tsx`）；非组件模块沿用下划线命名（`.ts`）
- 含组件与非组件导出的文件**必须拆分**（`react-refresh/only-export-components` 为 error 级）
- 与后端交互的字段一律保持 snake_case
- 样式统一使用 `global.css` 的设计变量；同类页面共用一套类名（如 `.auth-*`）
- Hooks 只写在组件 / 自定义 Hook 的顶层；提交前跑 `npm run lint` + `npm run typecheck`
- 生产环境 `/api` 由 Nginx/网关转发；`server.proxy` 仅开发服务器生效（`npm run preview` 不代理）

## 3. 下一步计划（按优先级）
- [ ] 后端"生成 / 项目"模块接口就绪后，按同一套模式对接前端（types → api → 页面 → 守卫）
- [ ] 首页与生成页的真实内容
- [ ] 可选：`AbortController` 取消请求；Vitest 单元测试；表单字段级校验
- [ ] 可选：清理 `src/hooks/useDebounce.ts`、`src/utils/format.ts` 等尚未使用的脚手架示例（确认不用再删）

## 4. 相关文档
- 问答记录：`docs/QA.md`（已积累 Q1–Q15，覆盖本轮前后端对接）
- 后端进度：`../backend-uv-fastapi/docs/proj_progress.md`
