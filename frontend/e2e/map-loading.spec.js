import { test, expect } from '@playwright/test'
import { workspace } from './workspace-helper.js'
const ORIGIN = 'http://127.0.0.1:5264', HOME = ORIGIN + '/travel/'
const config = { enabled: true, js_key: 'synthetic-test-key', security_code: 'synthetic-code' }
const sdk = `(() => {
  class Map {
    constructor(el) { window.__testMapCreates = (window.__testMapCreates || 0) + 1; el.dataset.testMapReady = 'true' }
    add() {} remove() {} addControl() {} on(name,fn) { if(name==='complete')queueMicrotask(fn) } off() {} setFitView() {} setCenter() {} setZoomAndCenter() {} destroy() {}
  }
  window.AMap = { Map, Marker: class {}, Polyline: class {}, ToolBar: class {}, Scale: class {}, InfoWindow: class {} }
})()`
async function isolate(page) {
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/*', route => new URL(route.request().url()).origin === ORIGIN ? route.fallback() : route.abort())
  return errors
}
async function createGuest(page) {
  await page.goto(HOME)
  await page.getByRole('link', { name: '＋ 创建旅行', exact: true }).click()
  await page.getByRole('textbox', { name: '旅行名称', exact: true }).fill('地图超时合成旅行')
  await page.getByRole('button', { name: '创建旅行 →', exact: true }).click()
  await expect(page.getByRole('heading', { name: '地图超时合成旅行', exact: true })).toBeVisible()
  await workspace(page, '地图')
}
async function capture(page, info, name, errors) {
  expect(errors).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: info.outputPath(name) })
}

test('地图配置挂起后显示失败，迟到响应不插入脚本，规划页可原地重试', async ({ page }, info) => {
  const errors = await isolate(page)
  let requests = 0, external = 0, release
  const gate = new Promise(resolve => { release = resolve })
  await page.route('https://webapi.amap.com/**', route => { external++; return route.abort() })
  await page.route('**/api/map/config', async route => {
    const attempt = ++requests
    if (attempt === 1) await gate
    await route.fulfill({ json: attempt === 1 ? config : { enabled: false } }).catch(() => {})
  })
  try {
    await page.goto(HOME + 'plan')
    await expect(page.getByText('正在加载地图，仍可继续规划行程…', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '重试地图', exact: true })).toBeVisible({ timeout: 11000 })
    await expect(page.getByText(/地图配置加载超时/)).toBeVisible()
    release()
    await page.getByRole('button', { name: '重试地图', exact: true }).click()
    await expect.poll(() => requests).toBe(2)
    await expect(page.getByText(/地图暂不可用，请稍后重试/)).toBeVisible()
    expect(external).toBe(0)
    await capture(page, info, 'plan-map-timeout.png', errors)
  } finally { release() }
})

test('工作区脚本挂起后可重试，模拟SDK恢复只创建一次且行程仍可编辑', async ({ page }, info) => {
  const errors = await isolate(page)
  let scripts = 0, release
  const gate = new Promise(resolve => { release = resolve })
  await page.route('**/api/map/config', route => route.fulfill({ json: config }))
  await page.route('https://webapi.amap.com/**', async route => {
    scripts++
    if (scripts === 1) await gate
    await route.fulfill({ contentType: 'application/javascript', body: sdk }).catch(() => {})
  })
  try {
    await createGuest(page)
    await expect(page.getByText('正在加载地图，行程编辑不受影响…', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '重试地图', exact: true })).toBeVisible({ timeout: 15000 })
    await capture(page, info, 'workspace-map-timeout.png', errors)
    release()
    await page.getByRole('button', { name: '重试地图', exact: true }).click()
    await expect(page.locator('[data-test-map-ready="true"]')).toBeVisible()
    await expect(page.getByText('添加带坐标的地点后，将在这里显示。', { exact: true })).toBeVisible()
    expect(await page.evaluate(() => window.__testMapCreates)).toBe(1)
    expect(scripts).toBe(2)
    await workspace(page, '收集')
    await page.getByRole('textbox', { name: '手动输入想去的地点', exact: true }).fill('合成恢复地点')
    await page.getByRole('button', { name: '加入', exact: true }).click()
    await expect(page.getByText('合成恢复地点', { exact: true })).toBeVisible()
    await capture(page, info, 'workspace-map-recovered.png', errors)
  } finally { release() }
})

test('底图未触发完成时有界降级，重试复用SDK并重建实例', async ({ page }, info) => {
  const errors = await isolate(page)
  let scripts = 0
  const delayed = sdk.replace("if(name==='complete')queueMicrotask(fn)", "if(name==='complete' && window.__testMapCreates > 1)queueMicrotask(fn)")
    .replace('destroy() {}', 'destroy() { window.__testMapDestroyed = (window.__testMapDestroyed || 0) + 1 }')
  await page.route('**/api/map/config', route => route.fulfill({ json: config }))
  await page.route('https://webapi.amap.com/**', route => { scripts++; return route.fulfill({ contentType: 'application/javascript', body: delayed }) })
  await createGuest(page)
  await expect(page.getByText('正在加载地图，行程编辑不受影响…', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '重试地图', exact: true })).toBeVisible({ timeout: 18000 })
  expect(await page.evaluate(() => window.__testMapDestroyed)).toBe(1)
  await page.getByRole('button', { name: '重试地图', exact: true }).click()
  await expect(page.getByText('添加带坐标的地点后，将在这里显示。', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => window.__testMapCreates)).toBe(2)
  expect(scripts).toBe(1)
  await capture(page, info, 'base-map-timeout-recovered.png', errors)
})
