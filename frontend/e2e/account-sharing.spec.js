import {workspace} from './workspace-helper.js'
import { randomUUID } from 'node:crypto'
import { test, expect } from '@playwright/test'

const ORIGIN = 'http://127.0.0.1:5264'
const HOME = ORIGIN+'/travel/'
const PASSWORD = 'isolated-browser-test-password'
const DEPARTURE = '还有未保存的输入、仅在本页显示的分享地址或正在进行的操作。离开后内容可能丢失，确定离开吗？'

async function localOnly(page) {
  await page.route('**/*', route => new URL(route.request().url()).origin === ORIGIN
    ? route.continue() : route.abort('blockedbyclient'))
}
async function account(request, nickname) {
  const username = 'qa_'+randomUUID().replaceAll('-', '').slice(0, 16)
  const registered = await request.post(ORIGIN+'/travel/api/auth/register', {
    data: { username, nickname, password: PASSWORD },
  })
  expect(registered.status()).toBe(201)
  const login = await request.post(ORIGIN+'/travel/api/auth/login', { data: { username, password: PASSWORD } })
  expect(login.status()).toBe(200)
  return { username, nickname, ...await login.json() }
}
async function loginForm(page, user) {
  const dialog = page.getByRole('dialog', { name: '登录', exact: true })
  await dialog.getByRole('textbox', { name: '用户名', exact: true }).fill(user.username)
  await dialog.getByLabel('密码', { exact: true }).fill(PASSWORD)
  return dialog
}
async function login(page, user) {
  await page.getByRole('navigation').first().getByRole('button', { name: '登录', exact: true }).click()
  const dialog = await loginForm(page, user)
  await dialog.getByRole('button', { name: '登录', exact: true }).last().click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
}
async function createTrip(page, title) {
  await page.getByRole('link', { name: '＋ 创建旅行', exact: true }).click()
  await page.getByRole('textbox', { name: '旅行名称', exact: true }).fill(title)
  await page.getByRole('spinbutton', { name: '旅行天数', exact: true }).fill('1')
  await page.getByRole('button', { name: '创建旅行 →', exact: true }).click()
  await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible()
  return page.url()
}
async function confirm(page, trigger, accept, message = DEPARTURE) {
  const handled = page.waitForEvent('dialog').then(async dialog => {
    const observed = { type: dialog.type(), message: dialog.message() }
    if (accept) await dialog.accept()
    else await dialog.dismiss()
    expect(observed).toEqual({ type: 'confirm', message })
  })
  await Promise.all([handled, trigger()])
}
async function editTheme(page, value) {
  await page.getByRole('button', { name: '编辑主题', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '编辑当天主题', exact: true })
  await dialog.getByRole('textbox', { name: '每日主题', exact: true }).fill(value)
  await dialog.getByRole('button', { name: '保存主题', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('heading', { name: `第 1 天 ${value}`, exact: true })).toBeVisible()
}
test.beforeEach(async ({ page }) => { await localOnly(page); await page.goto(HOME) })

test('登录弹窗约束键盘焦点，Esc 关闭并清除密码', async ({ page }, testInfo) => {
  const opener = page.getByRole('navigation').first().getByRole('button', { name: '登录', exact: true })
  await opener.click()
  const dialog = page.getByRole('dialog', { name: '登录', exact: true })
  await dialog.getByLabel('密码', { exact: true }).fill('synthetic-unsent-password')
  for (const key of ['Tab', 'Shift+Tab']) {
    for (let index = 0; index < 8; index++) {
      await page.keyboard.press(key)
      expect(await dialog.evaluate(element => element.contains(document.activeElement))).toBe(true)
    }
  }
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await expect(opener).toBeFocused()
  await opener.click()
  await expect(dialog.getByLabel('密码', { exact: true })).toHaveValue('')
  await page.screenshot({ path: testInfo.outputPath('login-dialog.png') })
})

test('注册弹窗通过真实接口注册并登录，重载后恢复账号', async ({ page }) => {
  await page.getByRole('navigation').first().getByRole('button', { name: '注册', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '注册', exact: true })
  await dialog.getByRole('textbox', { name: '用户名', exact: true }).fill('qa_'+randomUUID().replaceAll('-', '').slice(0, 16))
  await dialog.getByRole('textbox', { name: '昵称', exact: true }).fill('合成页面注册账号')
  await dialog.getByLabel('密码', { exact: true }).fill(PASSWORD)
  await dialog.getByRole('button', { name: '注册并登录', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
})

test('游客登录前取消不发送凭证，确认后清除草稿并保留本机旅行', async ({ page, request }) => {
  const owner = await account(request, '合成账号A')
  await createTrip(page, '游客登录保护')
  await workspace(page,'助手')
  const draft = page.getByRole('textbox', { name: '告诉 AI 你的规划需求', exact: true })
  await draft.fill('登录取消后保留的需求')
  let loginRequests = 0
  page.on('request', req => { if (req.url().endsWith('/api/auth/login')) loginRequests++ })
  await page.getByRole('navigation').first().getByRole('button', { name: '登录', exact: true }).click()
  const dialog = await loginForm(page, owner)
  const submit = dialog.getByRole('button', { name: '登录', exact: true }).last()
  await confirm(page, () => submit.click(), false)
  await expect(dialog.getByText('已取消登录，未保存的内容仍保留', { exact: true })).toBeVisible()
  expect(loginRequests).toBe(0)
  await dialog.getByRole('button', { name: '取消', exact: true }).click()
  await workspace(page,'助手')
  await expect(draft).toHaveValue('登录取消后保留的需求')
  await page.getByRole('navigation').first().getByRole('button', { name: '登录', exact: true }).click()
  await loginForm(page, owner)
  await confirm(page, () => submit.click(), true)
  await expect(dialog).toHaveCount(0)
  await workspace(page,'助手')
  await expect(draft).toHaveValue('')
  await expect(page.getByRole('heading', { name: '游客登录保护', exact: true })).toBeVisible()
  expect(loginRequests).toBe(1)
  await page.reload()
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '游客登录保护', exact: true })).toBeVisible()
})

test('云端退出取消保留草稿，切换账号后拒绝读取上一账号旅行', async ({ page, request }) => {
  const owner = await account(request, '原账号')
  const other = await account(request, '切换账号')
  await login(page, owner)
  const tripUrl = await createTrip(page, '只能由原账号查看')
  await workspace(page,'助手')
  const draft = page.getByRole('textbox', { name: '攻略正文', exact: true })
  await draft.fill('原账号未发送的正文')
  const logout = page.getByRole('button', { name: '退出', exact: true })
  await confirm(page, () => logout.click(), false)
  await workspace(page,'助手')
  await expect(draft).toHaveValue('原账号未发送的正文')
  await confirm(page, () => logout.click(), true)
  await expect(page.getByText('登录后查看旅行。', { exact: false })).toBeVisible()
  await login(page, other)
  await expect(page.getByRole('alert').filter({ hasText: '旅行不存在' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '只能由原账号查看', exact: true })).toHaveCount(0)
  expect(new URL(page.url()).pathname).toBe(new URL(tripUrl).pathname)
  const tripId = new URL(tripUrl).pathname.split('/').at(-1)
  const denied = await request.get(ORIGIN+`/travel/api/trips/${tripId}`, { headers: { Authorization: 'Bearer '+other.token } })
  expect(denied.status()).toBe(404)
})

test('双账号协作：邀请、同步保留草稿、刷新确认及撤权后拒绝保存', async ({ page, browser, request }, testInfo) => {
  const owner = await account(request, '合成创建者')
  const editor = await account(request, '合成同行者')
  await login(page, owner)
  const tripUrl = await createTrip(page, '双账号协作验收')
  await workspace(page,'同行')
  await page.getByRole('button', { name: '生成编辑邀请', exact: true }).click()
  const address = page.getByRole('textbox', { name: '分享地址', exact: true })
  await expect(address).toBeVisible()
  const invitation = await address.inputValue()
  await page.getByRole('button', { name: '收起地址', exact: true }).click()
  const editorContext = await browser.newContext({ baseURL: HOME,
    viewport: testInfo.project.use.viewport, isMobile: testInfo.project.use.isMobile,
    hasTouch: testInfo.project.use.hasTouch })
  try {
    const editorPage = await editorContext.newPage()
    await localOnly(editorPage)
    await editorPage.goto(invitation)
    await editorPage.getByRole('button', { name: '登录后继续', exact: true }).click()
    const loginDialog = await loginForm(editorPage, editor)
    await loginDialog.getByRole('button', { name: '登录', exact: true }).last().click()
    await editorPage.getByRole('button', { name: '接受邀请并加入', exact: true }).click()
    await expect(editorPage.getByRole('heading', { name: '双账号协作验收', exact: true })).toBeVisible()
    await workspace(page,'助手')
    const ownerDraft = page.getByRole('textbox', { name: '告诉 AI 你的规划需求', exact: true })
    await ownerDraft.fill('同行者更新时不能覆盖这段需求')
    await editTheme(editorPage, '同行者已更新主题')
    const update = page.getByRole('status').filter({ hasText: '同行者更新了旅行' })
    await expect(update).toBeVisible({ timeout: 15000 })
    await expect(ownerDraft).toHaveValue('同行者更新时不能覆盖这段需求')
    const refresh = page.locator('header').getByRole('button', { name: '刷新旅行', exact: true })
    const refreshMessage = '刷新会关闭编辑窗口并清除未应用的建议、输入和仅在本页显示的分享地址，确定继续？'
    await confirm(page, () => refresh.click(), false, refreshMessage)
    await expect(ownerDraft).toHaveValue('同行者更新时不能覆盖这段需求')
    await confirm(page, () => refresh.click(), true, refreshMessage)
    await workspace(page,'行程')
    await expect(page.getByRole('heading', { name: '第 1 天 同行者已更新主题', exact: true })).toBeVisible()
    await workspace(page,'助手')
    await expect(ownerDraft).toHaveValue('')

    // Revoke the invitation separately: existing membership remains until removed.
    await workspace(page,'同行')
    await page.getByText('管理链接（1）', { exact: true }).click()
    await confirm(page, () => page.getByRole('button', { name: '撤销', exact: true }).click(), true,
      '撤销后，这个链接将无法再打开或用于加入。已加入的成员需单独移除。')
    await expect(page.getByText('链接已撤销。', { exact: true })).toBeVisible()
    await workspace(editorPage,'助手')
    const editorDraft = editorPage.getByRole('textbox', { name: '告诉 AI 你的规划需求', exact: true })
    await editorDraft.fill('撤权后仍应显示的草稿')
    await workspace(page,'同行')
    await page.getByText('同行成员（1）', { exact: true }).click()
    await confirm(page, () => page.getByRole('button', { name: '移除', exact: true }).click(), true,
      '移除 合成同行者 的编辑权限？仍有效的邀请链接可再次加入。')
    await expect(editorPage.getByRole('alert').filter({ hasText: '当前账号已无法访问此旅行' })).toBeVisible({ timeout: 15000 })
    await expect(editorDraft).toHaveValue('撤权后仍应显示的草稿')
    await workspace(editorPage,'行程')
    await expect(editorPage.getByRole('button', { name: '＋ 添加安排', exact: true })).toBeDisabled()
    const tripId = new URL(tripUrl).pathname.split('/').at(-1)
    const denied = await request.post(ORIGIN+`/travel/api/trips/${tripId}/days/1/items`, {
      headers: { Authorization: 'Bearer '+editor.token }, data: { version: 1, name: '撤权后不应写入' },
    })
    expect(denied.status()).toBe(404)
    const saved = await request.get(ORIGIN+`/travel/api/trips/${tripId}`, { headers: { Authorization: 'Bearer '+owner.token } })
    expect((await saved.json()).days[0].items).toEqual([])
    const rejoin = await request.post(ORIGIN+'/travel/api/trip-sharing/accept', {
      headers: { Authorization: 'Bearer '+editor.token }, data: { token: new URL(invitation).hash.slice(1) },
    })
    expect(rejoin.status()).toBe(404)
  } finally { await editorContext.close() }
})

test('只读分享包含公开安排但不发送预订记录，撤销后无法重新打开', async ({ page, browser, request }, testInfo) => {
  const owner = await account(request, '只读分享创建者')
  await login(page, owner)
  await createTrip(page, '只读隐私验收')
  await workspace(page,'记录')
  const reservations = page.locator('section.reservations')
  await reservations.getByRole('textbox', { name: '名称', exact: true }).fill('仅同行者可见的合成酒店')
  await reservations.getByRole('textbox', { name: '订单号（可留空）', exact: true }).fill('PRIVATE-TEST-REFERENCE')
  await reservations.getByRole('button', { name: '保存记录', exact: true }).click()
  await expect(reservations.getByText('订单号：PRIVATE-TEST-REFERENCE', { exact: true })).toBeVisible()
  await workspace(page,'行程')
  await page.getByRole('button', { name: '＋ 添加安排', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '添加安排', exact: true })
  await dialog.getByRole('textbox', { name: '名称', exact: true }).fill('公开安排')
  await dialog.getByRole('textbox', { name: '备注', exact: true }).fill('公开备注')
  await dialog.getByRole('button', { name: '保存安排', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await workspace(page,'同行')
  await page.getByRole('button', { name: '生成只读链接', exact: true }).click()
  const address = page.getByRole('textbox', { name: '分享地址', exact: true })
  await expect(address).toBeVisible()
  const url = await address.inputValue()
  const viewerContext = await browser.newContext({ viewport: testInfo.project.use.viewport,
    isMobile: testInfo.project.use.isMobile, hasTouch: testInfo.project.use.hasTouch })
  try {
    const viewer = await viewerContext.newPage()
    await localOnly(viewer)
    const response = viewer.waitForResponse(res => res.url().endsWith('/api/trip-sharing/view'))
    await viewer.goto(url)
    const payload = await (await response).json()
    expect(payload).not.toHaveProperty('reservations')
    expect(JSON.stringify(payload)).not.toContain('PRIVATE-TEST-REFERENCE')
    expect(JSON.stringify(payload)).not.toContain('合成酒店')
    await expect(viewer.getByRole('heading', { name: '公开安排', exact: true })).toBeVisible()
    await expect(viewer.getByText('公开备注', { exact: true })).toBeVisible()
    await expect(viewer.getByRole('heading', { name: '预订记录', exact: true })).toHaveCount(0)
    await expect(viewer.getByRole('button', { name: '编辑', exact: true })).toHaveCount(0)
    await page.getByText('管理链接（1）', { exact: true }).click()
    await confirm(page, () => page.getByRole('button', { name: '撤销', exact: true }).click(), true,
      '撤销后，这个链接将无法再打开或用于加入。已加入的成员需单独移除。')
    await expect(page.getByText('链接已撤销。', { exact: true })).toBeVisible()
    await viewer.reload()
    await expect(viewer.getByRole('alert')).toContainText('链接无效、已撤销或已过期')
    await expect(viewer.getByRole('heading', { name: '公开安排', exact: true })).toHaveCount(0)
    await workspace(page,'记录')
    await expect(reservations.getByText('订单号：PRIVATE-TEST-REFERENCE', { exact: true })).toBeVisible()
  } finally { await viewerContext.close() }
})

test('同浏览器换号：保留旧页草稿并阻止错账号写入，确认同步后才切换', async ({ page, context, request }, testInfo) => {
  const owner = await account(request, '跨标签原账号')
  const other = await account(request, '跨标签新账号')
  await login(page, owner)
  const tripUrl = await createTrip(page, '跨标签账号隔离')
  await workspace(page,'助手')
  const draft = page.getByRole('textbox', { name: '告诉 AI 你的规划需求', exact: true })
  await draft.fill('同步取消后必须保留的草稿')
  const second = await context.newPage()
  try {
    await localOnly(second)
    await second.goto(HOME)
    await second.getByRole('button', { name: '退出', exact: true }).click()
    await login(second, other)
    const notice = page.getByRole('status').filter({ hasText: '其他页面已更改登录状态' })
    await expect(notice).toBeVisible()
    await workspace(page,'助手')
  await expect(draft).toHaveValue('同步取消后必须保留的草稿')

    // The stale tab must not clear the other tab's newly accepted login.
    await page.getByRole('button', { name: '退出', exact: true }).click()
    await second.reload()
    await expect(second.getByRole('button', { name: '退出', exact: true })).toBeVisible()
    const writes = []
    page.on('request', req => { if (req.method() === 'POST' && /\/days\/\d+\/items$/.test(req.url())) writes.push(req) })
    await workspace(page,'行程')
    await page.getByRole('button', { name: '＋ 添加安排', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '添加安排', exact: true })
    await dialog.getByRole('textbox', { name: '名称', exact: true }).fill('禁止用新账号发送的旧页安排')
    await dialog.getByRole('button', { name: '保存安排', exact: true }).click()
    await expect(dialog.getByRole('alert')).toContainText('其他页面已更改登录状态')
    expect(writes).toHaveLength(0)
    await confirm(page, () => dialog.getByRole('button', { name: '关闭', exact: true }).click(), true,
      '关闭将丢弃未保存的安排，确定关闭吗？')
    const sync = page.getByRole('button', { name: '同步账号', exact: true })
    await confirm(page, () => sync.click(), false)
    await workspace(page,'助手')
  await expect(draft).toHaveValue('同步取消后必须保留的草稿')
    await workspace(page,'助手')
    await draft.scrollIntoViewIfNeeded()
    await page.screenshot({ path: testInfo.outputPath('cross-tab-draft-preserved.png') })
    await confirm(page, () => sync.click(), true)
    await expect(notice).toHaveCount(0)
    await expect(page.getByRole('alert').filter({ hasText: '旅行不存在' })).toBeVisible()
    await expect(page.getByRole('heading', { name: '跨标签账号隔离', exact: true })).toHaveCount(0)
    const tripId = new URL(tripUrl).pathname.split('/').at(-1)
    const saved = await request.get(ORIGIN+`/travel/api/trips/${tripId}`, { headers: { Authorization: 'Bearer '+owner.token } })
    expect((await saved.json()).days[0].items).toEqual([])
  } finally { await second.close() }
})

test('其他标签退出：取消同步保留云端草稿，确认后显示登录入口', async ({ page, context, request }) => {
  const owner = await account(request, '跨标签退出账号')
  await login(page, owner)
  await createTrip(page, '跨标签退出保护')
  await workspace(page,'助手')
  const draft = page.getByRole('textbox', { name: '攻略正文', exact: true })
  await draft.fill('其他标签退出时不能直接清空的正文')
  const second = await context.newPage()
  try {
    await localOnly(second)
    await second.goto(HOME)
    await second.getByRole('button', { name: '退出', exact: true }).click()
    const notice = page.getByRole('status').filter({ hasText: '其他页面已更改登录状态' })
    await expect(notice).toBeVisible()
    const sync = page.getByRole('button', { name: '同步账号', exact: true })
    await confirm(page, () => sync.click(), false)
    await workspace(page,'助手')
  await expect(draft).toHaveValue('其他标签退出时不能直接清空的正文')
    await confirm(page, () => sync.click(), true)
    await expect(page.getByText('登录后查看旅行。', { exact: false })).toBeVisible()
    await expect(notice).toHaveCount(0)
    await expect(page.getByRole('button', { name: '退出', exact: true })).toHaveCount(0)
  } finally { await second.close() }
})

test('同步账号遇到503保留旧页草稿且继续阻止请求，重试成功后才切换', async ({ page, context, request }) => {
  const owner = await account(request, '同步故障原账号')
  const other = await account(request, '同步故障新账号')
  await login(page, owner)
  await createTrip(page, '账号核对失败保护')
  await workspace(page,'助手')
  const draft = page.getByRole('textbox', { name: '告诉 AI 你的规划需求', exact: true })
  await draft.fill('核对暂时失败时保留的合成草稿')
  const second = await context.newPage()
  try {
    await localOnly(second); await second.goto(HOME)
    await second.getByRole('button', { name: '退出', exact: true }).click()
    await login(second, other)
    let failing = true
    await page.route('**/travel/api/auth/me', route => {
      if (failing) { failing = false; return route.fulfill({ status: 503, json: { detail: 'Controlled identity check outage' } }) }
      return route.continue()
    })
    const sync = page.getByRole('button', { name: '同步账号', exact: true })
    await confirm(page, () => sync.click(), true)
    await expect(page.getByRole('status').filter({ hasText: '暂时无法确认登录状态' })).toBeVisible()
    await workspace(page,'助手')
  await expect(draft).toHaveValue('核对暂时失败时保留的合成草稿')
    await expect(sync).toBeEnabled()
    await expect(page.getByRole('heading', { name: '账号核对失败保护', exact: true })).toBeVisible()
    await confirm(page, () => sync.click(), true)
    await expect(page.getByRole('status').filter({ hasText: '其他页面已更改登录状态' })).toHaveCount(0)
    await expect(page.getByRole('alert').filter({ hasText: '旅行不存在' })).toBeVisible()
  } finally { await second.close() }
})

test('重载恢复账号遇到503不清除凭证，重试后恢复原云端旅行', async ({ page, request }) => {
  const owner = await account(request, '恢复故障账号')
  await login(page, owner)
  await createTrip(page, '重载账号恢复保护')
  let failing = true
  await page.route('**/travel/api/auth/me', route => {
    if (failing) { failing = false; return route.fulfill({ status: 503, json: { detail: 'Controlled restore outage' } }) }
    return route.continue()
  })
  await page.reload()
  const notice = page.getByRole('status').filter({ hasText: '暂时无法确认登录状态' })
  await expect(notice).toBeVisible()
  await expect(page.getByText('登录后查看旅行。', { exact: false })).toBeVisible()
  await notice.getByRole('button', { name: '重试', exact: true }).click()
  await expect(page.getByRole('heading', { name: '重载账号恢复保护', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '退出', exact: true })).toBeVisible()
  await expect(notice).toHaveCount(0)
})

test('身份核对不可用时可主动登录，登录请求不携带未核对的旧凭证', async ({ page, request }) => {
  const owner = await account(request, '未核对的旧账号')
  const other = await account(request, '主动登录新账号')
  await login(page, owner)
  await createTrip(page, '身份未知时主动登录')
  await page.route('**/travel/api/auth/me', route => route.fulfill({ status: 503, json: { detail: 'Controlled restore outage' } }))
  await page.reload()
  await expect(page.getByRole('status').filter({ hasText: '暂时无法确认登录状态' })).toBeVisible()
  const headers = []
  page.on('request', req => { if (req.url().endsWith('/api/auth/login')) headers.push(req.headers().authorization) })
  await login(page, other)
  await expect(page.getByRole('alert').filter({ hasText: '旅行不存在' })).toBeVisible()
  expect(headers).toEqual([undefined])
})
