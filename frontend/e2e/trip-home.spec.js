import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'
const ORIGIN='http://127.0.0.1:5264',HOME=ORIGIN+'/travel/',PASSWORD='isolated-home-password'
const LEAVE='首页还有待确认的转换或复制。离开会保留本标签恢复信息，不会撤销已发请求，确定继续吗？'
const DISCARD='放弃只清除本标签恢复信息，不会删除已转换或复制的账号旅行。请先核对账号旅行，确定放弃吗？'
const panel=p=>p.getByRole('status').filter({hasText:/转换或复制待确认|账号旅行已确认/})
async function account(request){const username='home_'+randomUUID().replaceAll('-','').slice(0,14);expect((await request.post(HOME+'api/auth/register',{data:{username,password:PASSWORD}})).status()).toBe(201);const b=await(await request.post(HOME+'api/auth/login',{data:{username,password:PASSWORD}})).json();return {username,auth:{Authorization:'Bearer '+b.token},id:b.user.id}}
async function login(page,user){await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click();const d=page.getByRole('dialog',{name:'登录',exact:true});await d.getByRole('textbox',{name:'用户名',exact:true}).fill(user.username);await d.getByLabel('密码',{exact:true}).fill(PASSWORD);await d.getByRole('button',{name:'登录',exact:true}).last().click();await expect(d).toHaveCount(0)}
async function seed(request,user){const j=await(await request.post(HOME+'api/plan/jobs/create',{headers:user.auth,data:{request_id:randomUUID(),user_input:'正常生成历史甲',messages:[{role:'ai',text:'首页历史原聊天'}]}})).json();let s;await expect.poll(async()=>{s=await(await request.get(HOME+'api/plan/jobs/'+j.job_id,{headers:user.auth})).json();return s.status}).toBe('done');return (await(await request.get(HOME+'api/plan/'+s.plan_id,{headers:user.auth})).json())}
async function setup(page,request,{trip=true}={}){await page.route('**/*',r=>new URL(r.request().url()).origin===ORIGIN?r.continue():r.abort());await page.goto(HOME);const user=await account(request),plan=await seed(request,user);if(trip)expect((await request.post(HOME+'api/trips',{headers:user.auth,data:{title:'已有账号旅行',days:1}})).status()).toBe(201);await login(page,user);await expect(page.locator('.history-row')).toHaveCount(1);return {user,plan}}
async function trips(request,user){return (await(await request.get(HOME+'api/trips',{headers:user.auth})).json()).items}
async function convert(page){await page.locator('.history-row').getByRole('button',{name:'转为旅行',exact:true}).click()}
async function local(page){const t=await page.evaluate(async()=>{const {guestTrips}=await import('/travel/src/store/guestTrips.js');return guestTrips.create({title:'本机原件',destination:'杭州',days:1})});await page.reload();await expect(page.locator('.local-card')).toHaveCount(1);return t}
async function answer(page,action,accept,text=LEAVE,type='confirm'){const d=page.waitForEvent('dialog').then(async x=>{expect(x.type()).toBe(type);if(type==='confirm')expect(x.message()).toBe(text);if(accept)await x.accept();else await x.dismiss()});await Promise.all([d,action()])}
async function go(page,name){const link=page.getByRole('navigation').first().getByRole('link',{name,exact:true});if(await link.isVisible())await link.click();else await page.getByRole('navigation',{name:'主要导航'}).getByRole('link',{name:new RegExp(name==='我的旅行'?'旅行':'探索')}).click()}
async function capture(page,info,name){const action=panel(page).getByRole('link').or(panel(page).getByRole('button')).first();await action.evaluate(el=>el.scrollIntoView({block:'center'}));await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);const box=await action.boundingBox();expect(box.y).toBeGreaterThanOrEqual(56);const nav=page.getByRole('navigation',{name:'主要导航'});if(await nav.isVisible())expect(box.y+box.height).toBeLessThanOrEqual((await nav.boundingBox()).y);await page.screenshot({path:info.outputPath(name)})}

test('键盘转换原历史，显式打开可编辑旅行，原聊天保留',async({page,request},info)=>{
  const {user,plan}=await setup(page,request,{trip:false});const button=page.locator('.history-row').getByRole('button',{name:'转为旅行',exact:true});await button.focus();await page.keyboard.press('Enter');await expect(panel(page)).toContainText('账号旅行已确认');await capture(page,info,'home-confirmed.png');expect(new URL(page.url()).pathname).toBe('/travel/');expect(await trips(request,user)).toHaveLength(1)
  await panel(page).getByRole('link',{name:'打开已确认旅行 →',exact:true}).click();await expect(page.getByRole('heading',{name:'历史方案甲',exact:true})).toBeVisible();expect((await(await request.get(HOME+`api/plan/${plan.id}`,{headers:user.auth})).json()).chat).toEqual([{role:'ai',text:'首页历史原聊天'}])
})
test('转换已提交丢响应，只读核对找回已编辑结果，不再POST',async({page,request},info)=>{
  const {user,plan}=await setup(page,request);let writes=0,pid
  await page.route(`**/from-plan/${plan.id}/convert`,async r=>{writes++;const response=await r.fetch();pid=(await response.json()).trip.id;await r.abort('failed')})
  await convert(page);await expect(panel(page)).toContainText('无法确认');await capture(page,info,'home-pending.png')
  expect((await request.patch(HOME+'api/trips/'+pid,{headers:user.auth,data:{version:1,title:'其他页面已编辑'}})).status()).toBe(200)
  await panel(page).getByRole('button',{name:'核对账号结果',exact:true}).click();await expect(panel(page)).toContainText('账号旅行已确认');expect(writes).toBe(1);expect((await trips(request,user)).find(t=>t.id===pid).title).toBe('其他页面已编辑')
})
test('未送达转换，核对不变更原版本，继续仅创建一次',async({page,request})=>{
  const {user,plan}=await setup(page,request);const bodies=[];await page.route(`**/from-plan/${plan.id}/convert`,async r=>{bodies.push(r.request().postDataJSON());if(bodies.length===1)return r.abort('failed');await r.continue()})
  await convert(page);await expect(panel(page)).toContainText('无法确认');await panel(page).getByRole('button',{name:'核对账号结果',exact:true}).click();await expect(panel(page)).toContainText('尚未找到');await panel(page).getByRole('button',{name:'继续原次操作',exact:true}).click();await expect(panel(page)).toContainText('账号旅行已确认');expect(bodies[1]).toEqual(bodies[0]);expect((await trips(request,user)).filter(t=>t.source_plan_id===plan.id)).toHaveLength(1)
})
test('旧版本冲突保留原次，核对和重试不偷偷采用新版本',async({page,request})=>{
  const {user,plan}=await setup(page,request);expect((await request.patch(HOME+`api/plan/${plan.id}/snapshot/edit`,{headers:user.auth,data:{version:1,revision:plan.revision,messages:[{role:'ai',text:'另一页新聊天'}]}})).status()).toBe(200)
  const bodies=[];await page.route(`**/from-plan/${plan.id}/convert`,async r=>{bodies.push(r.request().postDataJSON());await r.continue()});await convert(page);await expect(panel(page)).toContainText('原方案已更新');await panel(page).getByRole('button',{name:'核对账号结果',exact:true}).click();await panel(page).getByRole('button',{name:'继续原次操作',exact:true}).click();await expect(panel(page)).toContainText('原方案已更新');expect(bodies.map(b=>b.version)).toEqual([1,1]);expect((await trips(request,user)).filter(t=>t.source_plan_id===plan.id)).toHaveLength(0)
})
test('复制丢响应刷新继续使用原本机快照，改动原件不混入重试',async({page,request})=>{
  const {user}=await setup(page,request);const t=await local(page),bodies=[]
  await page.route('**/api/trips/import-guest',async r=>{bodies.push(r.request().postDataJSON());const response=await r.fetch();if(bodies.length===1)return r.abort('failed');await r.fulfill({response})})
  await page.getByRole('button',{name:'复制到账号',exact:true}).click();await expect(panel(page)).toContainText('无法确认')
  await page.evaluate(async t=>{const {guestTrips}=await import('/travel/src/store/guestTrips.js');await guestTrips.patch(t.id,{version:t.version,title:'本机后续修改'})},t)
  await answer(page,()=>page.reload(),true,null,'beforeunload');await expect(panel(page)).toContainText('本机版本 1');await panel(page).getByRole('button',{name:'继续原次操作',exact:true}).click();await expect(panel(page)).toContainText('账号旅行已确认');expect(bodies[1]).toEqual(bodies[0]);expect((await trips(request,user)).filter(t=>t.title==='本机原件')).toHaveLength(1);await expect(page.locator('.local-card')).toContainText('本机后续修改')
})
test('本机复制成功只读核对，标记故障保留明确账号结果',async({page,request})=>{
  await setup(page,request);await local(page);let writes=0
  await page.evaluate(async()=>{const {guestTrips}=await import('/travel/src/store/guestTrips.js');guestTrips.markMigrated=async()=>{throw Error('隔离标记故障')}})
  await page.route('**/api/trips/import-guest',async r=>{writes++;const response=await r.fetch();await r.abort('failed')});await page.getByRole('button',{name:'复制到账号',exact:true}).click();await expect(panel(page)).toContainText('无法确认');await panel(page).getByRole('button',{name:'核对账号结果',exact:true}).click();await expect(panel(page)).toContainText('账号旅行已确认');await expect(page.getByRole('status').filter({hasText:'本机复制标记未保存'})).toBeVisible();expect(writes).toBe(1)
})
test('账号旅行503重试保留已有卡片，历史无需重新请求',async({page,request})=>{
  await setup(page,request);let historyReads=0;page.on('request',r=>{if(r.url().endsWith('/api/plan/history'))historyReads++});await page.route('**/api/trips',r=>r.request().method()==='GET'?r.fulfill({status:503,json:{detail:'隔离账号旅行故障'}}):r.continue())
  await page.getByRole('button',{name:'刷新账号旅行',exact:true}).click();await expect(page.getByRole('alert')).toContainText('隔离账号旅行故障');await expect(page.getByRole('heading',{name:'已有账号旅行',exact:true})).toBeVisible();await expect(page.locator('.history-row')).toHaveCount(1);expect(historyReads).toBe(0)
  await page.unroute('**/api/trips');await page.getByRole('button',{name:'重试账号旅行',exact:true}).click();await expect(page.getByRole('alert')).toHaveCount(0)
})
test('历史坏200保留列表，单独重试可恢复',async({page,request})=>{
  await setup(page,request);await page.route('**/api/plan/history',r=>r.fulfill({json:{items:null}}));await page.getByRole('button',{name:'刷新历史方案',exact:true}).click();await expect(page.getByRole('alert')).toContainText('数据不完整');await expect(page.locator('.history-row')).toHaveCount(1);await expect(page.getByRole('heading',{name:'已有账号旅行',exact:true})).toBeVisible();await page.unroute('**/api/plan/history');await page.getByRole('button',{name:'重试历史方案',exact:true}).click();await expect(page.getByRole('alert')).toHaveCount(0)
})
test('本机列表错误不阻塞账号来源，已有本机卡片保留',async({page,request})=>{
  await setup(page,request);await local(page);await page.evaluate(async()=>{const {guestTrips}=await import('/travel/src/store/guestTrips.js');guestTrips.list=async()=>{throw Error('隔离本机读取故障')}});await page.getByRole('button',{name:'刷新本机旅行',exact:true}).click();await expect(page.getByRole('alert')).toContainText('隔离本机读取故障');await expect(page.locator('.local-card')).toHaveCount(1);await expect(page.locator('.history-row')).toHaveCount(1);await expect(page.getByRole('heading',{name:'已有账号旅行',exact:true})).toBeVisible()
})
test('未知操作保护路由刷新换号，取消后仍保留，返回可继续',async({page,request})=>{
  const {plan}=await setup(page,request);await page.route(`**/from-plan/${plan.id}/convert`,r=>r.fulfill({status:503,json:{detail:'隔离转换故障'}}));await convert(page);await expect(panel(page)).toContainText('隔离转换故障')
  await answer(page,()=>go(page,'探索地点'),false);await expect(panel(page)).toBeVisible();await answer(page,()=>page.evaluate(()=>location.reload()),false,null,'beforeunload');await answer(page,()=>page.getByRole('button',{name:'退出',exact:true}).click(),false);await expect(panel(page)).toBeVisible()
  await answer(page,()=>go(page,'探索地点'),true);await go(page,'我的旅行');await expect(panel(page)).toContainText('待确认')
})
test('换号接受后迟到成功不跳页，原账号回到固定尝试',async({page,request})=>{
  const {user,plan}=await setup(page,request),other=await account(request);let release,arrived,finished
  const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),complete=new Promise(r=>finished=r)
  await page.route(`**/from-plan/${plan.id}/convert`,async r=>{const response=await r.fetch();arrived();await gate;await r.fulfill({response});finished()})
  try{await convert(page);await received;await answer(page,()=>page.getByRole('button',{name:'退出',exact:true}).click(),true);await login(page,other);release();await complete;await expect(page.locator('.transfer-panel')).toHaveCount(0);expect(new URL(page.url()).pathname).toBe('/travel/');await page.getByRole('button',{name:'退出',exact:true}).click();await login(page,user);await expect(panel(page)).toContainText('待确认');await panel(page).getByRole('button',{name:'核对账号结果',exact:true}).click();await expect(panel(page)).toContainText('账号旅行已确认')}finally{release()}
})
test('存储不可写不首次发送，旧能力也不回退普通接口',async({page,request})=>{
  await setup(page,request);let writes=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/from-plan/'))writes++})
  await page.evaluate(()=>{const original=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k.startsWith('travel.home-transfer'))throw Error('quota');return original.call(this,k,v)}});await convert(page);await expect(page.getByRole('status').filter({hasText:'无法保存本标签恢复信息'})).toBeVisible();expect(writes).toBe(0)
  await page.reload();await page.route('**/api/trips/plan-import-capabilities',r=>r.fulfill({json:{}}));await convert(page);await expect(panel(page)).toContainText('不支持安全恢复');expect(writes).toBe(0)
})
test('错误归属的200不清原次，真实重试找回同一旅行',async({page,request})=>{
  const {user,plan}=await setup(page,request);let writes=0
  await page.route(`**/from-plan/${plan.id}/convert`,async r=>{writes++;const response=await r.fetch();await r.fulfill(writes===1?{json:{...await response.json(),owner_id:user.id+10000}}:{response})});await convert(page);await expect(panel(page)).toContainText('回执无法确认');await panel(page).getByRole('button',{name:'继续原次操作',exact:true}).click();await expect(panel(page)).toContainText('账号旅行已确认');expect((await trips(request,user)).filter(t=>t.source_plan_id===plan.id)).toHaveLength(1)
})
test('损坏恢复信息锁定提交，放弃可取消且不删除账号旅行',async({page,request})=>{
  const {user}=await setup(page,request);await page.evaluate(uid=>sessionStorage.setItem('travel.home-transfer.v1.'+uid,'{bad'),user.id);await page.reload();await expect(page.locator('.transfer-panel')).toContainText('恢复信息损坏');await expect(page.locator('.history-row').getByRole('button',{name:'转为旅行',exact:true})).toBeDisabled();await answer(page,()=>page.getByRole('button',{name:'放弃本标签恢复',exact:true}).click(),false,DISCARD);await expect(page.locator('.transfer-panel')).toBeVisible();await answer(page,()=>page.getByRole('button',{name:'放弃本标签恢复',exact:true}).click(),true,DISCARD);await expect(page.locator('.transfer-panel')).toHaveCount(0);expect(await trips(request,user)).toHaveLength(1)
})
test('本机预订与订单号显式复制，原件保留，未调用真实预订',async({page,request})=>{
  const {user}=await setup(page,request);const t=await local(page)
  await page.evaluate(async t=>{const {guestTrips}=await import('/travel/src/store/guestTrips.js');await guestTrips.addReservation(t.id,{version:t.version,kind:'住宿',title:'隔离酒店',status:'已预订',reference:'HOME-QA-001',item_id:null})},t)
  await page.reload();await expect(page.locator('.local-hint')).toContainText('预订记录及订单号');await page.getByRole('button',{name:'复制到账号',exact:true}).click();await expect(panel(page)).toContainText('账号旅行已确认')
  const copied=(await trips(request,user)).find(t=>t.title==='本机原件');const records=await(await request.get(HOME+`api/trips/${copied.id}/reservations`,{headers:user.auth})).json();expect(records.items[0].reference).toBe('HOME-QA-001');expect(await page.evaluate(async id=>{const {guestTrips}=await import('/travel/src/store/guestTrips.js');return (await guestTrips.get(id)).reservations.length},t.id)).toBe(1)
})
