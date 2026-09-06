/** 极简 API 层：fetch 封装 + token 自动携带 */
const TOKEN_KEY = 'tp_token'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function setToken(t) {
  t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY)
}

async function request(method, url, body) {
  const headers = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const resp = await fetch(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try { detail = (await resp.json()).detail || detail } catch { /* keep */ }
    throw new Error(detail)
  }
  if (resp.status === 204) return null // 删除等无响应体
  return resp.json()
}

export const api = {
  get: (url) => request('GET', url),
  post: (url, body) => request('POST', url, body),
  patch: (url, body) => request('PATCH', url, body),
  raw: (method, url) => request(method, url),
}
