# QA.md —— 开发问答记录

> 本文档汇总 frontend-react（React 19 + TypeScript + Vite 8）开发过程中的疑问与解答，
> 供新手复习使用。格式：每个问题带编号与 tag（主题标签），随后是对应简答。
> 本文件覆盖"用户模块前后端对接"这一轮开发（2026-09-12），后续问答在末尾追加，编号接续。

---

### Q1: `useEffect` 是干什么的？React 里的"副作用"指什么？
**tag:** `React` | `Hooks` | `useEffect` | `入门`

**A1:**

组件本质是个函数，state 一变 React 就**重新执行整个函数体**（重新渲染）。所以函数体必须是"纯"的：
同样的输入给同样的输出，不能顺手发请求、改 `localStorage`、开定时器 —— 否则重渲染一次就多发一次请求。

这些"渲染之外要做的事"就是**副作用（side effect）**，用 `useEffect` 登记，在界面渲染完成之后执行。

```tsx
useEffect(要执行的函数, [依赖数组])
```

| 写法 | 执行时机 | 典型用途 |
|---|---|---|
| `useEffect(fn)` | 每次渲染后都执行 | 极少用 |
| `useEffect(fn, [])` | 只在组件首次出现后执行一次（+ 卸载时清理） | 打开页面拉一次数据、恢复登录态 |
| `useEffect(fn, [a])` | 首次 + `a` 变化后执行 | 搜索词变了重新请求 |

对比 `useMemo`：**`useEffect` 是"做事"（返回清理函数），`useMemo` 是"算值"（返回算出来的值）**，两者都有依赖数组，但用途完全不同。

> 开发模式下 `StrictMode` 会故意"挂载 → 卸载 → 再挂载"，effect 因此执行两次（Network 里 `GET /current` 出现两次是正常的），生产环境只执行一次。
>
> 补充（2026-09-14）：即使用 `useCallback` 稳定了依赖，`GET /api/generation/list` 在开发环境仍会出现两次请求，原因同上。**不要为了消掉它去关 StrictMode** —— GET 是幂等的，而 StrictMode 正是靠这种"双调用"帮你发现副作用没清理干净的问题。

---

### Q2: `useEffect` 里 `return () => { cancelled = true }` 有什么意义？`let cancelled = false` 不就是个局部变量吗？
**tag:** `React` | `useEffect` | `闭包` | `清理函数`

**A2:**

是局部变量没错，但关键在于两点：

1. **每次 effect 执行都会新建一份 `cancelled`**（互不干扰）；
2. **它被闭包记住了** —— 清理函数和 `.then` 回调处于同一作用域，改的是同一份：

```tsx
useEffect(() => {
  let cancelled = false                                 // ← 这一份
  getCurrentUser().then(() => { if (!cancelled) ... })   // ← 和这里共用
  return () => { cancelled = true }                      // ← 和这里共用
}, [])
```

可以在浏览器 Console 亲眼验证闭包共享：

```js
function run() {
  let cancelled = false
  setTimeout(() => console.log('请求回来了，cancelled =', cancelled), 1000)
  return () => { cancelled = true }
}
const cleanup = run()   // 相当于"挂载"
cleanup()               // 相当于立刻"卸载"
// 1 秒后输出：cancelled = true（把 cleanup() 注释掉则输出 false）
```

**清理函数什么时候真的执行？**

| 场景 | 会执行吗 |
|---|---|
| 整页刷新 / 关标签页 | ❌ 不会（整个 JS 环境销毁，也不需要） |
| 开发模式 `StrictMode` 的卸载演练 | ✅ 会 |
| 组件被从界面移除（路由切走等） | ✅ 会 |
| 依赖数组里的值变化 | ✅ 会（下一次 effect 之前） |

**没有它会怎样**：① 向已卸载组件写 state（只是浪费）；② 更严重的是**竞态** —— 旧请求比新请求晚返回，把新数据覆盖成过期数据（如"点项目 A 慢、点项目 B 快，最后界面显示 A"）。

> 更彻底的做法是 `AbortController` 真正取消请求（`http.ts` 已支持 `signal` 参数，需给 api 函数加 `signal?: AbortSignal`），属可选优化。

---

### Q3: `useMemo` 是什么？`useMemo<AuthContextValue>` 里的 `<AuthContextValue>` 又是什么？
**tag:** `React` | `useMemo` | `useCallback` | `TypeScript` | `泛型`

**A3:**

**`<AuthContextValue>` 是 TypeScript 的泛型参数**，与 React 无关。`useMemo` 的类型签名是：

```ts
function useMemo<T>(factory: () => T, deps: DependencyList): T
```

所以 `useMemo<AuthContextValue>(() => ({...}), [...])` 的意思是"这个函数返回的东西类型是 `AuthContextValue`"。等价写法 `useMemo((): AuthContextValue => ({...}), [...])`。不写也能靠 TS 推断通过，显式写的价值是：字段写错时**报错就在那一行**，且补全更准。

**`useMemo` 是"缓存计算结果"**：依赖没变就复用上次的结果，不重算。这里必须用的原因：

```js
{ a: 1 } === { a: 1 }   // false —— JS 比较的是"是不是同一个对象"
```

Context 的 `value` 若每次渲染都新建对象，所有 `useAuth()` 消费者都会**白重渲染一遍**。

**必须与 `useCallback` 配套**：`login` / `register` / `logout` 若每次渲染都是新函数，`useMemo` 的依赖一直在变，等于没用。

> 诚实结论：这里是**性能优化**，不是功能必需；别在 `useMemo` 里写副作用（React 允许丢弃缓存重算）；将来启用 React Compiler 后这类手写 memo 可自动完成。

---

### Q4: `isAuthenticated` 为什么不用 `useState`？
**tag:** `React` | `状态设计` | `派生值`

**A4:**

因为它**能由 `user` 算出来**（`user !== null`），属于**派生值**。官方原则：能算出来的，就不要存。

若用 state，就多出一个必须手动同步的数据源，每处改 `user` 的地方都要记得同时改它：

| 操作 | 两份状态（易漏） | 正确做法 |
|---|---|---|
| 登录成功 | `setUser(...)` + `setIsAuthenticated(true)` | 只 `setUser(...)` |
| 登出 / 401 / 刷新恢复 | 两个都要改 | 只改 `user` 相关的那个 |

漏一处就会出现自相矛盾状态（`user` 有值而 `isAuthenticated` 为 `false`），界面行为诡异且难查。

**判断标准**：
- **要 state**：用户输入、从后端拿的数据、无法推导的 UI 状态（如 `initializing` —— "请求是否还在路上"这个信息推导不出来）；
- **不要 state**：能由其他 state/props 算出的（`isAuthenticated`、`fullName`、`filteredList`）。

派生值如果计算昂贵（大列表过滤），用 `useMemo` 缓存，**而不是用 `useState` 存**。

---

### Q5: 为什么 `AuthContext`/`useAuth` 放 `auth_context.ts`，组件放 `AuthProvider.tsx`？
**tag:** `ESLint` | `react-refresh` | `工程约定`

**A5:**

因为项目启用了 `react-refresh/only-export-components`（来自 `reactRefresh.configs.vite`，**级别是 error**，且**只扫描 `.jsx` / `.tsx`**）。

规则内容：一个 `.tsx` 文件如果**既导出组件、又导出非组件**（hook、`createContext` 返回的 context 对象等），Fast Refresh 会失效 → 直接报错。

所以拆法固定为：

| 文件 | 扩展名 | 允许导出 |
|---|---|---|
| `auth_context.ts` | `.ts`（不被规则扫描） | `AuthContext`、`AuthContextValue`、`useAuth` |
| `AuthProvider.tsx` | `.tsx` | **只**导出 `AuthProvider` 组件 |

推论：想给 `.tsx` 加工具函数导出，应放到 `utils/*.ts` 里（本次的 `intendedPath` 就是这么处理的）。

---

### Q6: `AuthProvider` 为什么挂在 `main.tsx` 最外层？为什么它里面不能做跳转？
**tag:** `React` | `Context` | `路由分层` | `架构`

**A6:**

- `main.tsx` 的职责就是"挂载全局 Provider + Router"（README 约定），Provider 包在 Router 外面，因此**所有页面都能 `useAuth()`**；
- 但 Router 之外的组件**拿不到 `useNavigate()`**，所以 Provider 只做状态，跳转交给**声明式守卫**（`RequireAuth` / `GuestOnly` 渲染 `<Navigate />` 完成跳转），依赖方向保持单向：`页面 → useAuth`，不反向耦合路由；
- 同理，`http.ts` 遇到 401 时不 import `AuthProvider`，而是通过 `setUnauthorizedHandler` **回调注入**通知它清登录态 —— 目的都是**避免循环依赖**（否则出现 `AuthProvider → api → http → AuthProvider`）。

---

### Q7: 前端请求 `/api/...` 却返回 404，为什么？
**tag:** `Vite` | `开发代理` | `联调`

**A7:**

旧 `vite.config.ts` 的 proxy 里有一行

```ts
rewrite: (path) => path.replace(/^\/api/, ''),
```

它把 `/api` 前缀剥掉了：浏览器请求 `/api/user/login` → 后端实际收到 `/user/login` → 404。

而后端 `main.py` 是 `app.include_router(user_router, prefix="/api")`，**真实路径必须带 `/api`**。修法就是删掉 `rewrite`，让代理原样转发。

**对照实验（很直观）**：

```powershell
curl.exe -i http://127.0.0.1:5173/api/user/current
# 改前：404（前缀被剥）
# 改后：401（走到了鉴权，说明路由匹配成功）
```

> 另外：`server.proxy` **只在 `npm run dev` 生效**。`npm run preview` 预览构建产物时没有代理，需要配 `preview.proxy` 或生产用 Nginx 转发 `/api`。

---

### Q8: 接口前缀到底写 `/api/user` 还是 `/api/users`？
**tag:** `联调` | `接口契约` | `排错方法`

**A8:**

**以后端"实际路由"为准，不要只信文档或注释。** 两种快速核实方式：

```powershell
# ① 看运行中的后端真实路由表
curl.exe http://127.0.0.1:8000/openapi.json     # paths 里就是真实路径
# ② 对比实验
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/api/user/current    # 401 → 路由存在
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/api/users/current   # 404 → 不存在
```

本项目最终约定 **单数 `/api/user/*`**（后端 `APIRouter(prefix="/user")` + `include_router(prefix="/api")`）。

**教训**：路由前缀一旦改动，要同步三处 —— 后端 `main.py` 注释、后端 `docs/proj_progress.md`、前端 `api/*_api.ts` 与 README。

---

### Q9: PowerShell 里 `curl -d "{\"a\":\"b\"}"` 报 `JSON decode error`，为什么？
**tag:** `联调` | `PowerShell` | `curl` | `工具`

**A9:**

**PowerShell 不用反斜杠转义** —— `\"` 里的反斜杠会被原样保留，传给 `curl.exe` 的其实是 `{\"a\":\"b\"}`，不是合法 JSON。于是 FastAPI 在参数校验**之前**就抛 `json_invalid`。

三种正确写法：

```powershell
# ① 外层单引号 + 反斜杠（curl.exe 经典写法）
curl.exe -s -X POST http://127.0.0.1:8000/api/user/register -H "Content-Type: application/json" -d '{\"user_account\":\"a\",\"user_password\":\"123\"}'

# ② PowerShell 原生（推荐，零引号地狱）
$body = @{ user_account = 'a'; user_password = '123' } | ConvertTo-Json
try { Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/user/register' -Method Post -ContentType 'application/json' -Body $body }
catch { $_.ErrorDetails.Message }     # 非 2xx 会抛异常，用 catch 看响应体（PS7 可用 -SkipHttpErrorCheck）
```

```text
# ③ 最省事：浏览器打开 http://127.0.0.1:8000/docs → Try it out
```

补充两点：PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名，要写 **`curl.exe`**；另外这个 422 报错本身**证明请求已到达后端**（404 才说明路径不对）。

> **补充（2026-09-14）**：同一环境下还有一个更隐蔽的坑 —— **中文会静默变成问号**。
> 判定方法：
> ```sql
> SELECT CHAR_LENGTH(col) AS char_len, LENGTH(col) AS byte_len, HEX(LEFT(col, 4)) AS head_hex FROM t;
> ```
> 若 `head_hex = 3F3F3F3F` 且 `char_len == byte_len` → 说明**请求发出前**中文就已被替换成 `?`（有损、不可逆），后端与数据库无责。
> 规避：把 JSON 先写成 UTF-8 文件，再用 `curl.exe --data-binary "@body.json"`；注意 PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名，不是真正的 curl。

---

### Q10: `FormEvent` 被标记 deprecated（ts 6385），需要修吗？
**tag:** `TypeScript` | `React` | `事件类型`

**A10:**

需要，但**不影响构建**（弃用是提示级诊断，`tsc` 不会因此失败）。

`@types/react` 里的原文批注是：

```ts
/** @deprecated FormEvent doesn't actually exist.
 *  You probably meant to use ChangeEvent, InputEvent, SubmitEvent, or just SyntheticEvent ... */
interface FormEvent<T = Element> extends SyntheticEvent<T> {}
```

而 React 19 类型中 `onSubmit` 的真实类型是 `SubmitEventHandler<T>`（即 `EventHandler<SubmitEvent<T>>`）。所以表单提交应写：

```tsx
import type { SubmitEvent } from 'react'
async function handleSubmit(event: SubmitEvent<HTMLFormElement>): Promise<void> { ... }
```

> 坑：DOM 全局也有同名的 `SubmitEvent`，**必须显式 import** React 的那个。
> 相关：独立声明的处理函数必须自己标类型（没有上下文推断）；写成内联箭头函数 `onSubmit={(e) => ...}` 则可让 TS 推断。

---

### Q11: `const from = (location.state as { from?: string } | null)?.from ?? '/'` 看不懂
**tag:** `TypeScript` | `可选链` | `空值合并` | `路由 state`

**A11:**

背景：`<Navigate to="/login" state={{ from: location.pathname }} replace />` 把"原本想去的路径"作为**隐形数据**传给登录页（不会出现在 URL 里），登录页用 `useLocation()` 取回。

逐段拆解：

```tsx
const from = (location.state as { from?: string } | null)?.from ?? '/'
//            └──────┬──────┘ └──────────┬─────────┘ └─┬─┘ └┬┘
//                  ①                    ②             ③    ④
```

| 段 | 含义 |
|---|---|
| ① `location.state` | React Router 的隐形传参；直接打开登录页时是 `null` |
| ② `as {...} \| null` | **类型断言**：告诉 TS"它要么是 null，要么形如 `{ from?: string }`"。**只在编译期生效，运行时不转换** |
| ③ `?.from` | **可选链**：左边为 `null`/`undefined` 时整体返回 `undefined`，不会抛"Cannot read properties of null" |
| ④ `?? '/'` | **空值合并**：左边为 `null`/`undefined` 时用 `'/'` 兜底 |

`??` 与 `||` 的区别（易混）：

```ts
'' ?? 'x'   // ''    ← 空字符串是有效值，不兜底
0  ?? 5     // 0
'' || 'x'   // 'x'   ← || 会把空字符串当"没值"
```

**白话**："从这次跳转捎带的隐形数据里取 `from`；没有就用首页 `/`。"

更易读、也更安全的版本（本项目实际采用）：把规则集中到 `src/utils/navigation.ts`：

```ts
export function intendedPath(state: unknown): string {
  const from = (state as { from?: unknown } | null)?.from
  return typeof from === 'string' && from !== '' ? from : '/'   // 校验类型，而不是硬断言
}
```

---

### Q12: 从 `/projects` 被拦到登录页，登录成功后却回到 `/` 而不是 `/projects`，为什么？
**tag:** `React Router` | `路由守卫` | `跳转冲突`

**A12:**

因为**两个守卫在抢方向盘**。`GuestOnly` 原本写死：

```tsx
if (isAuthenticated) return <Navigate to="/" replace />   // ❌ 不知道用户原本想去哪
```

登录成功瞬间 `isAuthenticated` 变 true，`GuestOnly` 的跳转与登录页的 `navigate(from)` 撞车，`'/'` 赢了。

**修法：让两个守卫共用同一条"目标路径"规则**

```tsx
// RouteGuards.tsx 的 GuestOnly
if (isAuthenticated) return <Navigate to={intendedPath(location.state)} replace />
```

**通用教训**：多处跳转/多个守卫共存时，必须共用同一套"从哪来、回哪去"的规则，否则会互相覆盖。

> 自查手段：在被拦到的登录页 Console 里执行 `history.state`，能看到 `{ usr: { from: '/projects', key, idx } }` —— 说明隐形数据确实传到了。

---

### Q13: 点击"登录"完全没反应、也不跳转，Console 报 Invalid hook call，为什么？
**tag:** `React` | `Hooks 规则` | `排错`

**A13:**

因为把 **`useLocation()` 写进了 `handleSubmit`（事件处理函数）**。Hooks 只能在**函数组件 / 自定义 Hook 的最顶层**调用。

React 内部靠"每次渲染时 hook 的调用顺序"来对应内部状态（不认变量名），所以顺序一旦可能变化（放在 `if`、循环、回调、事件处理函数里）就对不上号，直接抛：

```
Invalid hook call. Hooks can only be called inside of the body of a function component.
```

| 可以 | 不可以 |
|---|---|
| 组件函数体最顶层 | ❌ 事件处理函数里 |
| 自定义 Hook 最顶层 | ❌ `if` / 循环 / `try…catch` / 嵌套函数里 |

修法：把 `const location = useLocation()` 与 `const from = intendedPath(location.state)` 移到组件函数体顶层，与其他 hook 排在一起。

> **关键习惯**：`npm run lint` 本可以提前抓到（`react-hooks/rules-of-hooks`）。写完先跑 lint + typecheck，能省掉大量"点了没反应"的排查。

---

### Q14: 登录页为什么不校验密码长度（注册页却校验 6~64 位）？
**tag:** `表单校验` | `前端校验边界` | `安全`

**A14:**

- **注册**是在**制定**密码规则 → 当然要拦；
- **登录**是在**验证已有账号** → 该账号可能是按老规则注册的，将来也可能接第三方登录。若按当前注册规则去拦，就会出现"密码明明是对的却进不去"的诡异 bug。

所以登录页只校验"非空"，其余交给后端判断。后端登录失败返回 **400 + "账号或密码错误"**，且**刻意不区分**"账号不存在"与"密码错误"，防止攻击者探测哪些账号存在。

**原则**：前端校验管**体验**，后端校验管**安全**（前端可被绕过）；前端不要做超出表单本身能确定的业务推断。

---

### Q15: 登录页和注册页的外壳几乎一样，为什么不抽成公共组件？
**tag:** `组件复用` | `工程判断`

**A15:**

判断标准：**结构重复可以先忍，样式/设计变量重复绝对不能忍。**

- 本项目 CSS（`.auth-*`）已经共用，所以登录页**零样式成本**，重复的只是十几行 JSX；
- 抽早了会痛：一旦登录页要加"记住我"、注册页要加"图形验证码"，公共组件会被塞满 `if` 分支，比重复更难维护；
- **抽象时机**：出现**第三处**同类页面（如"忘记密码"），或你发现"改一处必须同步改另一处"时，再抽 `components/common/AuthCard.tsx`（三次法则 Rule of Three）。

---

### Q16: `await` 之后调用 `window.open` 为什么被浏览器拦掉？为什么不能加 `noopener`？
**tag:** `浏览器` | `window.open` | `弹窗拦截`

**A16:**

浏览器只允许在"用户手势（transient activation）"期间弹窗。`await fetch(...)` 之后已经脱离那次点击，Safari / Firefox 会直接拦，Chrome 也不稳定。

正确做法：在**同步代码**里先占位拿到句柄，等数据回来再改地址：

```ts
const previewWindow = window.open('', '_blank')      // 同步占位，一定不被拦
const ticket = await issuePreviewTicket(taskUuid)
previewWindow.location.href = ticket.preview_url
```

**但不能加 `noopener`**：按规范，一旦指定 `noopener`，`window.open()` 必然返回 `null`，句柄就没了。想要同样的安全性，就在拿到句柄后手动 `previewWindow.opener = null`。

失败路径也要收拾：`catch` 里 `previewWindow?.close()`，否则用户会留下一个永远空白的标签页。

---

### Q17: 为什么 `localhost:5173` 和 `127.0.0.1:8000` 的 token / Cookie 不共享？
**tag:** `同源策略` | `Cookie` | `localStorage`

**A17:**

**源 = 协议 + 域名 + 端口**，三者任一不同就是不同的源；`localStorage` 与 Cookie 都按源隔离。

所以 `localhost:8000`、`127.0.0.1:8000`、`localhost:5173` 是**三个互不相通的源**（哪怕指向同一台机器）。

实例：在前端（`localhost:5173`）登录拿到 token，再到 `/docs`（`127.0.0.1:8000`）的 Console 执行 `localStorage.getItem('wgp_access_token')` 得到 `null` → 请求头变成 `Bearer null` → **401**。

预览票据同理：**在哪里签的票，就只能在那个源打开预览**。开发期请固定一套源（本项目统一 `localhost:5173`，`/api` 与 `/preview` 都走 Vite 代理）。

---

### Q18: 为什么 `document.cookie` 看不到预览票据？
**tag:** `HttpOnly` | `Cookie` | `安全`

**A18:**

票据是用 `httponly=True` 下发的（`response.set_cookie(..., httponly=True)`），**JS 读不到正是它的设计目的**：XSS 偷不走。

所以 `document.cookie` 为空**不代表 Cookie 不存在**。观察它有两条路：

1. DevTools → Application → Storage → Cookies：**必须先选中与当前页面同源的那一项**，再看 `Path` / `HttpOnly` / `Expires` 三列；
2. 更权威：Network → 刷新 → 点文档请求 → Request Headers 里的 `Cookie: wgp_preview=...`。

相关坑：票据的 `Path` 锁死在 `/preview/{user_id}/{task_uuid}/`，路径不匹配时浏览器**根本不会发送**该 Cookie（表现为 403，而不是"Cookie 不存在"）。

---

### Q19: `useCallback` 不写为什么会造成无限请求？
**tag:** `React` | `Hooks` | `useCallback`

**A19:**

`useEffect(fn, [load])` 的依赖比较的是**引用**。组件每次渲染都会重新创建 `load` 函数 → 引用变化 → effect 重跑 → 发请求 → `setItems` → 重新渲染 → 新 `load` → **死循环**。

`useCallback(fn, [])` 把函数引用固定下来，effect 就只在首次（开发环境 StrictMode 下是两次）执行。

这也正是 `react-hooks/exhaustive-deps` 这条 lint 规则的价值：它逼你把 effect 用到的外部值都写进依赖数组，从而暴露"函数引用不稳定"的问题。

判断标准：**effect 依赖里出现"组件内定义的函数"时，先问它是否稳定。**

---

## 相关文档
- 前端进度：`docs/proj_progress.md`
- 后端问答：`../../backend-uv-fastapi/docs/QA.md`
- 后端进度：`../../backend-uv-fastapi/docs/proj_progress.md`
