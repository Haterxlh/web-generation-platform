/**
 * 本地登录凭证存储：统一维护 localStorage 的键名，避免各处硬编码字符串。
 * 这里只存 JWT 字符串；用户信息由 AuthContext（步骤 3）在内存中维护。
 */

/** localStorage 中保存 JWT 的键名 */
const TOKEN_KEY = 'wgp_access_token'

/** 读取 token；未登录返回 null */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

/** 保存 token（登录成功后调用） */
export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

/** 清除 token（登出或登录态失效时调用） */
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}
