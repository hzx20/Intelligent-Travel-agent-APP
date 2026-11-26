/** 登录态（响应式）：token + 当前用户 + 全局登录弹窗，供导航栏与各页面共用 */
import { ref } from 'vue'
import { api, fetchSessionUser } from '../api'
import { TOKEN_KEY, getStoredToken, setToken, acceptStoredToken, sessionChanged, observeSessionChange, onSessionChange, beginSessionAuthentication, SessionChangedError } from './sessionToken'
import { allowAuthChange } from './authChangeGuard'
import { checkStoredSession } from './sessionRecovery'
import { mergeUserInfo } from './profileInfo'

const user = ref(null)
const showLogin = ref(false)
const loginMode = ref('login') // login | register
const restoreError = ref(''), restorePending = ref(false)
const externalSessionChange = ref(false)
let authEpoch = 0
onSessionChange(changed => { externalSessionChange.value = changed })
if (typeof window !== 'undefined') {
  window.addEventListener('storage', event => {
    if (event.key === TOKEN_KEY || event.key === null) observeSessionChange()
  })
  window.addEventListener('focus', observeSessionChange)
  document.addEventListener('visibilitychange', () => { if (!document.hidden) observeSessionChange() })
}

export function useAuth() {
  async function restore() {
    const token = getStoredToken(), changed = sessionChanged()
    observeSessionChange()
    if (changed && !allowAuthChange()) return
    const run = ++authEpoch
    restorePending.value = true
    try {
      const result = await checkStoredSession(token, () => fetchSessionUser(token),
        expected => run === authEpoch && getStoredToken() === expected)
      if (run !== authEpoch || result.status === 'superseded') return
      if (result.status === 'authenticated') {
        if (!changed && user.value?.id !== result.user.id && !allowAuthChange()) {
          restoreError.value = '已取消恢复账号，未保存的内容仍保留。'; return
        }
        acceptStoredToken(token)
        if (user.value?.id === result.user.id) Object.assign(user.value, mergeUserInfo(user.value, result.user))
        else user.value = result.user
        restoreError.value = ''
      } else if (result.status === 'expired') {
        if (!changed && user.value && !allowAuthChange()) {
          restoreError.value = '登录已过期，未保存的内容仍保留。请处理草稿后重新登录。'; return
        }
        if (getStoredToken() !== token) { observeSessionChange(); return }
        setToken(''); user.value = null; restoreError.value = '登录已过期，请重新登录。'
      } else if (result.status === 'unavailable') {
        restoreError.value = '暂时无法确认登录状态，请检查网络后重试。'
      } else { acceptStoredToken(token); user.value = null; restoreError.value = '' }
    } finally { if (run === authEpoch) restorePending.value = false }
  }
  async function login(username, password, skipGuard = false) {
    if (!skipGuard && !allowAuthChange()) throw new Error('已取消登录，未保存的内容仍保留')
    const session = beginSessionAuthentication(), run = ++authEpoch
    restorePending.value = false
    let r
    try { r = await api.post('/api/auth/login', { username, password }, {timeout: 15000}) }
    catch (error) { if (error.name === 'AbortError') throw new Error('登录请求超时，请稍后重试'); throw error }
    session.check()
    if (run !== authEpoch) throw new SessionChangedError(false)
    setToken(r.token)
    if (user.value?.id === r.user.id) Object.assign(user.value, mergeUserInfo(user.value,r.user))
    else user.value = r.user
    restoreError.value = ''
  }
  async function register(username, password, nickname) {
    if (!allowAuthChange()) throw new Error('已取消注册，未保存的内容仍保留')
    ++authEpoch; restorePending.value = false
    try { await api.post('/api/auth/register', { username, password, nickname }, {timeout: 15000}) }
    catch (error) { if (error.name === 'AbortError') throw new Error('注册请求超时，无法确认是否已完成，请尝试登录或稍后重试'); throw error }
    try { await login(username, password, true) }
    catch (error) { throw new Error(`注册已完成，但自动登录失败：${error.message}。请切换到登录重试。`) }
  }
  function logout() {
    if (observeSessionChange()) return false
    if (!allowAuthChange()) return false
    if (observeSessionChange()) return false
    ++authEpoch; restorePending.value = false; restoreError.value = ''
    setToken('')
    user.value = null
    return true
  }
  function openLogin(mode = 'login') {
    loginMode.value = mode
    showLogin.value = true
  }
  function acceptProfile(profile) {
    if (!user.value || user.value.id !== profile?.id || !Number.isSafeInteger(profile.version) || profile.version < 1 ||
        profile.version < (user.value.profile_version || 0) ||
        typeof profile.nickname !== 'string' || typeof profile.avatar !== 'string') return false
    Object.assign(user.value, mergeUserInfo(user.value, {...user.value, nickname:profile.nickname, avatar:profile.avatar, profile_version:profile.version}))
    return true
  }
  return { user, showLogin, loginMode, restore, restoreError, restorePending, externalSessionChange, login, register, logout, openLogin, acceptProfile }
}
