import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

async function fixture({ verified = true } = {}) {
  const previous = globalThis.localStorage
  let stored = 'account-A'
  globalThis.localStorage = { getItem: () => stored, setItem: (_, value) => { stored = value }, removeItem: () => { stored = '' } }
  const tokenUrl = new URL('../src/store/sessionToken.js', import.meta.url).href+'?test='+Math.random()
  const tokens = await import(tokenUrl)
  if (verified) tokens.acceptStoredToken(stored)
  return { tokens, tokenUrl, change(value) { stored = value }, stored: () => stored,
    cleanup() { globalThis.localStorage = previous } }
}

test('持久化凭证核对失败前不发送普通请求，核对本身不自动授予访问', async () => {
  const f = await fixture({ verified: false })
  try {
    const t = f.tokens
    assert.throws(() => t.beginSessionRequest(), { name: 'SessionUnverifiedError' })
    t.beginSessionCheck('account-A').check()
    assert.throws(() => t.beginSessionRequest(), { name: 'SessionUnverifiedError' })
    const authentication = t.beginSessionAuthentication()
    assert.equal(authentication.token, '')
    authentication.check()
    t.acceptStoredToken('account-A')
    assert.equal(t.beginSessionRequest().token, 'account-A')
    assert.throws(() => authentication.check(), { name: 'SessionChangedError' })
  } finally { f.cleanup() }
})

test('共享凭证变化时阻止普通请求，身份核对不自动接受新账号', async () => {
  const f = await fixture()
  try {
    const t = f.tokens, changes = []
    t.onSessionChange(changed => changes.push(changed))
    const old = t.beginSessionRequest()
    assert.equal(old.token, 'account-A')
    f.change('account-B')
    assert.equal(t.getToken(), 'account-A')
    assert.throws(() => t.beginSessionRequest(), t.SessionChangedError)
    assert.throws(() => old.check(), t.SessionChangedError)
    const identity = t.beginSessionCheck('account-B')
    identity.check()
    assert.equal(t.getToken(), 'account-A')
    assert.equal(changes.at(-1), true)
    t.acceptStoredToken('account-B')
    assert.equal(t.beginSessionRequest().token, 'account-B')
    assert.equal(changes.at(-1), false)
    assert.throws(() => old.check(), t.SessionChangedError)
  } finally { f.cleanup() }
})

test('核对期间再次换号不接受旧核对结果，相同凭证重新接受也使旧请求失效', async () => {
  const f = await fixture()
  try {
    const t = f.tokens, old = t.beginSessionRequest(), identity = t.beginSessionCheck('account-A')
    f.change('account-C')
    assert.throws(() => identity.check(), t.SessionChangedError)
    assert.throws(() => t.acceptStoredToken('account-A'), t.SessionChangedError)
    assert.equal(f.stored(), 'account-C')
    f.change('account-A'); t.acceptStoredToken('account-A')
    assert.throws(() => old.check(), t.SessionChangedError)
  } finally { f.cleanup() }
})

async function apiFixture() {
  const f = await fixture(), previousFetch = globalThis.fetch
  const source = (await readFile(new URL('../src/api/index.js', import.meta.url), 'utf8'))
    .replaceAll("'../store/sessionToken'", JSON.stringify(f.tokenUrl))
    .replaceAll("'../utils/apiFailure'", JSON.stringify(new URL('../src/utils/apiFailure.js', import.meta.url).href))
    .replace('import.meta.env.BASE_URL', JSON.stringify('/'))
  const api = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64')+'#'+Math.random())
  f.tokens.getToken()
  return { ...f, api, cleanup() { globalThis.fetch = previousFetch; f.cleanup() } }
}

test('实际 JSON、照片与上传 API 拒绝换号后的迟到响应', async () => {
  for (const kind of ['json', 'photo', 'upload']) {
    const f = await apiFixture()
    try {
      let finish, calls = 0
      globalThis.fetch = async () => { calls++; return new Promise(resolve => { finish = resolve }) }
      const pending = kind === 'json' ? f.api.api.get('/api/trips') : kind === 'photo'
        ? f.api.api.photo('/api/photo') : f.api.api.uploadPhoto('/api/photo', new Blob(['synthetic']), 1)
      f.change('account-B')
      finish(new Response(kind === 'photo' ? 'synthetic-image' : '{}'))
      await assert.rejects(pending, { name: 'SessionChangedError' })
      await assert.rejects(f.api.api.post('/api/trips', {}), { name: 'SessionChangedError' })
      assert.equal(calls, 1)
    } finally { f.cleanup() }
  }
})

test('流式回调在账号变化后停止，释放读取器且不输出迟到内容', async () => {
  const f = await apiFixture()
  try {
    let controller, cancelled = false
    const stream = new ReadableStream({ start(value) { controller = value }, cancel() { cancelled = true } })
    globalThis.fetch = async () => new Response(stream)
    const received = []
    const pending = f.api.streamPlan({}, { onToken: value => received.push(value) })
    await new Promise(resolve => setImmediate(resolve))
    f.change('account-B')
    controller.enqueue(new TextEncoder().encode('event: token\ndata: {"text":"old account"}\n\n'))
    await assert.rejects(pending, { name: 'SessionChangedError' })
    assert.deepEqual(received, [])
    assert.equal(cancelled, true)
  } finally { f.cleanup() }
})

test('请求超时释放等待，写入标记结果未知且没有自动重试', async () => {
  for (const method of ['get', 'post']) {
    const f = await apiFixture()
    try {
      let calls = 0
      globalThis.fetch = async (_, options) => {
        calls++
        return new Promise((_, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))))
      }
      const pending = method === 'get' ? f.api.api.get('/api/trips', {timeout:5})
        : f.api.api.post('/api/trips', {}, {timeout:5})
      await assert.rejects(pending, error => error.code === 'REQUEST_TIMEOUT' && error.outcomeUnknown === (method === 'post'))
      assert.equal(calls, 1)
    } finally { f.cleanup() }
  }
})

test('连接失败和损坏的写入响应提示先核对，HTTP版本冲突仍保留原状态码', async () => {
  const f = await apiFixture()
  try {
    globalThis.fetch = async () => { throw new TypeError('Failed to fetch') }
    await assert.rejects(f.api.api.post('/api/trips', {}), error => error.code === 'NETWORK_ERROR' && error.outcomeUnknown)
    globalThis.fetch = async () => new Response('not-json')
    await assert.rejects(f.api.api.post('/api/trips', {}), error => error.code === 'INVALID_RESPONSE' && error.outcomeUnknown)
    globalThis.fetch = async () => new Response('{"detail":"version conflict"}', {status:409})
    await assert.rejects(f.api.api.post('/api/trips', {}), error => error.status === 409 && !error.outcomeUnknown)
    globalThis.fetch = async () => new Response('{"detail":"temporary failure"}', {status:503})
    await assert.rejects(f.api.api.post('/api/trips', {}), error => error.status === 503 && error.outcomeUnknown && error.message.includes('结果暂时无法确认'))
  } finally { f.cleanup() }
})
