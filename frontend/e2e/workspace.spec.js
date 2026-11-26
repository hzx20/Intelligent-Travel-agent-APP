import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'
import {workspace} from './workspace-helper.js'
const ORIGIN='http://127.0.0.1:5264', HOME=ORIGIN+'/travel/', PASSWORD='isolated-workspace-password'
const REFRESH='刷新会关闭编辑窗口并清除未应用的建议、输入和仅在本页显示的分享地址，确定继续？'
async function setup(page,request,cloud=true){
  await page.route('**/*',r=>new URL(r.request().url()).origin===ORIGIN?r.fallback():r.abort())
  await page.goto(HOME)
  let auth
  if(cloud){
    const username='work_'+randomUUID().replaceAll('-','').slice(0,15)
    expect((await request.post(HOME+'api/auth/register',{data:{username,password:PASSWORD}})).status()).toBe(201)
    const result=await(await request.post(HOME+'api/auth/login',{data:{username,password:PASSWORD}})).json()
    auth={Authorization:'Bearer '+result.token}
    await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click()
    const d=page.getByRole('dialog',{name:'登录',exact:true})
    await d.getByRole('textbox',{name:'用户名',exact:true}).fill(username);await d.getByLabel('密码',{exact:true}).fill(PASSWORD)
    await d.getByRole('button',{name:'登录',exact:true}).last().click();await expect(d).toHaveCount(0)
  }
  await page.getByRole('link',{name:'＋ 创建旅行',exact:true}).click()
  await page.getByRole('textbox',{name:'旅行名称',exact:true}).fill('合成两日旅行')
  await page.getByRole('spinbutton',{name:'旅行天数',exact:true}).fill('2')
  await page.getByRole('button',{name:'创建旅行 →',exact:true}).click()
  await expect(page.getByRole('heading',{name:'合成两日旅行',exact:true})).toBeVisible()
  return {id:new URL(page.url()).pathname.split('/').at(-1),auth}
}
async function read(request,u){return (await request.get(HOME+'api/trips/'+u.id,{headers:u.auth})).json()}
async function add(page,name='合成午后散步'){
  await workspace(page,'行程');await page.getByRole('button',{name:'＋ 添加安排',exact:true}).click()
  const d=page.getByRole('dialog',{name:'添加安排',exact:true});await d.getByRole('textbox',{name:'名称',exact:true}).fill(name)
  await d.getByRole('button',{name:'保存安排',exact:true}).click();await expect(d).toHaveCount(0)
}
async function confirm(page,action){const done=page.waitForEvent('dialog').then(async d=>{expect(d.message()).toBe(REFRESH);await d.accept()});await Promise.all([done,action()])}
async function capture(page,info,name,target){if(target)await target.evaluate(el=>el.scrollIntoView({block:'center'}));await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:info.outputPath(name)})}

test('本机两日旅行从收集到日程和预订，刷新恢复工作区',async({page,request},info)=>{
  await setup(page,request,false);await expect(page.getByRole('navigation',{name:'旅行工作区'}).getByRole('button')).toHaveCount(5)
  await page.getByRole('button',{name:'DAY 2 第 2 天',exact:true}).click();await workspace(page,'收集')
  await page.getByRole('textbox',{name:'手动输入想去的地点',exact:true}).fill('合成本机湖边')
  await page.getByRole('button',{name:'加入',exact:true}).click();await page.getByRole('button',{name:'安排到当天',exact:true}).click()
  await workspace(page,'行程');await expect(page.getByRole('heading',{name:'合成本机湖边',exact:true})).toBeVisible()
  await workspace(page,'记录');const r=page.locator('section.reservations')
  await r.getByRole('textbox',{name:'名称',exact:true}).fill('合成酒店');await r.getByRole('textbox',{name:'订单号（可留空）',exact:true}).fill('QA-WORK-LOCAL')
  await r.getByRole('button',{name:'保存记录',exact:true}).click();await expect(r.getByText('订单号：QA-WORK-LOCAL',{exact:true})).toBeVisible()
  await page.reload();await expect(page.getByRole('navigation',{name:'旅行工作区'}).getByRole('button',{name:/^记录/})).toHaveAttribute('aria-pressed','true')
  await expect(r.getByText('订单号：QA-WORK-LOCAL',{exact:true})).toBeVisible();await workspace(page,'行程')
  await expect(page.getByRole('button',{name:'DAY 2 第 2 天',exact:true})).toHaveAttribute('aria-pressed','true');await capture(page,info,'guest-itinerary.png')
})

test('景点库加入后返回收集区，明确安排到当天',async({page,request},info)=>{
  const u=await setup(page,request);await workspace(page,'收集');await page.getByRole('link',{name:'从景点库收集 →',exact:true}).click()
  await page.getByRole('link').filter({hasText:'合成湖边步道'}).first().click()
  await page.getByRole('button',{name:'＋ 加入旅行',exact:true}).click();const d=page.getByRole('dialog',{name:'加入哪段旅行？',exact:true})
  await d.getByRole('combobox',{name:/选择旅行/}).selectOption('cloud:'+u.id)
  await d.getByRole('button',{name:'加入待安排',exact:true}).click()
  await page.getByRole('link',{name:'查看旅行 →',exact:true}).click();await expect(page).toHaveURL(/panel=places/)
  expect((await read(request,u)).days[0].items).toHaveLength(0)
  await page.getByRole('button',{name:'安排到当天',exact:true}).click();await workspace(page,'行程')
  await expect(page.getByRole('heading',{name:'合成湖边步道',exact:true})).toBeVisible();expect((await read(request,u)).days[0].items).toHaveLength(1)
  await capture(page,info,'collected-itinerary.png',page.getByRole('heading',{name:'合成湖边步道',exact:true}))
})

test('助手任务跨区运行，预览确认前不写入，保留其他区输入',async({page,request},info)=>{
  const u=await setup(page,request);await workspace(page,'助手')
  await page.getByRole('textbox',{name:'告诉 AI 你的规划需求',exact:true}).fill('等待后给我一条散步安排')
  await page.getByRole('button',{name:'生成修改建议',exact:true}).click();await workspace(page,'收集')
  const draft=page.getByRole('textbox',{name:'手动输入想去的地点',exact:true});await draft.fill('尚未加入的合成地点')
  await workspace(page,'助手');await expect(page.getByRole('heading',{name:'修改预览',exact:true})).toBeVisible({timeout:15000})
  expect((await read(request,u)).days[0].items).toHaveLength(0);await capture(page,info,'assistant-preview.png',page.getByRole('button',{name:'确认应用这些修改',exact:true}))
  await page.getByRole('button',{name:'确认应用这些修改',exact:true}).click()
  await expect.poll(async()=>(await read(request,u)).days[0].items.length).toBe(1)
  await workspace(page,'收集');await expect(draft).toHaveValue('尚未加入的合成地点')
})

test('记录区保留跨区草稿，足迹未处理阻止切日，清空后放行',async({page,request},info)=>{
  await setup(page,request);await add(page);await workspace(page,'记录')
  const r=page.locator('section.reservations'),f=page.locator('section.footprints')
  await r.getByRole('textbox',{name:'名称',exact:true}).fill('尚未保存的酒店')
  await f.getByRole('combobox',{name:'当天安排',exact:true}).selectOption({label:'合成午后散步'})
  await f.getByRole('textbox',{name:'备注',exact:true}).fill('尚未提交的足迹')
  await workspace(page,'助手');const ai=page.getByRole('textbox',{name:'告诉 AI 你的规划需求',exact:true});await ai.fill('尚未发送的需求')
  await workspace(page,'行程');await page.getByRole('button',{name:'DAY 2 第 2 天',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('请先保存、取消或清空足迹表单');await expect(f.getByRole('textbox',{name:'备注',exact:true})).toHaveValue('尚未提交的足迹')
  await capture(page,info,'records-day-guard.png',f.getByRole('button',{name:'清空到访表单',exact:true}));await f.getByRole('button',{name:'清空到访表单',exact:true}).click()
  await workspace(page,'行程');await page.getByRole('button',{name:'DAY 2 第 2 天',exact:true}).click();await expect(page.getByRole('alert')).toHaveCount(0)
  await workspace(page,'记录');await expect(r.getByRole('textbox',{name:'名称',exact:true})).toHaveValue('尚未保存的酒店')
  await workspace(page,'助手');await expect(ai).toHaveValue('尚未发送的需求')
})

test('刷新503及错误归属200保留已加载数据和草稿，有效刷新才清空',async({page,request},info)=>{
  const u=await setup(page,request);await workspace(page,'助手');const draft=page.getByRole('textbox',{name:'告诉 AI 你的规划需求',exact:true});await draft.fill('刷新失败应保留')
  const url='**/api/trips/'+u.id
  await page.route(url,r=>r.fulfill({status:503,json:{detail:'隔离刷新故障'}}));await confirm(page,()=>page.getByRole('button',{name:'刷新旅行',exact:true}).click())
  await expect(page.getByRole('alert')).toContainText('隔离刷新故障');await expect(draft).toHaveValue('刷新失败应保留');await expect(page.getByRole('heading',{name:'合成两日旅行',exact:true})).toBeVisible()
  await page.unroute(url);await page.route(url,async r=>{const res=await r.fetch();await r.fulfill({json:{...await res.json(),id:999999}})})
  await confirm(page,()=>page.getByRole('button',{name:'刷新旅行',exact:true}).click());await expect(draft).toHaveValue('刷新失败应保留');await capture(page,info,'refresh-retained.png')
  await page.unroute(url);await confirm(page,()=>page.getByRole('button',{name:'刷新旅行',exact:true}).click());await expect(draft).toHaveValue('');await expect(page.getByRole('alert')).toHaveCount(0)
})

test('记录实际保存预订账本足迹，同行独立可达',async({page,request},info)=>{
  const u=await setup(page,request);await add(page);await workspace(page,'记录')
  const r=page.locator('section.reservations'),e=page.locator('section.expenses'),f=page.locator('section.footprints')
  await r.getByRole('textbox',{name:'名称',exact:true}).fill('合成酒店');await r.getByRole('button',{name:'保存记录',exact:true}).click();await expect(r.getByText('合成酒店',{exact:true})).toBeVisible()
  await e.getByLabel('金额',{exact:true}).fill('88.50');await e.getByRole('combobox',{name:'分类',exact:true}).selectOption('餐饮');await e.getByRole('button',{name:'记一笔',exact:true}).click();await expect(e.getByText('餐饮 · CNY 88.50',{exact:true})).toBeVisible()
  await f.getByRole('combobox',{name:'当天安排',exact:true}).selectOption({label:'合成午后散步'});await f.getByRole('button',{name:'记录到访',exact:true}).click();await expect(f.locator('.record-heading').getByText('合成午后散步',{exact:true})).toBeVisible()
  await workspace(page,'同行');await expect(page.getByRole('button',{name:'生成只读链接',exact:true})).toBeVisible();await capture(page,info,'sharing-workspace.png',page.getByRole('button',{name:'生成只读链接',exact:true}))
  expect((await read(request,u)).days[0].items).toHaveLength(1)
})

test('工作区与日期链接刷新恢复，攻略深链接默认打开助手',async({page,request})=>{
  const u=await setup(page,request);await page.goto(HOME+'trips/'+u.id+'?panel=records&day=2')
  await expect(page.getByRole('heading',{name:'旅行记录',exact:true})).toBeVisible();await page.reload();await workspace(page,'行程')
  await expect(page.getByRole('button',{name:'DAY 2 第 2 天',exact:true})).toHaveAttribute('aria-pressed','true')
  await page.goto(HOME+'trips/'+u.id+'?guide=1');await expect(page.getByRole('textbox',{name:'告诉 AI 你的规划需求',exact:true})).toBeVisible()
  await expect(page.getByRole('textbox',{name:'攻略正文',exact:true})).toBeVisible();expect((await read(request,u)).days[0].items).toHaveLength(0)
})

test('地图降级可读可重试，窄屏菜单键盘及工作区无横向溢出',async({page,request},info)=>{
  let configs=0;page.on('request',r=>{if(r.url().includes('/api/map/config'))configs++})
  await setup(page,request)
  if(info.project.name==='mobile')expect(configs).toBe(0)
  await workspace(page,'地图');await expect(page.getByRole('button',{name:'重试地图',exact:true})).toBeVisible()
  const before=configs;await page.getByRole('button',{name:'重试地图',exact:true}).click();await expect.poll(()=>configs).toBeGreaterThan(before)
  await page.setViewportSize({width:320,height:740});await workspace(page,'收集');await capture(page,info,'narrow-collection.png')
  await page.evaluate(()=>window.scrollTo(0,0));const menu=page.getByRole('button',{name:'打开导航菜单',exact:true});await menu.click()
  await expect(page.locator('#site-links a').first()).toBeFocused();await page.keyboard.press('Escape');await expect(menu).toBeFocused();await expect(menu).toHaveAttribute('aria-expanded','false')
  await menu.click();expect(await page.evaluate(()=>Boolean(document.elementFromPoint(310,650)?.closest('nav.nav')))).toBe(false);await page.mouse.click(310,650);await expect(menu).toHaveAttribute('aria-expanded','false')
})
