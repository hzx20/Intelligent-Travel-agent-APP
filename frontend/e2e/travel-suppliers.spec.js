import { test, expect } from '@playwright/test'
const ORIGIN = 'http://127.0.0.1:5287'
async function setup(page, { configured = true, loggedIn = true, flightFailure = false } = {}) {
  const queries = []
  if (loggedIn) await page.addInitScript(() => localStorage.setItem('tp_token', 'synthetic-supplier-session'))
  await page.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.origin !== ORIGIN) return route.abort()
    if (!url.pathname.startsWith('/travel/api/')) return route.continue()
    const path = url.pathname.split('/api/')[1]
    if (path === 'auth/me') return route.fulfill({ json: { id: 1, username: 'supplier_test', nickname: '旅行者', is_admin: false } })
    if (path === 'travel-data/status') return route.fulfill({ json: { qweather: { configured }, variflight: { configured } } })
    queries.push(path)
    if (path === 'travel-data/weather') return route.fulfill({ json: { ok: true, city: '杭州', provider: 'qweather',
      casts: [{ date: '2026-11-17', day: '晴', night: '多云', temp_low: '15', temp_high: '24.5' }],
      attributions: ['https://developer.qweather.com/attribution.html'] } })
    if (path === 'travel-data/flights/query') {
      if (flightFailure) return route.fulfill({ status: 502, json: { detail: '飞常准暂不可用' } })
      const body = route.request().postDataJSON()
      const text = body.kind === 'number'
        ? JSON.stringify({ flight: body.flight_number, status: '合成验收数据' })
        : `${body.origin}-${body.destination}\n合成验收数据\n<em>供应商文本标记</em>`
      return route.fulfill({ json: { source: '飞常准', texts: [text], queried_at: '2026-11-17T02:00:00Z' } })
    }
    return route.fulfill({ status: 404, json: { detail: 'unexpected test request' } })
  })
  await page.goto('travel-data')
  return queries
}

test('天气和三类航班按点击查询，归因、子路径与窄屏布局正确', async ({ page }) => {
  const queries = await setup(page)
  await expect(page.getByLabel('城市名称')).toBeVisible()
  expect(queries).toHaveLength(0)
  await page.getByLabel('城市名称').fill('杭州')
  await page.getByRole('button', { name: '查询天气', exact: true }).click()
  await expect(page.getByRole('heading', { name: '杭州' })).toBeVisible()
  await expect(page.getByRole('link', { name: '来源声明 1' })).toHaveAttribute('href', 'https://developer.qweather.com/attribution.html')
  await page.getByLabel('航班号', { exact: true }).fill('mu2157')
  await page.getByRole('button', { name: '查询航班（使用供应商额度）', exact: true }).click()
  await expect(page.getByText('"flight": "MU2157"', { exact: false })).toBeVisible()
  await page.getByLabel('查询内容').selectOption('route')
  const origin = page.getByLabel('出发城市', { exact: true })
  const destination = page.getByLabel('到达城市', { exact: true })
  const flightDate = page.getByLabel('出发日期')
  const submitFlight = page.getByRole('button', { name: '查询航班（使用供应商额度）', exact: true })
  await expect(page.locator('#flight-cities option[value="杭州"]')).toHaveAttribute('label', 'HGH')
  await origin.fill('上海市')
  await destination.fill('ｓｈａ')
  await submitFlight.click()
  await expect(page.getByRole('alert')).toHaveText('出发和到达城市不能相同')
  await destination.fill('没有收录的城市')
  await submitFlight.click()
  await expect(page.getByRole('alert')).toHaveText('请选择常用城市，或填写城市 IATA 三字代码')
  expect(queries).toHaveLength(2)

  await destination.fill('北京')
  await flightDate.fill('2026-11-21')
  await expect(page.getByText('上海（SHA） → 北京（BJS）', { exact: true })).toBeVisible()
  expect(queries).toHaveLength(2)
  await submitFlight.click()
  await expect(page.locator('.supplier-text')).toHaveText('SHA-BJS\n合成验收数据\n<em>供应商文本标记</em>')
  await expect(page.locator('.supplier-text')).toHaveCSS('white-space', 'pre-wrap')
  await expect(page.locator('.supplier-text em')).toHaveCount(0)
  await expect(page.getByText('上海（SHA） → 北京（BJS） · 2026-11-21 · 直飞航班')).toBeVisible()

  await page.getByLabel('查询内容').selectOption('prices')
  await origin.fill('hgh')
  await destination.fill('sin')
  await flightDate.fill('2026-11-22')
  await expect(page.getByText('上海（SHA） → 北京（BJS） · 2026-11-21 · 直飞航班')).toBeVisible()
  expect(queries).toHaveLength(3)
  await submitFlight.click()
  await expect(page.locator('.supplier-text')).toContainText('HGH-SIN')
  await expect(page.getByText('杭州（HGH） → SIN · 2026-11-22 · 航班票价')).toBeVisible()
  expect(queries).toHaveLength(4)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.screenshot({ path: test.info().outputPath('suppliers.png'), fullPage: true })
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await expect(page.getByRole('heading', { name: '航班信息', exact: true })).toHaveCount(0)
  await expect(page.getByText('登录后查询旅行信息。')).toBeVisible()
})

test('未配置时不调用供应商，游客提示登录', async ({ page }) => {
  const queries = await setup(page, { configured: false })
  await expect(page.getByText('服务尚未配置，请管理员在服务端填写飞常准 key。')).toBeVisible()
  await expect(page.getByLabel('城市名称')).toHaveCount(0)
  expect(queries).toHaveLength(0)
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await expect(page.getByText('登录后查询旅行信息。')).toBeVisible()
})

test('供应商失败明确显示且不自动重试，不提示核对已保存内容', async ({ page }) => {
  const queries = await setup(page, { flightFailure: true })
  await page.getByLabel('航班号', { exact: true }).fill('MU2157')
  await page.getByRole('button', { name: '查询航班（使用供应商额度）', exact: true }).click()
  await expect(page.getByRole('alert')).toHaveText('飞常准暂不可用')
  expect(queries).toHaveLength(1)
  await expect(page.getByRole('heading', { name: '航班信息', exact: true })).toHaveCount(0)
})
