# 项目进度 —— frontend-react

> 本文件用于跨会话同步开发进度。每次总结进度时按此格式更新。
> 最近更新时间：2026-09-15（Agent 框架阶段 8：会话式生成页 + 样式按组件拆分）

## 1. 模块进度

### 模块：会话式生成页（Agent 框架阶段 8）
- **状态**：已完成
- **功能范围**：把「填表单 → 生成 → 预览」升级为**会话式**：聊天澄清需求 → 挂附件 → 需求确认 → 生成 → 阶段进度 → 预览；
  并按阶段 7 的对照结论把默认模式切到 `agent`
- **已交付内容**：
  - 页面与组件（`src/pages/Generate/`，每个组件配同名 `*.module.css`）：
    - `GeneratePage.tsx`（容器：会话/消息/槽位/生成编排）
    - `ChatPanel.tsx`（消息列表 + Enter 发送 + 待发送附件 + 隐藏 file input）
    - `AttachmentChips.tsx`（`@docN` 别名 + 原文件名 + **三态**：成功 / 解析中 / 解析失败）
    - `RequirementCard.tsx`（槽位摊开，含"未提及"；缺槽位时列出还缺什么）
    - `GenerationProgress.tsx`（进度条 + 已等待计时 / 澄清暂停 / 失败 / 成功结果 + 预览）
    - `QuickGenerateForm.tsx`（原生 `<details>` 折叠的「极速生成（单页）」）
    - `generate_utils.ts`（**非组件**：校验、错误文案、槽位→prompt、会话回放映射、必备槽位口径）
  - 接口与类型：`src/api/agent_api.ts`、`src/types/agent_types.ts`
  - 共享逻辑：`src/hooks/useGenerationRunner.ts`（提交 + 轮询 + 计时 + clarifying 识别，两个入口共用）、
    `src/utils/agent_session.ts`（当前会话 uuid 的本地存取）
  - 组件复用：新增 `components/common/Button.tsx`、`AuthCard.tsx`、`AuthField.tsx`
    （登录 / 注册此前 90% 的标记与样式重复，现已收敛到共用组件）
- **关键决策**：
  - **默认 `gen_type='agent'`**，界面不再暴露 `multi`（阶段 7 结论：multi 连最简单需求都硬失败）；
    `single` 保留为折叠的「极速生成（单页）」——它的输入 token 只有 agent 的 1/76
  - **`clarifying` 与 `failed` 分开渲染**：前者是"停在原地等你补充"（`status` 仍是 running），
    用警示色 + "生成已暂停"的措辞，并**停止轮询**（否则会白转到 20 分钟上限）
  - **"已足够生成"的口径与后端一致**（`site_kind` + `features`）：刷新回放时后端不返回
    `ready_to_generate`，只能按同一口径重算 —— 两处漂移就会出现"按钮可点但后端说信息不足"
  - **`http.ts` 增加 multipart 分支**：走 `FormData` 时**不设 `Content-Type`**（boundary 得由浏览器生成，
    手写会让后端 422）；JSON 分支行为不变
  - **附件上传的前置条件如实反映在界面上**：别名作用域是会话，没有会话就禁用上传并提示"先发一条消息"
  - **消息正文写入 `@docN` 别名原文**，同时用 `attachments` 传 source_uuid —— 与后端"消息只存别名、
    不存文件内容"的设计一致，历史回放时也能看出这条消息引用了哪个文件
  - **附件解析失败不阻断对话**：chip 标红 + 一条 `notice` 消息说明原因（后端 `parse_status=failed` 是 200 而非 5xx）
  - **会话恢复不新增后端接口**：`session_uuid` 存本地 + `GET /agent/session/{uuid}` 回放；
    登出时与 token 一起清（否则换账号会去回放别人的会话）
  - **样式按组件拆分**：`global.css` 从 575 行收敛到只留设计变量 / 重置 / 页面通用排版，
    其余全部下沉到 `*.module.css`（详见 `frontend-react/README.md` 的「样式划分」）
- **验证情况**：
  - `npm run lint` / `npm run typecheck` / `npm run build`（62 modules，313.87 kB JS / 14.49 kB CSS）全绿
  - **Vite dev server 实际编译**：新模块逐个 HTTP 取回均 200（含 `*.module.css` 转成 JS 模块）
  - **HTTP 层端到端（按前端实际调用顺序与载荷，真实模型）**：
    注册登录 → 模糊需求 chat（`needs_clarification`）→ multipart 上传 `.md`（字段名 `session_uuid`/`file`
    → `@doc1`、`parse_status=success`）→ `source/list`（chip 数据）→ 带附件 chat
    （`ready=True`，且 **style 槽位来自文档实测**："极简风格，白色背景，元素统一 8px 圆角"）
    → 会话回放 4 条消息角色正确 → `create(gen_type=agent, session_uuid)` **202** →
    轮询 `routing 10% → retrieving 40% → planning 55% → generating 70% → done 100%` →
    `success` / 20552ms / 3 文件 → 签票 + 预览 **HTTP 200**
  - ⚠️ **浏览器内的实际观感与交互仍需人工点一遍**（见下方"待办与遗留"里的验证清单）
- **本轮修复（2026-09-15，用户实测发现）★提交 prompt 不能夹带闲聊**：
  - **现象**：用户对着需求确认卡片（槽位齐全、`ready`）点「开始生成」，任务却停在
    `clarifying`，理由是"用户实际在问『你能做什么』，属于能力咨询"。
  - **根因（前端 bug）**：`buildGenerationPrompt` 当时把**第一条用户消息**附在 prompt 末尾当"原始描述"，
    而用户的开场正是"你能做什么？"。**worker 的 ROUTING 节点只读 `task.prompt`（拿不到会话历史）**，
    于是把整段 prompt 读成能力咨询 → 判定信息不足 → 停在暂停态。
    用户明明已经人工确认过需求，却被告知"信息不够"。
  - **修复**：prompt **只由槽位拼成自洽的完整句子**（"请生成一个单页展示页面。核心功能：…。视觉风格：…。"），
    与确认卡片显示的内容同源；用户原话降级为"槽位拼不出东西时"的兜底，且取**最后一条**
    （`lastUserText`）—— 取第一条最容易命中开场白，正是这次踩的坑。
  - **验证（真实模型，完整复现用户场景）**：开场"你能做什么？" → `intent=chat`（正确）；
    再补四季主题需求 → `ready=True`；用修复后的口径拼 prompt →
    `routing 10% → planning 55% → generating 70% → done 100%` → **`success` / 66629ms / `index.html`**。
    修复前同一输入停在 `clarifying`。
  - **沉淀的契约**：前端提交的 `prompt` 必须**独立自洽**（worker 只认它），
    不能把"上下文"这类东西塞进去 —— 它会被 ROUTING 当作需求本身参与完备度判定。
  - **同日核查（用户追问"router 有没有 chat-agent 的上下文"）**：API 侧的 `intent_router` 与
    `chat_agent` 拿到的是**同一份 history（同一个对象）**；worker 侧的 ROUTING **只拿 prompt**。
    后端随后补齐了"会话草稿 → worker"的通道（见 `backend-uv-fastapi/docs/proj_progress.md`），
    但**三案例实测证明：接上草稿也不能单独挡住被污染的 prompt**（router 就是被设计来识别
    "用户在问能力"的）→ 所以**这条契约只能由前端承担**，前端这处修复是必需的，不是双保险里的可选项。
- **本轮修复 2（2026-09-15，用户实测发现）★切页后进度丢失**：
  - **现象**：生成过程中点「我的项目」再切回「生成应用」→ 进度条消失、"开始生成"重新可点
    （真实风险：会重复提交一个任务，白烧一份 token）。
  - **根因**：轮询状态此前只活在 `GeneratePage` 的组件状态里 —— 路由切换会卸载组件，状态随之蒸发；
    而生成其实还在后端 worker 里正常跑。
  - **修复**：新增 `src/utils/generation_task.ts`（localStorage 存"进行中的任务"：task_uuid /
    轮询间隔 / 提交时刻），`useGenerationRunner` 在提交成功后立刻落本地、到终态时清除；
    重新挂载时读取它**恢复轮询**，并按 `started_at` 续算"已等待 N 秒"（不归零）。
    若任务在离开期间已结束，恢复的第一次查询就会直接把结果卡片渲染出来。
  - **顺带修掉一个潜在竞态**：`reset()`（开始新会话）原来只清 state，挂在 `await` 上的旧循环
    醒来后还会往已清空的界面上写状态 —— 改为 `epochRef` 计数，旧循环凭 epoch 判定自己过期。
    理由：布尔标志在同一轮同步里"置 true 再置 false"，被 `await` 挂起的循环根本观察不到。
  - **存的是"我提交过哪个任务"，不是任务状态**：真源始终在 MySQL，恢复时回后端查 ——
    与"Redis 只做队列、不是真源"是同一条原则。
  - **验证**：`lint` / `typecheck` / `build`（64 modules）全绿；
    ⚠️ 浏览器里的"切页 → 回来看到进度继续 / 看到已完成结果"仍需人工点一遍。
- **待办与遗留**：
  - **人工验证清单（我无法驱动浏览器，需你点一遍）**：
    1. `/generate` 首屏：空会话提示 + 发送按钮在输入为空时禁用 + 附件按钮禁用且提示"先发消息"
    2. 发一条模糊需求 → 助手追问 → 补齐后出现需求确认卡片与「开始生成」
    3. 上传一个 `.md` → 出现 `@doc1` chip；再上传一个**扫描版 PDF** → chip 标红 + notice 说明原因
    4. 点「开始生成」→ 进度条走动、已等待秒数递增；生成完出现结果卡片 → 「打开预览」新标签页打开
    5. **刷新页面** → 历史消息回放出来、需求确认卡片仍是就绪态（localStorage 的 session_uuid 生效）
    6. 点「开始新会话」→ 清空；折叠的「极速生成（单页）」可用且界面里**没有 multi 选项**
  - **附件历史 chip 不还原**：`GET /agent/session/{uuid}` 不返回消息级 attachments，
    刷新后历史消息里的 `@docN` 只剩正文文字（设计如此：消息只存别名），chip 不重现
  - **会话列表缺失**：只能恢复"最近一个"会话；要多会话需要后端加 `GET /api/agent/sessions`
  - **无自动化测试**：前端仍无 Vitest；本阶段的逻辑（槽位→prompt、必备槽位口径、校验）都是纯函数，适合补单测
  - **轮询仍是 1.5s 间隔**：SSE 未做（后端也没有），长任务里 `routing`/`retrieving` 这类短阶段可能看不到
  - **已知偏差**：`generate_utils.ts` 里的 `REQUIRED_SLOTS` 与后端 `agents/state.py` 是**两处硬编码**，
    后端若调整必备槽位，这里必须同步（跨语言暂无法共享契约，已在 `generate_utils.ts` 注释里标注）

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
  - 守卫采用组件包装式：`<RequireAuth><ProjectsPage /></RequireAuth>`；`/` 保持公开；`/generate` 与 `/projects` 需要登录（2026-09-14 调整：生成模块接口全部要求 Bearer token）
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
  - 生成 / 项目模块**已对接**，见下方「生成模块（前端对接）」条目
  - 无自动化测试（可考虑 Vitest + Testing Library）
  - 可选增强：字段级错误提示、"记住我"、"忘记密码"、注册页也回跳原目标页

### 模块：生成模块（前端对接）
- **状态**：已完成（**表单式 GeneratePage 已于 2026-09-15 阶段 8 重写为会话式页面**，见上方「会话式生成页」；
  本条的接口层 / 类型层 / 轮询骨架 / 项目列表仍然有效，GeneratePage 的旧实现描述保留作历史参考）
- **功能范围**：对接后端 `/api/generation/*`，提供「填需求 → 生成 → 预览」与「历史记录 → 重新预览」
- **已交付内容**：
  - 页面：
    - `src/pages/Generate/GeneratePage.tsx`（需求表单 + 生成状态机 + 结果卡片 + 打开预览）
    - `src/pages/Projects/ProjectsPage.tsx`（历史列表 + 分页 + 重新预览）
  - 接口层：`src/api/generation_api.ts`（create / list / detail / preview-ticket）
  - 类型层：`src/types/generation_types.ts`（`GenType` / `GenStatus` 字面量联合 + 4 个接口）
  - 共享逻辑：`src/hooks/useOpenPreview.ts`（签票 + 同步占位窗口 + 错误提示）
  - 路由：`src/App.tsx` 把 `/generate` 纳入 `RequireAuth`
  - 样式：`src/styles/global.css` 新增 `.gen-*` / `.proj-*`
- **关键决策**：
  - 字段一律 snake_case，与后端 schema 逐字段对齐；`GenType` / `GenStatus` 用字面量联合（写错值在 `tsc` 阶段就报错）
  - **打开预览必须"同步占位窗口"**：`await` 之后再 `window.open` 会被弹窗拦截器拦下；且不能用 `noopener`（它会让 `window.open` 返回 `null`，拿不到句柄）
  - **预览只能 `window.open(preview_url)`**：产物 css/js 子资源由浏览器自动携带 `wgp_preview` Cookie；前端不碰 token，也不能用 fetch 读产物
  - 列表只存 `task_uuid`，不缓存 `preview_url`（票据 30 分钟过期，每次点击重新签票）
  - 只有 `status === 'success'` 才允许点预览（`running` / `failed` 无产物，点下去必然被后端 400 拒绝）
  - `Record<GenStatus, string>` 保证后端新增状态时编译期报错
  - `useCallback` 稳定 `load` 引用，避免 `useEffect` 依赖变化导致无限请求
  - `/generate` 纳入 `RequireAuth`（生成模块接口全部要求 token；未登录点进来只会拿到 403）
- **验证情况**：
  - `npm run typecheck` / `npm run lint` / `npm run build` 全绿
  - 未登录访问 `/generate` → 跳登录页，登录后自动回到 `/generate`（`intendedPath` 生效）
  - 生成中按钮禁用、Network 仅 1 条 create；Offline 时提示中文文案且按钮恢复；成功后展示结果卡片
  - 预览：新标签页打开产物；`localhost:5173` 下 `wgp_preview` 的 Path 为 `/preview/{user_id}/{task_uuid}/`；手动删 Cookie 后刷新 403、重新点击即恢复
  - 历史列表：状态标签、失败原因展示、僵尸 `running` 行按钮置灰、分页（临时把 `PAGE_SIZE` 改为 2 验证，验完改回 10）
- **待办与遗留**：
  - ~~生成中只有"请稍候"文案，无实时进度~~ → **已解决**：阶段 0 起有阶段进度条 + 已等待计时（轮询版）
  - 无自动化测试（可选 Vitest + Testing Library）
  - 历史列表暂无删除 / 重新生成（后端无对应接口）
  - `immer` / `use-immer` 已引入但**暂未使用**（预留给"改数组中的某一项"这类场景）

## 2. 项目级约定（跨模块通用）
- 分层调用：`pages → api → http.ts`；类型放 `types/*_types.ts`；全局状态与 hook 放 `hooks/`
- 组件文件用 PascalCase（`.tsx`）；非组件模块沿用下划线命名（`.ts`）
- 含组件与非组件导出的文件**必须拆分**（`react-refresh/only-export-components` 为 error 级）
- 与后端交互的字段一律保持 snake_case
- **样式（2026-09-15 起）**：`global.css` 只放设计变量 / 元素重置 / 页面通用排版（`.page`、`.page-desc`）；
  组件与页面样式放**同目录 `*.module.css`**（CSS Modules，类名自动加哈希，不再靠 `.auth-`/`.gen-` 前缀防冲突）。
  需要叠加两个类时用模板串（`styles.navLink + ' ' + styles.navLinkActive`）
- **effect 里不许同步 setState**（`react-hooks/set-state-in-effect` 为 error 级）：
  能推导的初值用 `useState(() => ...)` 惰性初始化（如"本地有无 token / 会话"），异步结果放 Promise 回调
- Hooks 只写在组件 / 自定义 Hook 的顶层；提交前跑 `npm run lint` + `npm run typecheck`
- 生产环境 `/api` 由 Nginx/网关转发；`server.proxy` 仅开发服务器生效（`npm run preview` 不代理）

## 3. 下一步计划（按优先级）
- [x] **Agent 框架阶段 8（前端对接）**（已完成 2026-09-15）：会话式 Generate 页 + 附件 chip +
      需求确认卡片 + 阶段进度 + 折叠的极速单页入口；`agent_api.ts` / `agent_types.ts` / `useGenerationRunner`；
      默认切 `agent`、不再暴露 `multi`、区分 `clarifying` 与失败；
      并按要求把 `global.css` 按组件拆成 CSS Modules
- [ ] **人工点一遍验证清单**（见「会话式生成页」模块的待办与遗留）：我只能验证到 HTTP 层与构建，
      浏览器里的交互与观感需要你确认
- [ ] 首页真实内容（当前为占位 / 简介）
- [ ] 可选：Vitest 单元测试（`generate_utils.ts` 的纯函数最适合先补）；`AbortController` 取消在途请求
- [ ] 可选：会话列表（需要后端加 `GET /api/agent/sessions`，当前只能恢复最近一个会话）
- [ ] 可选：清理 `src/hooks/useDebounce.ts`、`src/utils/format.ts` 等尚未使用的脚手架示例

## 4. 相关文档
- 问答记录：`docs/QA.md`（已积累 Q1–Q19，覆盖用户模块与生成模块对接）
- 前端结构与约定：`README.md`（含「样式划分」与「会话式生成」两节）
- 后端进度：`../backend-uv-fastapi/docs/proj_progress.md`
- Agent 框架总方案与阶段验收：`../backend-uv-fastapi/docs/agent_refactor_plan.md`
- 阶段 7 对照实验数据：`../backend-uv-fastapi/docs/experiments/stage7_compare_*.json`
