// Exercise the saved production bundle through an isolated local nginx proxy.
import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'
import { chromium, expect } from '@playwright/test'

const origin = process.argv[2], output = process.argv[3]
assert.equal(new URL(origin).hostname, '127.0.0.1')
await mkdir(output, { recursive: true })
const config = { enabled: true, js_key: 'synthetic-bundle-key', security_code: 'synthetic-code' }
const sdk = `(() => {
 class Map {
  constructor(el) { window.__mapCreates=(window.__mapCreates||0)+1;el.dataset.bundleMapReady='true' }
  add() {} remove() {} addControl() {} on(name,fn) { if(name==='complete')queueMicrotask(fn) } off() {} setFitView() {} setCenter() {} setZoomAndCenter() {} destroy() {}
 }
 window.AMap={Map,Marker:class {},Polyline:class {},ToolBar:class {},Scale:class {},InfoWindow:class {}}
})()`
const browser = await chromium.launch({ channel: process.env.TRAVEL_E2E_CHANNEL || 'msedge', headless: true })
const results = []
try {
 for (const [name, viewport] of [['desktop', { width: 1280, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
  for (const scenario of ['config-timeout', 'script-timeout']) {
   const context = await browser.newContext({ viewport, isMobile: name === 'mobile', hasTouch: name === 'mobile' })
   let release
   try {
    const page = await context.newPage(), errors = [], gate = new Promise(resolve => { release = resolve })
    let configs = 0, scripts = 0
    page.on('pageerror', e => errors.push(e.message))
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort())
    await page.route('**/api/map/config', async route => {
     const attempt = ++configs
     if (scenario === 'config-timeout' && attempt === 1) await gate
     await route.fulfill({ json: scenario === 'config-timeout' && attempt > 1 ? { enabled: false } : config }).catch(() => {})
    })
    await page.route('https://webapi.amap.com/**', async route => {
     scripts++
     if (scenario === 'script-timeout' && scripts === 1) await gate
     await route.fulfill({ contentType: 'application/javascript', body: sdk }).catch(() => {})
    })
    if (scenario === 'config-timeout') {
     await page.goto(origin + '/travel/plan')
     await expect(page.getByText('正在加载地图，仍可继续规划行程…', { exact: true })).toBeVisible()
     await expect(page.getByRole('button', { name: '重试地图', exact: true })).toBeVisible({ timeout: 11000 })
     await expect(page.getByText(/地图配置加载超时/)).toBeVisible()
     release()
     await page.getByRole('button', { name: '重试地图', exact: true }).click()
     await expect(page.getByText(/地图暂不可用，请稍后重试/)).toBeVisible()
     assert.equal(configs, 2); assert.equal(scripts, 0)
    } else {
     await page.goto(origin + '/travel/')
     await page.getByRole('link', { name: '＋ 创建旅行', exact: true }).click()
     await page.getByRole('textbox', { name: '旅行名称', exact: true }).fill('生产包地图合成旅行')
     await page.getByRole('button', { name: '创建旅行 →', exact: true }).click()
     await page.getByRole('heading', { name: '生产包地图合成旅行', exact: true }).waitFor()
     const tabs = page.getByRole('navigation', { name: '旅行工作区' })
     await tabs.getByRole('button', { name: /^地图/ }).click()
     await expect(page.getByText('正在加载地图，行程编辑不受影响…', { exact: true })).toBeVisible()
     await expect(page.getByRole('button', { name: '重试地图', exact: true })).toBeVisible({ timeout: 15000 })
     await page.screenshot({ path: join(output, name + '-script-failed.png') })
     release()
     await page.getByRole('button', { name: '重试地图', exact: true }).click()
     await expect(page.locator('[data-bundle-map-ready="true"]')).toBeVisible()
     assert.equal(scripts, 2); assert.equal(await page.evaluate(() => window.__mapCreates), 1)
     await tabs.getByRole('button', { name: /^收集/ }).click()
     await page.getByRole('textbox', { name: '手动输入想去的地点', exact: true }).fill('生产包恢复地点')
     await page.getByRole('button', { name: '加入', exact: true }).click()
     await expect(page.getByText('生产包恢复地点', { exact: true })).toBeVisible()
    }
    assert.deepEqual(errors, [])
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
    await page.screenshot({ path: join(output, name + '-' + scenario + '.png') })
    results.push({ viewport: name, scenario, passed: true, configs, simulated_scripts: scripts, page_errors: errors.length })
   } finally { release?.(); await context.close() }
  }
 }
} finally { await browser.close() }
process.stdout.write(JSON.stringify({ production_map_smoke: results, external_provider_called: false }) + '\n')
