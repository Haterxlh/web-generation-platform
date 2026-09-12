/**
 * 从路由导航 state 中取出"用户原本想去的路径"。
 * - 被 RequireAuth 拦下时，state 形如 { from: '/projects' }
 * - 直接访问登录页时为 null
 * 取不到就回首页 '/'。
 */
export function intendedPath(state: unknown): string {
  const from = (state as { from?: unknown } | null)?.from
  // 比页内断言更安全：只接受"非空字符串"，其余一律回首页
  return typeof from === 'string' && from !== '' ? from : '/'
}
