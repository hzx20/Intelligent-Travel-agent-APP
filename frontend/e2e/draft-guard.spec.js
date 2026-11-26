import {workspace} from './workspace-helper.js'
import { test, expect } from '@playwright/test'

const TITLE = '草稿保护隔离验收'

// Handle native dialogs while the triggering input is pending. No replacement
// of window.confirm/beforeunload: exercise the browser and the complete app.
async function answer(page, trigger, { accept, message, type = 'confirm' }) {
  const handled = page.waitForEvent('dialog').then(async dialog => {
    const actual = { type: dialog.type(), message: dialog.message() }
    if (accept) await dialog.accept()
    else await dialog.dismiss()
    expect(actual.type).toBe(type)
    if (message) expect(actual.message).toBe(message)
  })
  await Promise.all([handled, trigger()])
}

test.beforeEach(async ({ page }) => {
  // Reject every external request even if a future component adds one.
  await page.route('**/*', async route => {
    if (new URL(route.request().url()).origin === 'http://127.0.0.1:5263') await route.continue()
    else await route.abort('blockedbyclient')
  })
  await page.goto('./')
  await page.getByRole('link', { name: '＋ 创建旅行', exact: true }).click()
  await page.getByRole('textbox', { name: '旅行名称', exact: true }).fill(TITLE)
  await page.getByRole('spinbutton', { name: '旅行天数', exact: true }).fill('1')
  await page.getByRole('button', { name: '创建旅行 →', exact: true }).click()
  await expect(page.getByRole('heading', { name: TITLE, exact: true })).toBeVisible()
})

for (const editor of [
  { open: '＋ 添加安排', dialog: '添加安排', field: '名称', message: '关闭将丢弃未保存的安排，确定关闭吗？', initial: '' },
  { open: '编辑主题', dialog: '编辑当天主题', field: '每日主题', message: '关闭将丢弃未保存的主题，确定关闭吗？', initial: '' },
  { open: '编辑旅行信息', dialog: '编辑旅行信息', field: '旅行名称', message: '关闭将丢弃未保存的旅行信息，确定关闭吗？', initial: TITLE },
]) {
  test(`${editor.dialog}：Esc 取消保留草稿，确认后丢弃并恢复焦点`, async ({ page }) => {
    const opener = page.getByRole('button', { name: editor.open, exact: true })
    await opener.click()
    const dialog = page.getByRole('dialog', { name: editor.dialog, exact: true })
    const field = dialog.getByRole('textbox', { name: editor.field, exact: true })
    await field.fill('尚未保存的合成内容')
    await answer(page, () => field.press('Escape'), { accept: false, message: editor.message })
    await expect(dialog).toBeVisible()
    await expect(field).toHaveValue('尚未保存的合成内容')
    await answer(page, () => field.press('Escape'), { accept: true, message: editor.message })
    await expect(dialog).toHaveCount(0)
    await expect(opener).toBeFocused()
    await opener.click()
    await expect(field).toHaveValue(editor.initial)
    await dialog.getByRole('button', { name: '关闭', exact: true }).click()
    await expect(dialog).toHaveCount(0)
  })
}

test('子组件草稿：取消路由离开保留输入，确认后才离开', async ({ page }, testInfo) => {
  await workspace(page,'助手')
  const original = page.url()
  const draft = page.getByRole('textbox', { name: '告诉 AI 你的规划需求', exact: true })
  await draft.fill('离开前请保留这段合成需求')
  const leave = page.getByRole('link', { name: '← 我的旅行', exact: true })
  const message = '还有未保存的输入、仅在本页显示的分享地址或正在进行的操作。离开后内容可能丢失，确定离开吗？'
  await answer(page, () => leave.click(), { accept: false, message })
  await expect(page).toHaveURL(original)
  await expect(draft).toHaveValue('离开前请保留这段合成需求')
  await expect(page.getByRole('button', { name: '导出行程文本', exact: true })).toBeDisabled()
  await draft.scrollIntoViewIfNeeded()
  await page.screenshot({ path: testInfo.outputPath('cancelled-navigation-draft.png') })
  await answer(page, () => leave.click(), { accept: true, message })
  await expect(page).toHaveURL('http://127.0.0.1:5263/travel/')
  await page.getByRole('link').filter({ has: page.getByRole('heading', { name: TITLE, exact: true }) }).click()
  await workspace(page,'助手')
  await expect(draft).toHaveValue('')
})

test('刷新：取消保留未保存正文，确认刷新后仅保留已保存旅行', async ({ page }) => {
  await workspace(page,'助手')
  const draft = page.getByRole('textbox', { name: '攻略正文', exact: true })
  await draft.fill('没有发送给模型的合成攻略正文')
  // A dismissed reload never navigates, so don't use page.reload's navigation
  // waiter here. Invoke the browser's real reload and verify the retained DOM.
  await answer(page, () => page.evaluate(() => window.location.reload()), { accept: false, type: 'beforeunload' })
  await expect(draft).toHaveValue('没有发送给模型的合成攻略正文')
  await answer(page, () => page.reload(), { accept: true, type: 'beforeunload' })
  await expect(page.getByRole('heading', { name: TITLE, exact: true })).toBeVisible()
  await workspace(page,'助手')
  await expect(draft).toHaveValue('')
})

test('保存安排后直接重载仍保留内容且没有未保存提示', async ({ page }) => {
  await page.getByRole('button', { name: '＋ 添加安排', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '添加安排', exact: true })
  await dialog.getByRole('textbox', { name: '名称', exact: true }).fill('已保存的合成安排')
  await dialog.getByRole('button', { name: '保存安排', exact: true }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByRole('heading', { name: '已保存的合成安排', exact: true })).toBeVisible()
  const dialogs = []
  page.on('dialog', dialog => { dialogs.push(dialog.type()); return dialog.dismiss() })
  await page.reload()
  await expect(page.getByRole('heading', { name: '已保存的合成安排', exact: true })).toBeVisible()
  expect(dialogs).toEqual([])
})

test('关闭标签页：取消保留草稿，确认后关闭隔离测试标签', async ({ page }) => {
  await workspace(page,'收集')
  const draft = page.getByRole('textbox', { name: '手动输入想去的地点', exact: true })
  await draft.fill('未加入行程的合成地点')
  await answer(page, () => page.close({ runBeforeUnload: true }), { accept: false, type: 'beforeunload' })
  expect(page.isClosed()).toBe(false)
  await expect(draft).toHaveValue('未加入行程的合成地点')
  const closed = page.waitForEvent('close')
  await answer(page, () => page.close({ runBeforeUnload: true }), { accept: true, type: 'beforeunload' })
  await closed
  expect(page.isClosed()).toBe(true)
})
