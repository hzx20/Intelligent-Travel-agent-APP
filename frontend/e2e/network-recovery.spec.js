import {workspace} from './workspace-helper.js'
import { randomUUID } from 'node:crypto'
import { test, expect } from '@playwright/test'

const ORIGIN = 'http://127.0.0.1:5264', HOME = ORIGIN+'/travel/'
const PASSWORD = 'isolated-network-test-password'
async function setup(page, request) {
  await page.route('**/*', route => new URL(route.request().url()).origin === ORIGIN ? route.continue() : route.abort())
  const username = 'net_'+randomUUID().replaceAll('-', '').slice(0, 16)
  expect((await request.post(ORIGIN+'/travel/api/auth/register', {data:{username,password:PASSWORD}})).status()).toBe(201)
  const signed = await request.post(ORIGIN+'/travel/api/auth/login', {data:{username,password:PASSWORD}})
  expect(signed.status()).toBe(200)
  const auth = { Authorization: 'Bearer '+(await signed.json()).token }
  await page.goto(HOME)
  await page.getByRole('navigation').first().getByRole('button', {name:'登录',exact:true}).click()
  const login = page.getByRole('dialog', {name:'登录',exact:true})
  await login.getByRole('textbox', {name:'用户名',exact:true}).fill(username)
  await login.getByLabel('密码',{exact:true}).fill(PASSWORD)
  await login.getByRole('button',{name:'登录',exact:true}).last().click()
  await expect(login).toHaveCount(0)
  await page.getByRole('link',{name:'＋ 创建旅行',exact:true}).click()
  await page.getByRole('textbox',{name:'旅行名称',exact:true}).fill('网络恢复隔离验收')
  await page.getByRole('spinbutton',{name:'旅行天数',exact:true}).fill('1')
  await page.getByRole('button',{name:'创建旅行 →',exact:true}).click()
  await expect(page.getByRole('heading',{name:'网络恢复隔离验收',exact:true})).toBeVisible()
  const id = new URL(page.url()).pathname.split('/').at(-1)
  return { auth, path: ORIGIN+`/travel/api/trips/${id}` }
}
async function itemForm(page, title) {
  await page.getByRole('button',{name:'＋ 添加安排',exact:true}).click()
  const dialog = page.getByRole('dialog',{name:'添加安排',exact:true})
  await dialog.getByRole('textbox',{name:'名称',exact:true}).fill(title)
  return dialog
}

test('浏览器离线保存失败保留安排，恢复网络后手动重试只保存一次', async ({page,context,request}) => {
  const trip = await setup(page, request)
  const dialog = await itemForm(page, '离线后恢复的安排')
  try {
    await context.setOffline(true)
    await dialog.getByRole('button',{name:'保存安排',exact:true}).click()
    await expect(dialog.getByRole('alert')).toContainText('结果暂时无法确认')
    await expect(dialog.getByRole('textbox',{name:'名称',exact:true})).toHaveValue('离线后恢复的安排')
    await expect(dialog.getByRole('button',{name:'保存安排',exact:true})).toBeEnabled()
    const before = await request.get(trip.path,{headers:trip.auth})
    expect((await before.json()).days[0].items).toEqual([])
    await context.setOffline(false)
    await dialog.getByRole('button',{name:'保存安排',exact:true}).click()
    await expect(dialog).toHaveCount(0)
    await expect(page.getByRole('heading',{name:'离线后恢复的安排',exact:true})).toBeVisible()
    const after = await request.get(trip.path,{headers:trip.auth})
    expect((await after.json()).days[0].items).toHaveLength(1)
  } finally { await context.setOffline(false) }
})

for (const kind of ['reservations', 'expenses']) {
  test(`${kind}写入成功但响应丢失：核对保留表单，旧版本重试不会重复新增`, async ({page,request}) => {
    const trip = await setup(page, request), versions = []
    let first = true
    await page.route(`**/travel/api/trips/*/${kind}`, async route => {
      if (route.request().method() !== 'POST') return route.continue()
      versions.push(route.request().postDataJSON().version)
      if (!first) return route.continue()
      first = false
      const saved = await route.fetch()
      expect(saved.status()).toBe(201)
      await route.abort('failed')
    })
    await workspace(page,'记录')
    const section = page.locator(`section.${kind}`)
    const field = kind === 'reservations' ? section.getByRole('textbox',{name:'名称',exact:true})
      : section.getByRole('textbox',{name:'金额',exact:true})
    const value = kind === 'reservations' ? '响应丢失的合成酒店' : '12.34'
    const save = section.getByRole('button',{name:kind === 'reservations' ? '保存记录' : '记一笔',exact:true})
    await field.fill(value)
    await save.click()
    await expect(section.getByRole('alert')).toContainText('结果暂时无法确认')
    await expect(field).toHaveValue(value)
    expect(versions).toHaveLength(1)
    const before = await request.get(trip.path+`/${kind}`,{headers:trip.auth})
    expect((await before.json()).items).toHaveLength(1)
    await section.getByRole('button',{name:kind === 'reservations' ? '核对已保存记录' : '核对已保存账本',exact:true}).click()
    await expect(section.getByRole('status')).toContainText('已重新读取')
    await expect(field).toHaveValue(value)
    await save.click()
    await expect(section.getByRole('alert')).toContainText('避免重复')
    expect(versions).toHaveLength(2)
    expect(versions[1]).toBe(versions[0])
    const after = await request.get(trip.path+`/${kind}`,{headers:trip.auth})
    expect((await after.json()).items).toHaveLength(1)
    await section.getByRole('button',{name:'清空表单',exact:true}).click()
    await expect(field).toHaveValue('')
  })
}

for (const delivered of [true, false]) {
  test(`照片回执丢失时${delivered ? '核对已保存结果' : '核对后重试原文件'}，后台只保存一张`, async ({ page, request }) => {
    const trip = await setup(page, request)
    const title = '足迹照片恢复测试地点'
    const dialog = await itemForm(page, title)
    await dialog.getByRole('button', { name: '保存安排', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await workspace(page, '记录')
    const section = page.locator('section.footprints')
    await section.getByRole('combobox', { name: '当天安排', exact: true }).selectOption({ label: title })
    await section.getByRole('button', { name: '记录到访', exact: true }).click()
    const input = section.locator('input[type=file]')
    await expect(input).toBeEnabled()

    const versions = []
    await page.route('**/travel/api/trips/*/footprints/*/photos', async route => {
      versions.push(route.request().headers()['x-trip-version'])
      if (versions.length > 1) return route.continue()
      if (delivered) expect((await route.fetch()).status()).toBe(201)
      await route.abort('failed')
    })
    const imageData = await page.evaluate(() => {
      const canvas = document.createElement('canvas')
      canvas.width = canvas.height = 24
      const ctx = canvas.getContext('2d')
      ctx.fillStyle = '#507968'
      ctx.fillRect(0, 0, 24, 24)
      return canvas.toDataURL('image/png').split(',')[1]
    })
    await input.setInputFiles({ name: 'synthetic-footprint.png', mimeType: 'image/png', buffer: Buffer.from(imageData, 'base64') })
    await expect(section.getByRole('alert')).toContainText('结果暂时无法确认')
    await expect(input).toBeDisabled()
    expect(versions).toHaveLength(1)
    const saved = async () => (await (await request.get(trip.path + '/footprints', { headers: trip.auth })).json()).items[0]
    expect((await saved()).photo_ids).toHaveLength(delivered ? 1 : 0)

    const retry = section.getByRole('button', { name: '重试本次照片', exact: true })
    await expect(retry).toHaveCount(0)
    await section.getByRole('button', { name: '核对已保存照片', exact: true }).click()
    const photo = section.getByRole('img', { name: '足迹照片', exact: true })
    if (delivered) {
      await expect(section.locator('.photo-review').getByRole('status')).toContainText('已重新读取照片')
      await expect(photo).toBeVisible()
      await expect(retry).toHaveCount(0)
      await section.getByRole('button', { name: '结束本次核对', exact: true }).click()
      expect(versions).toHaveLength(1)
    } else {
      await expect(retry).toBeEnabled()
      await retry.click()
      await expect(photo).toBeVisible()
      expect(versions).toHaveLength(2)
      expect(versions[1]).toBe(versions[0])
    }
    await expect(input).toBeEnabled()
    expect((await saved()).photo_ids).toHaveLength(1)
  })
}

test('保存请求停滞达到实际超时后恢复表单，未写入时可以手动重试', async ({page,request}) => {
  const trip = await setup(page, request)
  let release, first = true, attempts = 0
  const gate = new Promise(resolve => { release = resolve })
  await page.route('**/travel/api/trips/*/days/*/items', async route => {
    if (route.request().method() !== 'POST') return route.continue()
    attempts++
    if (!first) return route.continue()
    first = false
    await gate
    // The client has already aborted at its real 15-second limit.
    await route.abort('timedout').catch(() => {})
  })
  try {
    const dialog = await itemForm(page, '超时后手动恢复')
    await dialog.getByRole('button',{name:'保存安排',exact:true}).click()
    await expect(dialog.getByRole('alert')).toContainText('请求超时', {timeout:20000})
    await expect(dialog.getByRole('textbox',{name:'名称',exact:true})).toHaveValue('超时后手动恢复')
    await expect(dialog.getByRole('button',{name:'保存安排',exact:true})).toBeEnabled()
    expect(attempts).toBe(1)
    const before = await request.get(trip.path,{headers:trip.auth})
    expect((await before.json()).days[0].items).toEqual([])
    release()
    await dialog.getByRole('button',{name:'保存安排',exact:true}).click()
    await expect(dialog).toHaveCount(0)
    expect(attempts).toBe(2)
    const after = await request.get(trip.path,{headers:trip.auth})
    expect((await after.json()).days[0].items).toHaveLength(1)
  } finally { release() }
})

test('预订列表暂时503时保留独立表单，列表重试后恢复原记录', async ({page,request}) => {
  const trip = await setup(page, request)
  const saved = await request.post(trip.path+'/reservations',{headers:trip.auth,
    data:{version:1,title:'已经保存的合成记录'}})
  expect(saved.status()).toBe(201)
  let first = true
  await page.route('**/travel/api/trips/*/reservations', route => {
    if (route.request().method() === 'GET' && first) {
      first = false
      return route.fulfill({status:503,json:{detail:'Controlled list outage'}})
    }
    return route.continue()
  })
  await page.reload()
  await workspace(page,'记录')
  const section = page.locator('section.reservations')
  await expect(section.getByRole('alert')).toContainText('记录加载失败')
  await section.getByRole('textbox',{name:'名称',exact:true}).fill('不能被列表重试清空的输入')
  await section.getByRole('alert').getByRole('button',{name:'重试',exact:true}).click()
  await expect(section.getByText('已经保存的合成记录',{exact:true})).toBeVisible()
  await expect(section.getByRole('textbox',{name:'名称',exact:true})).toHaveValue('不能被列表重试清空的输入')
})
