/** 极简 API 层：fetch 封装 + token 自动携带 */
const TOKEN_KEY = 'tp_token'

// 生产构建挂在 /travel/ 子路径下，接口也要跟着加前缀；
// dev 时 BASE_URL 是 '/'，拼接结果仍是原来的 /api/...
export const BASE = import.meta.env.BASE_URL.replace(/\/$/, '')

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}
export function setToken(t) {
  t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY)
}

async function request(method, url, body, { timeout = 0 } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  /* timeout 默认 0 = 不设限（生成任务这类长请求本来就要等）。
   * 但"可选的富数据"接口必须给上限：酒店匹配内部要连高德，个别网络环境下会挂住，
   * 不设超时的话 loading 永远转下去，用户看到的就是"正在匹配…"卡死。 */
  const ctrl = timeout > 0 && typeof AbortController !== 'undefined' ? new AbortController() : null
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeout) : null
  try {
    const resp = await fetch(BASE + url, {
      method,
      headers,
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl ? ctrl.signal : undefined,
    })
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`
      try { detail = (await resp.json()).detail || detail } catch { /* keep */ }
      const err = new Error(detail)
      err.status = resp.status   // 调用方靠它区分 404（任务过期）和网络抖动
      throw err
    }
    if (resp.status === 204) return null // 删除等无响应体
    return await resp.json()
  } finally {
    if (timer) clearTimeout(timer)
  }
}

export const api = {
  get: (url, opts) => request('GET', url, undefined, opts),
  post: (url, body, opts) => request('POST', url, body, opts),
  patch: (url, body, opts) => request('PATCH', url, body, opts),
  del: (url, opts) => request('DELETE', url, undefined, opts),
  raw: (method, url) => request(method, url),
}

/**
 * 后台规划任务（v1.3 起前端走这套）
 *
 * 为什么不直接用 SSE 了：
 *   SSE 是"一条连接扛一个任务"，连接一断（切页面/刷新/关标签页）服务端就取消任务。
 *   改成"下单 + 轮询"之后，任务活在后端，前端只是个看板——断开不影响它跑。
 *   轮询还有个好处：断网恢复后接着拉就行，不用处理长连接重连那一堆状态。
 */
export const planJob = {
  /** 建任务，立刻返回 {job_id} —— 不等生成 */
  create: (payload) => request('POST', '/api/plan/jobs', payload),
  /** 拉增量：since 传上次收到的最大 seq，只回比它新的事件 */
  poll: (id, since = 0) => request('GET', `/api/plan/jobs/${encodeURIComponent(id)}?since=${since}`),
  /** 找回"我最近一条还在跑的任务"（关掉页面再打开时用） */
  active: () => request('GET', '/api/plan/jobs/active'),
  /** 取消（开新会话时顺手掐掉旧任务，别白烧模型调用） */
  cancel: (id) => request('DELETE', `/api/plan/jobs/${encodeURIComponent(id)}`),
}

/**
 * SSE 流式规划（保留给外部集成 / 调试，前端 v1.3 起不再用它）
 * POST + ReadableStream 手动解析（fetch 不支持 EventSource 的 POST）
 * 回调：onLog({node,message}) / onToken(text) / onResult(data) / onDone()
 */
export async function streamPlan(payload, { onLog, onToken, onResult, onDone, signal } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  const resp = await fetch(BASE + '/api/plan/stream', {
    method: 'POST', headers, body: JSON.stringify(payload), signal,
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
