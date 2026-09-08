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
  del: (url) => request('DELETE', url),
  raw: (method, url) => request(method, url),
}

/**
 * SSE 流式规划：POST + ReadableStream 手动解析（fetch 不支持 EventSource 的 POST）
 * 回调：onLog({node,message}) / onToken(text) / onResult(data) / onDone()
 */
export async function streamPlan(payload, { onLog, onToken, onResult, onDone }) {
  const headers = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const resp = await fetch('/api/plan/stream', {
    method: 'POST', headers, body: JSON.stringify(payload),
  })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const parts = buf.split('\n\n')
    buf = parts.pop()
    for (const part of parts) {
      let ev = 'message', data = ''
      for (const line of part.split('\n')) {
        if (line.startsWith('event: ')) ev = line.slice(7).trim()
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      if (!data) continue
      try {
        const obj = JSON.parse(data)
        if (ev === 'log') onLog?.(obj)
        else if (ev === 'token') onToken?.(obj.text || '')
        else if (ev === 'result') onResult?.(obj)
        else if (ev === 'done') onDone?.()
      } catch { /* 跳过不完整块 */ }
    }
  }
}
