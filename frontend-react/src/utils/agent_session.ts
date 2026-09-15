/**
 * 当前 Agent 会话的本地记录：统一维护 localStorage 的键名，避免各处硬编码。
 *
 * 后端**没有**"会话列表"接口（只有 `GET /agent/session/{uuid}`），
 * 所以 V1 不新增后端接口：把最近一次的 session_uuid 存在本地，
 * 刷新页面后用它回放当前会话的历史消息（会话属于别人或被删则 404，前端直接丢弃）。
 *
 * ⚠️ 与 token 分开存：登出时要一起清（见 AuthProvider），否则换个账号登录
 * 会拿着上一个账号的 session_uuid 去回放 —— 后端会 404，虽然安全但体验像"数据丢了"。
 */

/** localStorage 中保存当前会话 uuid 的键名 */
const SESSION_KEY = 'wgp_agent_session'

/** 读取当前会话 uuid；没有则返回 null */
export function getAgentSessionUuid(): string | null {
  try {
    return localStorage.getItem(SESSION_KEY)
  } catch {
    // 隐私模式等场景下 localStorage 可能不可用：读不到就当没有会话，不影响对话功能
    return null
  }
}

/** 保存当前会话 uuid（新建会话成功后调用） */
export function setAgentSessionUuid(sessionUuid: string): void {
  try {
    localStorage.setItem(SESSION_KEY, sessionUuid)
  } catch {
    // 存不进去只会导致刷新后丢历史，不该让主流程报错
  }
}

/** 清除当前会话 uuid（点"开始新会话"或登出时调用） */
export function clearAgentSessionUuid(): void {
  try {
    localStorage.removeItem(SESSION_KEY)
  } catch {
    // 同上：清理失败无须上报
  }
}
