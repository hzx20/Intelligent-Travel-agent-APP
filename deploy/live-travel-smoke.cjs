const assert = require('node:assert/strict')
const { randomBytes } = require('node:crypto')
const { createRequire } = require('node:module')
const { resolve } = require('node:path')

const { chromium, expect } = createRequire(resolve(__dirname, '../frontend/package.json'))('@playwright/test')
const origin = 'https://134.175.137.18'
const base = `${origin}/travel/`

async function run() {
  assert.equal(process.argv[2], '--live', 'Pass --live after the public cutover is verified')
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } })
  context.setDefaultTimeout(9000)
  const page = await context.newPage()
  const checks = []
  const staticResponses = new Map()
  const errors = []
  const username = `travel_smoke_${randomBytes(7).toString('hex')}`
  const password = randomBytes(24).toString('base64url')
  const title = '上线冒烟测试旅行'
  const redact = text => String(text).replaceAll(username, '[synthetic user]').replaceAll(password, '[redacted]').slice(0, 320)
  let testAccountCreated = false
  let testTripCreated = false

  function label(url) {
    const parsed = new URL(url)
    return parsed.origin === origin ? parsed.pathname : parsed.origin
  }
  page.on('pageerror', error => errors.push({ kind: 'pageerror', message: redact(error.message) }))
  page.on('console', message => {
    if (message.type() === 'error') {
      errors.push({ kind: 'console', message: redact(message.text()) })
    }
  })
  page.on('response', response => {
    const url = new URL(response.url())
    const asset = /\/(?:assets|fonts)\//.test(url.pathname)
      || ['script', 'stylesheet', 'font', 'image'].includes(response.request().resourceType())
    if (asset && url.origin === origin) staticResponses.set(label(url), response.status())
  })

  async function check(name, test) {
    try {
      checks.push({ name, passed: true, ...await test() })
    } catch (error) {
      checks.push({ name, passed: false, detail: redact(error.message) })
    }
  }

  async function visit(route, target) {
    const response = await page.goto(base + route, { waitUntil: 'load', timeout: 14000 })
    assert.equal(response.status(), 200)
    await expect(target).toBeVisible()
    return response
  }

  try {
    let landing
    await check('首页', async () => {
      landing = await visit('', page.getByRole('heading', { name: /把想去的地方/ }))
      return { http: landing.status() }
    })
    await check('HTTPS 证书与浏览器信任', async () => {
      assert(landing, '首页尚未通过浏览器访问')
      assert.equal(new URL(page.url()).origin, origin)
      const cert = await landing.securityDetails()
      assert(cert && cert.validFrom < Date.now() / 1000 && cert.validTo > Date.now() / 1000)
      return { issuer: cert.issuer, protocol: cert.protocol, expires_utc: new Date(cert.validTo * 1000).toISOString(), certificate_warning: false }
    })
    await check('网站图标', async () => {
      const icon = await page.locator('link[rel="icon"]').getAttribute('href')
      const url = new URL(icon, base)
      assert.equal(url.origin, origin)
      assert(url.pathname.startsWith('/travel/assets/'))
      const response = await context.request.get(url.href)
      assert.equal(response.status(), 200)
      assert.match(response.headers()['content-type'], /^image\/png/)
      staticResponses.set(url.pathname, response.status())
      return { http: response.status() }
    })
    const pages = [
      ['灵感', 'inspiration', page.getByRole('heading', { name: /热门目的地/ })],
      ['天气与航班', 'travel-data', page.getByRole('heading', { name: '天气与航班', exact: true })],
      ['景点', 'spots', page.getByRole('textbox', { name: '搜索景点' })],
      ['攻略', 'guides', page.getByRole('textbox', { name: '搜索攻略' })],
      ['AI 规划', 'plan', page.getByPlaceholder('输入目的地或告诉我你的想法')],
      ['创建旅行', 'trips/new', page.getByRole('heading', { name: '创建一段新旅程' })],
    ]
    for (const [name, route, target] of pages) {
      await check(`页面 ${name}`, async () => ({ http: (await visit(route, target)).status() }))
    }
    await check('登录与个人中心', async () => {
      assert(landing, '首页尚未通过浏览器访问')
      const signup = await context.request.post(`${base}api/auth/register`, { data: { username, password }, maxRedirects: 0 })
      assert.equal(signup.status(), 201, '合成账号注册应返回 201')
      testAccountCreated = true
      await visit('', page.getByRole('heading', { name: /把想去的地方/ }))
      await page.getByRole('navigation').first().getByRole('button', { name: '登录', exact: true }).click()
      const login = page.getByRole('dialog', { name: '登录', exact: true })
      await login.getByRole('textbox', { name: '用户名', exact: true }).fill(username)
      await login.getByLabel('密码', { exact: true }).fill(password)
      const [loginResponse] = await Promise.all([
        page.waitForResponse(response => response.url() === `${base}api/auth/login`),
        login.getByRole('button', { name: '登录', exact: true }).last().click(),
      ])
      assert.equal(loginResponse.status(), 200)
      await expect(login).toHaveCount(0)
      await visit('me', page.getByRole('region', { name: '个人资料' }))
      return { login_http: 200, profile_http: 200 }
    })
    await check('核心表单', async () => {
      assert(testAccountCreated && checks.find(item => item.name === '登录与个人中心')?.passed, '需要先用合成账号登录')
      await visit('trips/new', page.getByRole('heading', { name: '创建一段新旅程' }))
      await page.getByRole('textbox', { name: '旅行名称' }).fill(title)
      await page.getByRole('spinbutton', { name: '旅行天数' }).fill('2')
      const [response] = await Promise.all([
        page.waitForResponse(response => response.url() === `${base}api/trips/create` && response.request().method() === 'POST'),
        page.getByRole('button', { name: '创建旅行 →', exact: true }).click(),
      ])
      assert.equal(response.status(), 201)
      testTripCreated = true
      const detail = page.getByRole('heading', { name: title, exact: true })
      await expect(detail).toBeVisible()
      await page.reload({ waitUntil: 'load' })
      await expect(detail).toBeVisible()
      return { create_http: response.status(), persisted_after_reload: true }
    })
    const missing = [...staticResponses].filter(([, status]) => status === 404)
    checks.push({ name: '站内静态资源', passed: staticResponses.size > 0 && missing.length === 0,
      observed: staticResponses.size, missing: missing.map(([path]) => path) })
    checks.push({ name: '浏览器控制台与页面异常', passed: errors.length === 0, count: errors.length, errors: errors.slice(0, 8) })
    console.log(JSON.stringify({ checks, test_account_created: testAccountCreated, test_trip_created: testTripCreated }))
    process.exitCode = checks.every(check => check.passed) ? 0 : 1
  } finally {
    await browser.close()
  }
}

run().catch(error => {
  console.error(JSON.stringify({ state: 'smoke_runner_failed', error_type: error.name }))
  process.exitCode = 1
})
