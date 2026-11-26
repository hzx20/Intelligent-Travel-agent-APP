import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'

const source = (await readFile(new URL('../src/utils/amap.js', import.meta.url), 'utf8'))
  .replace("await import('../api')", "await Promise.resolve({ BASE: '/travel' })")
  .replace('export function loadAmap', 'function loadAmap')
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve() }
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r }); return { promise, resolve } }
const sdk = () => Object.fromEntries(['Map', 'Marker', 'Polyline', 'ToolBar', 'Scale', 'InfoWindow'].map(name => [name, class {}]))
const config = { enabled: true, js_key: 'test&key', security_code: 'synthetic-code' }

function harness() {
  const timers = new Map(), requests = [], scripts = [], window = {}
  let nextTimer = 0
  const context = {
    AbortController, window, encodeURIComponent,
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, delay }); return id },
    clearTimeout(id) { timers.delete(id) },
    fetch(url, options) { requests.push({ url, ...options }); return context.response() },
    response: async () => ({ ok: true, json: async () => config }),
    document: {
      createElement: () => ({ remove() { this.removed = true } }),
      head: { appendChild(script) { scripts.push(script) } },
    },
  }
  const load = vm.runInNewContext(source + '\nloadAmap', context)
  return {
    load, context, requests, scripts, timers, window,
    expire(delay) {
      const entry = [...timers].find(([, timer]) => timer.delay === delay)
      assert.ok(entry, `missing ${delay}ms timer`)
      timers.delete(entry[0]); entry[1].fn()
    },
    async succeed() { await flush(); window.AMap = sdk(); scripts.at(-1).onload(); return window.AMap },
  }
}

test('地图并发使用同一加载任务，成功后复用已验证SDK', async () => {
  const h = harness(), first = h.load(), second = h.load()
  assert.equal(first, second)
  await flush()
  assert.equal(h.requests.length, 1)
  assert.equal(h.requests[0].url, '/travel/api/map/config')
  assert.equal(h.scripts.length, 1)
  assert.match(h.scripts[0].src, /key=test%26key&plugin=/)
  assert.equal(h.window._AMapSecurityConfig.securityJsCode, config.security_code)
  const verified = await h.succeed()
  assert.equal(await first, verified)
  h.window.AMap = null
  assert.equal(await h.load(), verified)
  assert.equal(h.timers.size, 0)
})

test('配置请求无响应时有界失败并中止请求，迟到结果不插入脚本，重试成功', async () => {
  const h = harness(), pending = deferred()
  h.context.response = () => pending.promise
  const first = h.load(), rejected = assert.rejects(first, /配置加载超时/)
  await flush(); h.expire(8000); await rejected
  assert.equal(h.requests[0].signal.aborted, true)
  h.context.response = async () => ({ ok: true, json: async () => config })
  const retry = h.load()
  await h.succeed(); await retry
  pending.resolve({ ok: true, json: async () => config }); await flush()
  assert.equal(h.scripts.length, 1)
  assert.equal(h.timers.size, 0)
})

test('配置响应体一直未完成也会超时', async () => {
  const h = harness()
  h.context.response = async () => ({ ok: true, json: () => new Promise(() => {}) })
  const rejected = assert.rejects(h.load(), /配置加载超时/)
  await flush(); h.expire(8000); await rejected
  assert.equal(h.scripts.length, 0)
  assert.equal(h.timers.size, 0)
})

test('脚本超时移除失败节点，旧回调不能影响进行中的新尝试', async () => {
  const h = harness(), first = h.load(), rejected = assert.rejects(first, /脚本加载超时/)
  await flush()
  const old = h.scripts[0], lateLoad = old.onload, lateError = old.onerror
  h.expire(12000); await rejected
  assert.equal(old.removed, true)
  assert.equal(old.onload, null)
  const retry = h.load(); await flush()
  h.window.AMap = sdk(); lateLoad(); lateError()
  assert.equal(h.load(), retry)
  assert.equal(h.timers.size, 1)
  const verified = await h.succeed()
  assert.equal(await retry, verified)
  assert.equal(h.timers.size, 0)
})

test('脚本报告成功但SDK不完整时允许重新加载，网络错误也清理节点', async () => {
  const h = harness(), invalid = assert.rejects(h.load(), /地图对象未就绪/)
  await flush(); h.window.AMap = { Map: class {} }; h.scripts[0].onload(); await invalid
  assert.equal(h.scripts[0].removed, true)
  const failed = assert.rejects(h.load(), /脚本加载失败/)
  await flush(); h.scripts[1].onerror(); await failed
  assert.equal(h.scripts[1].removed, true)
  const retry = h.load(); await h.succeed(); await retry
  assert.equal(h.scripts.length, 3)
  assert.equal(h.timers.size, 0)
})

test('HTTP失败、禁用或异常配置不启动外部脚本', async t => {
  for (const [response, message] of [
    [{ ok: false, json: async () => config }, /配置获取失败/],
    [{ ok: true, json: async () => ({ enabled: false }) }, /MISSING_KEY/],
    [{ ok: true, json: async () => ({ ...config, js_key: ' ' }) }, /配置暂不可用/],
    [{ ok: true, json: async () => ({ ...config, enabled: 'true' }) }, /配置暂不可用/],
  ]) await t.test(String(message), async () => {
    const h = harness(); h.context.response = async () => response
    await assert.rejects(h.load(), message)
    assert.equal(h.scripts.length, 0)
    assert.equal(h.timers.size, 0)
  })
})
