import {randomUUID} from 'node:crypto'
import {fileURLToPath} from 'node:url'
import {test,expect} from '@playwright/test'
import {workspace} from './workspace-helper.js'
const ORIGIN='http://127.0.0.1:5264',HOME=ORIGIN+'/travel/',PASSWORD='isolated-import-password'
const SAMPLE=fileURLToPath(new URL('../qa/photo-upload/sample.png',import.meta.url))
const guide=p=>p.locator('#guide-import')
async function setup(page,request,cloud=true){
 await request.post(HOME+'api/qa-guide-import/reset-limits')
 await page.route('**/*',r=>new URL(r.request().url()).origin===ORIGIN?r.fallback():r.abort());await page.goto(HOME)
 let auth
 if(cloud){const username='imp_'+randomUUID().replaceAll('-','').slice(0,16);await request.post(HOME+'api/auth/register',{data:{username,password:PASSWORD}});const u=await(await request.post(HOME+'api/auth/login',{data:{username,password:PASSWORD}})).json();auth={Authorization:'Bearer '+u.token};await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click();const d=page.getByRole('dialog',{name:'登录',exact:true});await d.getByRole('textbox',{name:'用户名',exact:true}).fill(username);await d.getByLabel('密码',{exact:true}).fill(PASSWORD);await d.getByRole('button',{name:'登录',exact:true}).last().click();await expect(d).toHaveCount(0)}
 await page.getByRole('link',{name:'＋ 创建旅行',exact:true}).click();await page.getByRole('textbox',{name:'旅行名称',exact:true}).fill('合成攻略导入旅行');await page.getByRole('button',{name:'创建旅行 →',exact:true}).click();await expect(page.getByRole('heading',{name:'合成攻略导入旅行',exact:true})).toBeVisible();const id=new URL(page.url()).pathname.split('/').at(-1);await workspace(page,'助手');return {id,auth}
}
async function read(page,request,u){if(u.auth)return (await request.get(HOME+'api/trips/'+u.id,{headers:u.auth})).json();return page.evaluate(async id=>{const {guestTrips}=await import('/travel/src/store/guestTrips.js');return guestTrips.get(id)},u.id)}
async function choose(page){await guide(page).locator('input[type=file]').setInputFiles(SAMPLE);await expect(guide(page).getByText(/已选择：sample.png/)).toBeVisible()}
async function preview(page){await guide(page).getByRole('textbox',{name:'攻略正文',exact:true}).fill('合成湖边公园适合散步。合成街区适合闲逛。');await guide(page).getByRole('button',{name:'识别攻略地点',exact:true}).click();await expect(guide(page).getByRole('heading',{name:'确认识别结果',exact:true})).toBeVisible();await guide(page).getByRole('button',{name:'全选',exact:true}).click()}
async function confirm(page,action,accept){const done=page.waitForEvent('dialog').then(async d=>{expect(d.type()).toBe('confirm');if(accept)await d.accept();else await d.dismiss()});await Promise.all([done,action()])}
async function capture(page,info,name,target){await target.evaluate(el=>el.scrollIntoView({block:'center'}));await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:info.outputPath(name)})}

for (const cloud of [true, false]) {
  test((cloud ? '账号' : '本机') + '维基导游链接预览与勾选导入', async ({ page, request }) => {
    const user = await setup(page, request, cloud)
    // The account case reads directly in the browser, and the guest case
    // exercises the server fallback when the browser cannot reach the source.
    const revision = cloud ? 124 : 123
    const revisionUrl = 'https://zh.wikivoyage.org/w/index.php?oldid=' + revision
    let outgoing = 0
    if (cloud) {
      await page.route('https://zh.wikivoyage.org/w/api.php?**', route => {
        const remote = route.request()
        const headers = {
          'access-control-allow-origin': '*', 'access-control-allow-headers': 'api-user-agent',
          'access-control-allow-methods': 'GET, OPTIONS',
        }
        if (remote.method() === 'OPTIONS') return route.fulfill({ status: 204, headers })
        outgoing++
        const params = new URL(remote.url()).searchParams
        expect(params.get('origin')).toBe('*')
        expect(params.get('page')).toBe('杭州')
        expect(remote.headers().authorization).toBeUndefined()
        expect(remote.headers().cookie).toBeUndefined()
        return route.fulfill({ headers, json: { parse: { title: '杭州', revid: revision,
          text: '<p>介绍文字</p><span class="listing-name">合成湖边公园</span>'
            + '<span class="listing-name">合成街区</span>' } } })
      })
    }
    const panel = guide(page)
    const savedText = panel.getByRole('textbox', { name: '攻略正文', exact: true })
    await savedText.fill('尚未提交的手工正文')
    await panel.getByText('从维基导游链接收集', { exact: true }).click()
    const field = panel.getByRole('textbox', { name: '维基导游链接', exact: true })
    const button = panel.getByRole('button', { name: '预览链接地点', exact: true })
    await field.fill('https://example.test/wiki/杭州')
    await button.click()
    await expect(panel.getByRole('alert')).toContainText('中文维基导游')
    await expect(savedText).toHaveValue('尚未提交的手工正文')

    await field.fill('https://zh.wikivoyage.org/wiki/杭州')
    await button.click()
    await expect(panel.getByRole('link', { name: '杭州原文及贡献记录' }))
      .toHaveAttribute('href', revisionUrl)
    await expect(panel.getByRole('link', { name: 'CC BY-SA 4.0' }))
      .toHaveAttribute('href', 'https://creativecommons.org/licenses/by-sa/4.0/')
    expect((await read(page, request, user)).places).toHaveLength(0)
    await expect(savedText).toHaveValue('尚未提交的手工正文')
    await panel.getByRole('checkbox', { name: /合成湖边公园/ }).check()
    await panel.getByRole('button', { name: '加入待安排（1）', exact: true }).click()
    await expect(panel.getByRole('status').filter({ hasText: '已新增 1' })).toBeVisible()
    const places = (await read(page, request, user)).places
    expect(places).toHaveLength(1)
    expect(places[0].name).toBe('合成湖边公园')
    expect(places[0].source).toBe('维基导游 · CC BY-SA 4.0 · ' + revisionUrl)
    expect(outgoing).toBe(cloud ? 1 : 0)
  })
}

for(const cloud of [true,false])test((cloud?'账号':'本机')+'选图到核对正文及明确导入，选择/识别不写旅行',async({page,request},info)=>{
 const u=await setup(page,request,cloud);let ocr=0;page.on('request',r=>{if(r.url().includes('image-text'))ocr++});await choose(page);expect(ocr).toBe(0);await guide(page).getByRole('button',{name:'识别图片文字',exact:true}).click();await expect(guide(page).getByRole('textbox',{name:'核对识别文字',exact:true})).toHaveValue(/合成湖边公园/);expect((await read(page,request,u)).places).toHaveLength(0)
 await guide(page).getByRole('textbox',{name:'核对识别文字',exact:true}).fill('合成湖边公园适合散步。合成街区适合闲逛。');await capture(page,info,'ocr-review.png',guide(page).getByRole('button',{name:'加入攻略正文',exact:true}));await guide(page).getByRole('button',{name:'加入攻略正文',exact:true}).click();await expect(guide(page).getByRole('textbox',{name:'来源备注',exact:true})).toHaveValue('图片识别文字（用户核对）');expect((await read(page,request,u)).places).toHaveLength(0);await preview(page);expect((await read(page,request,u)).places).toHaveLength(0);await guide(page).getByRole('button',{name:'加入待安排（2）',exact:true}).click();await expect(guide(page).getByRole('status').filter({hasText:'已新增 2'})).toBeVisible();expect((await read(page,request,u)).places).toHaveLength(2)
})
test('重识别失败与取消换图保留已核对文字，清空后可重新选择同图',async({page,request},info)=>{
 await setup(page,request);await choose(page);await guide(page).getByRole('button',{name:'识别图片文字',exact:true}).click();const draft=guide(page).getByRole('textbox',{name:'核对识别文字',exact:true});await expect(draft).toBeVisible();await draft.fill('人工核对后保留的文字');await confirm(page,()=>guide(page).locator('input[type=file]').setInputFiles(SAMPLE),false);await expect(draft).toHaveValue('人工核对后保留的文字')
 await page.route('**/guide-import/image-text',r=>r.fulfill({status:502,json:{detail:'隔离OCR暂不可用'}}));await confirm(page,()=>guide(page).getByRole('button',{name:'识别图片文字',exact:true}).click(),true);await expect(guide(page).getByRole('alert')).toContainText('隔离OCR');await expect(draft).toHaveValue('人工核对后保留的文字');await capture(page,info,'ocr-preserved.png',draft);await guide(page).getByRole('button',{name:'清空图片与识别文字',exact:true}).click();await expect(draft).toHaveCount(0);await choose(page)
})
test('导入已提交丢响应，只读核对不重复加入或改用新版本',async({page,request},info)=>{
 const u=await setup(page,request);await preview(page);let writes=0;await page.route('**/guide-import/apply',async r=>{writes++;await r.fetch();await r.abort('failed')});await guide(page).getByRole('button',{name:'加入待安排（2）',exact:true}).click();const check=guide(page).getByRole('region',{name:'核对本次攻略导入'});await expect(check).toBeVisible();await check.getByRole('button',{name:'核对待安排清单',exact:true}).click();await expect(check.getByRole('status')).toContainText('行程已有更新');expect((await read(page,request,u)).places).toHaveLength(2);expect(writes).toBe(1);await expect(check.getByRole('button',{name:'继续原次加入',exact:true})).toHaveCount(0);await capture(page,info,'import-checked.png',check);await confirm(page,()=>check.getByRole('button',{name:'结束本次核对',exact:true}).click(),true);await expect(guide(page).getByRole('textbox',{name:'攻略正文',exact:true})).toHaveValue('合成湖边公园适合散步。合成街区适合闲逛。')
})
test('导入未送达，核对后原内容继续；损坏OCR回执不覆盖正文',async({page,request})=>{
 const u=await setup(page,request);await choose(page);await page.route('**/guide-import/image-text',r=>r.fulfill({json:{version:1,text:{bad:true}}}));await guide(page).getByRole('button',{name:'识别图片文字',exact:true}).click();await expect(guide(page).getByRole('alert')).toContainText('回执无法确认');await preview(page);const bodies=[];await page.route('**/guide-import/apply',async r=>{bodies.push(r.request().postDataJSON());if(bodies.length===1)return r.abort('failed');await r.continue()});await guide(page).getByRole('button',{name:'加入待安排（2）',exact:true}).click();await guide(page).getByRole('button',{name:'核对待安排清单',exact:true}).click();await guide(page).getByRole('button',{name:'继续原次加入',exact:true}).click();await expect(guide(page).getByRole('status').filter({hasText:'已新增 2'})).toBeVisible();expect(bodies[1]).toEqual(bodies[0]);expect((await read(page,request,u)).places).toHaveLength(2)
})
