/** 登录态（响应式）：token + 当前用户信息，供导航栏与权限判断共用 */
import { ref } from 'vue'
import { api, getToken, setToken } from '../api'

const user = ref(null)

export function useAuth() {
  async function restore() {
    if (!getToken()) { user.value = null; return }
    try { user.value = await api.get('/api/auth/me') }
    catch { setToken(''); user.value = null }
  }
  async function login(username, password) {
    const r = await api.post('/api/auth/login', { username, password })
    setToken(r.token)
    user.value = r.user
  }
  async function register(username, password, nickname) {
    await api.post('/api/auth/register', { username, password, nickname })
    await login(username, password)
  }
  function logout() {
    setToken('')
    user.value = null
  }
  return { user, restore, login, register, logout }
}
