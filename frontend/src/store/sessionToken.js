/** Keep each tab's accepted identity separate from the shared login preference. */
export const TOKEN_KEY = 'tp_token'
let acceptedToken, verified = false, revision = 0
const listeners = new Set()

export class SessionChangedError extends Error {
  constructor(external = true) {
    super(external ? '其他页面已更改登录状态，请处理未保存内容后同步账号。已发出的操作可能仍会完成。'
      : '登录状态已变化，请核对当前账号后重试。已发出的操作可能仍会完成。')
    this.name = 'SessionChangedError'; this.code = 'SESSION_CHANGED'
  }
}
export function getStoredToken() { return localStorage.getItem(TOKEN_KEY) || '' }
export function getToken() {
  if (acceptedToken === undefined) acceptedToken = getStoredToken()
  return acceptedToken
}
export function sessionChanged() { return getStoredToken() !== getToken() }
export function observeSessionChange() {
  const changed = sessionChanged()
  for (const listener of listeners) listener(changed)
  return changed
}
export function onSessionChange(listener) { listeners.add(listener); return () => listeners.delete(listener) }
export function acceptStoredToken(token) {
  if (getStoredToken() !== token) { observeSessionChange(); throw new SessionChangedError() }
  acceptedToken = token; verified = true; revision++
  observeSessionChange()
}
export function setToken(token) {
  token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY)
  acceptedToken = token || ''; verified = true; revision++
  observeSessionChange()
}
export function beginSessionRequest() {
  if (sessionChanged()) { observeSessionChange(); throw new SessionChangedError() }
  const token = getToken(), started = revision
  if (token && !verified) {
    const error = new Error('暂时无法确认登录状态，请重试恢复账号后继续。')
    error.name = 'SessionUnverifiedError'; error.code = 'SESSION_UNVERIFIED'
    throw error
  }
  return { token, check() {
    const changed = sessionChanged()
    if (started !== revision || token !== getToken() || changed) {
      observeSessionChange(); throw new SessionChangedError(changed)
    }
  } }
}
// Only the identity check may use a not-yet-accepted shared credential. It never
// changes the accepted identity or grants ordinary requests permission to run.
export function beginSessionCheck(token) {
  const check = () => {
    if (token !== getStoredToken()) { observeSessionChange(); throw new SessionChangedError() }
  }
  check()
  return { token, check }
}
// Explicit login/registration is allowed even if stored identity restoration
// failed. Never attach its unknown credential to the authentication request.
export function beginSessionAuthentication() {
  const stored = getStoredToken(), started = revision
  return { token: '', check() {
    if (stored !== getStoredToken() || started !== revision) {
      observeSessionChange(); throw new SessionChangedError(sessionChanged())
    }
  } }
}
