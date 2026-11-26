import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'
const ORIGIN='http://127.0.0.1:5264',HOME=ORIGIN+'/travel/',PASSWORD='isolated-history-password'
const RELOAD='重新载入会替换本页聊天和地图，并放弃本页快照草稿；已发送的保存不会被撤销。确定载入吗？'
const CHANGE='历史方案有未确认保存或操作。切换账号会结束本页核对状态，不会撤销已发请求，确定切换吗？'
const END='结束只清除本页核对信息，不会撤销已发出的操作。请先核对服务器状态，确定结束吗？'
async function localOnly(page){await page.route('**/*',r=>new URL(r.request().url()).origin===ORIGIN?r.continue():r.abort())}
async function account(request){const username='ph_'+randomUUID().replaceAll('-','').slice(0,16);expect((await request.post(HOME+'api/auth/register',{data:{username,password:PASSWORD}})).status()).toBe(201);const r=await request.post(HOME+'api/auth/login',{data:{username,password:PASSWORD}}),body=await r.json();return {username,auth:{Authorization:'Bearer '+body.token}}}
async function login(page,user){await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click();const d=page.getByRole('dialog',{name:'登录',exact:true});await d.getByRole('textbox',{name:'用户名',exact:true}).fill(user.username);await d.getByLabel('密码',{exact:true}).fill(PASSWORD);await d.getByRole('button',{name:'登录',exact:true}).last().click();await expect(d).toHaveCount(0)}
async function detail(request,user,id){const r=await request.get(HOME+`api/plan/${id}`,{headers:user.auth});expect(r.status()).toBe(200);return r.json()}
async function state(request,user,id){return (await request.get(HOME+`api/plan/${id}/state`,{headers:user.auth})).json()}
async function seed(request,user,label){const r=await request.post(HOME+'api/plan/jobs/create',{headers:user.auth,data:{request_id:randomUUID(),user_input:'正常生成历史'+label,messages:[{role:'user',text:'历史'+label+'输入'},{role:'ai',text:'历史'+label+'对话'}]}});expect(r.status()).toBe(201);const jid=(await r.json()).job_id;let snap;await expect.poll(async()=>{snap=await(await request.get(HOME+`api/plan/jobs/${jid}`,{headers:user.auth})).json();return snap.status}).toBe('done');return detail(request,user,snap.plan_id)}
async function setup(page,request){await localOnly(page);await page.goto(HOME);const user=await account(request),a=await seed(request,user,'甲'),b=await seed(request,user,'乙');await login(page,user);await page.goto(HOME+'plan');await expect(page.getByRole('button',{name:/历史方案（2）/})).toBeVisible();return {user,a,b}}
async function list(page){if(!await page.locator('.hist-list').isVisible())await page.getByRole('button',{name:/历史方案/}).click()}
async function open(page,label){await list(page);const button=page.getByRole('button',{name:new RegExp('^历史方案'+label)});await button.focus();await page.keyboard.press('Enter');await expect(page.locator('.msg.ai .bubble')).toHaveText('历史'+label+'对话')}
async function map(page,center,zoom){await page.evaluate(async v=>{const {usePlanSession}=await import('/travel/src/store/planSession.js');usePlanSession().setMapView(v)},{center,zoom})}
async function localMap(page){return page.evaluate(async()=>{const {usePlanSession}=await import('/travel/src/store/planSession.js');return usePlanSession().mapView.value})}
async function deleteRow(page,label){await list(page);const row=page.locator('.hist-item').filter({hasText:'历史方案'+label});await row.getByTitle('删除这条方案',{exact:true}).click();await row.locator('.hist-confirm').getByRole('button',{name:'删除',exact:true}).click()}
async function answer(page,action,accept,message,type='confirm'){const handled=page.waitForEvent('dialog').then(async d=>{expect(d.type()).toBe(type);if(type==='confirm')expect(d.message()).toBe(message);if(accept)await d.accept();else await d.dismiss()});await Promise.all([handled,action()])}
async function capture(page,info,name,target){if(target)await target.evaluate(el=>el.scrollIntoView({block:'center'}));else await page.evaluate(()=>window.scrollTo(0,0));await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);if(target){const box=await target.boundingBox(),nav=page.getByRole('navigation',{name:'主要导航'});expect(box.y).toBeGreaterThanOrEqual(56);if(await nav.isVisible())expect(box.y+box.height).toBeLessThanOrEqual((await nav.boundingBox()).y)}await page.screenshot({path:info.outputPath(name)})}

test('键盘打开历史并保存视角，刷新再读原聊天和地图',async({page,request},info)=>{
  const {user,a}=await setup(page,request);await open(page,'甲');await map(page,[120,30],11)
  await expect.poll(async()=>(await detail(request,user,a.id)).map_state.zoom).toBe(11);await page.reload();await open(page,'甲');expect(await localMap(page)).toEqual({center:[120,30],zoom:11});await capture(page,info,'history-open.png')
})

test('较早历史读取迟到，不能覆盖已选择的另一条',async({page,request})=>{
  const {a}=await setup(page,request);let release,arrived,finished
  const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),complete=new Promise(r=>finished=r)
  await page.route(`**/api/plan/${a.id}`,async r=>{const response=await r.fetch();arrived();await gate;await r.fulfill({response});finished()})
  try{await list(page);await page.getByRole('button',{name:/^历史方案甲/}).click();await received;await page.getByRole('button',{name:/^历史方案乙/}).click();await expect(page.locator('.msg.ai .bubble')).toHaveText('历史乙对话');release();await complete;await expect(page.locator('.msg.ai .bubble')).toHaveText('历史乙对话')}finally{release()}
})

test('历史列表故障保留已有行，显式重试可恢复',async({page,request})=>{
  await setup(page,request);let failing=true
  await page.route('**/api/plan/history',r=>failing?r.fulfill({status:503,json:{detail:'隔离历史列表故障'}}):r.continue())
  await page.evaluate(async()=>{const {usePlanSession}=await import('/travel/src/store/planSession.js');await usePlanSession().refreshHistory()});await list(page)
  await expect(page.getByRole('alert').filter({hasText:'隔离历史列表故障'})).toBeVisible();await expect(page.locator('.hist-item')).toHaveCount(2)
  failing=false;await page.getByRole('button',{name:'重试历史列表',exact:true}).click();await expect(page.getByRole('alert').filter({hasText:'隔离历史列表故障'})).toHaveCount(0)
})

test('视角延时保存捕获原记录，切换历史不会写进新记录',async({page,request})=>{
  const {user,a,b}=await setup(page,request);await open(page,'甲');await map(page,[120,30],10);await open(page,'乙')
  await expect.poll(async()=>(await detail(request,user,a.id)).map_state.zoom).toBe(10);expect((await detail(request,user,b.id)).map_state).toEqual({});await expect(page.locator('.msg.ai .bubble')).toHaveText('历史乙对话')
})

test('另一标签已更新快照，旧页保留视角和原版本，核对不覆盖',async({page,request,context},info)=>{
  const {user,a}=await setup(page,request);await open(page,'甲');const other=await context.newPage();await localOnly(other);await other.goto(HOME+'plan');await expect(other.getByRole('button',{name:/历史方案（2）/})).toBeVisible();await open(other,'甲');await map(other,[121,31],12)
  await expect.poll(async()=>(await detail(request,user,a.id)).version).toBe(2)
  const calls=[];await page.route(`**/api/plan/${a.id}/snapshot/edit`,async r=>{calls.push(r.request().postDataJSON());await r.continue()});await map(page,[120,30],10)
  await expect(page.locator('.snapshot-feedback')).toContainText('已更新');expect(await localMap(page)).toEqual({center:[120,30],zoom:10})
  await page.getByRole('button',{name:'核对快照状态',exact:true}).click();await expect(page.locator('.snapshot-feedback')).toContainText('版本2')
  await page.getByRole('button',{name:'重试原次快照',exact:true}).click();await expect(page.locator('.snapshot-feedback')).toContainText('已更新');expect(calls[1]).toEqual(calls[0])
  await capture(page,info,'snapshot-conflict.png',page.getByRole('button',{name:'重新载入服务端快照',exact:true}))
  await answer(page,()=>page.getByRole('button',{name:'重新载入服务端快照',exact:true}).click(),false,RELOAD);expect((await localMap(page)).zoom).toBe(10)
  await answer(page,()=>page.getByRole('button',{name:'重新载入服务端快照',exact:true}).click(),true,RELOAD);await expect(page.locator('.snapshot-feedback')).toHaveCount(0);expect(await localMap(page)).toEqual({center:[121,31],zoom:12});await other.close()
})

test('快照已保存但响应丢失，原版本重试不覆盖已提交数据',async({page,request})=>{
  const {user,a}=await setup(page,request);await open(page,'甲');const calls=[]
  await page.route(`**/api/plan/${a.id}/snapshot/edit`,async r=>{calls.push(r.request().postDataJSON());const response=await r.fetch();if(calls.length===1)return r.abort('failed');await r.fulfill({response})})
  await map(page,[120,30],11);await expect(page.locator('.snapshot-feedback')).toContainText('无法确认');expect((await detail(request,user,a.id)).version).toBe(2)
  await page.getByRole('button',{name:'核对快照状态',exact:true}).click();await page.getByRole('button',{name:'重试原次快照',exact:true}).click();await expect(page.locator('.snapshot-feedback')).toContainText('已更新');expect(calls[1]).toEqual(calls[0]);expect((await detail(request,user,a.id)).map_state.zoom).toBe(11)
})

test('连续视角串行提交，后一次使用确认后的版本',async({page,request})=>{
  const {user,a}=await setup(page,request);await open(page,'甲');const bodies=[];let release,arrived
  const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r)
  await page.route(`**/api/plan/${a.id}/snapshot/edit`,async r=>{bodies.push(r.request().postDataJSON());const response=await r.fetch();if(bodies.length===1){arrived();await gate}await r.fulfill({response})})
  try{await map(page,[120,30],10);await received;await map(page,[122,32],13);expect(bodies).toHaveLength(1);release();await expect.poll(async()=>(await detail(request,user,a.id)).version).toBe(3);expect(bodies.map(x=>x.version)).toEqual([1,2]);expect((await detail(request,user,a.id)).map_state.center).toEqual([122,32])}finally{release()}
})

test('删除确认及限时撤销走服务端原版本，内容恢复完整',async({page,request},info)=>{
  const {user,a}=await setup(page,request);await open(page,'甲');await deleteRow(page,'甲');const undo=page.getByRole('button',{name:/^撤销（/});await expect(undo).toBeVisible();expect((await state(request,user,a.id)).status).toBe('deleted');await capture(page,info,'history-undo.png',undo)
  await undo.click();await expect.poll(async()=>(await state(request,user,a.id)).status).toBe('completed');expect((await detail(request,user,a.id)).chat.at(-1).text).toBe('历史甲对话')
})

test('删除已提交后丢响应，核对确认而不是另删一次',async({page,request},info)=>{
  const {user,a}=await setup(page,request);await open(page,'甲');let deletes=0
  await page.route(`**/api/plan/${a.id}/content?*`,async r=>{deletes++;const response=await r.fetch();expect(response.status()).toBe(200);await r.abort('failed')})
  await deleteRow(page,'甲');await expect(page.locator('.history-pending')).toContainText('无法确认');await capture(page,info,'delete-unknown.png',page.getByRole('button',{name:'核对服务器状态',exact:true}))
  await page.getByRole('button',{name:'核对服务器状态',exact:true}).click();await expect(page.locator('.history-pending')).toHaveCount(0);expect(deletes).toBe(1);expect((await state(request,user,a.id)).version).toBe(2)
})

test('未送达的删除核对后继续仍用原版本',async({page,request})=>{
  const {user,a}=await setup(page,request);await open(page,'甲');const queries=[]
  await page.route(`**/api/plan/${a.id}/content?*`,async r=>{queries.push(new URL(r.request().url()).search);if(queries.length===1)return r.abort('failed');await r.continue()})
  await deleteRow(page,'甲');await expect(page.locator('.history-pending')).toContainText('无法确认');await page.getByRole('button',{name:'核对服务器状态',exact:true}).click();await expect(page.locator('.history-pending')).toContainText('原次提交版本仍为1')
  await page.getByRole('button',{name:'继续原次操作',exact:true}).click();await expect(page.locator('.history-pending')).toHaveCount(0);expect(queries[1]).toBe(queries[0]);expect((await state(request,user,a.id)).version).toBe(2)
})

test('旧删除成功迟到不清除之后打开的历史',async({page,request})=>{
  const {a}=await setup(page,request);await open(page,'甲');let release,arrived,finished
  const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),complete=new Promise(r=>finished=r)
  await page.route(`**/api/plan/${a.id}/content?*`,async r=>{const response=await r.fetch();arrived();await gate;await r.fulfill({response});finished()})
  try{await deleteRow(page,'甲');await received;await page.getByRole('button',{name:/^历史方案乙/}).click();await expect(page.locator('.msg.ai .bubble')).toHaveText('历史乙对话');release();await complete;await expect(page.locator('.history-pending')).toHaveCount(0);await expect(page.locator('.msg.ai .bubble')).toHaveText('历史乙对话')}finally{release()}
})

test('其他页面恢复后再删除，旧撤销不覆盖更新后的决定',async({page,request})=>{
  const {user,a}=await setup(page,request);await open(page,'甲');await deleteRow(page,'甲');const undo=page.getByRole('button',{name:/^撤销（/});await expect(undo).toBeVisible()
  expect((await request.post(HOME+`api/plan/${a.id}/content/restore`,{headers:user.auth,data:{version:2,revision:a.revision}})).status()).toBe(200)
  expect((await request.delete(HOME+`api/plan/${a.id}/content?version=3&revision=${a.revision}`,{headers:user.auth})).status()).toBe(200)
  await undo.click();await expect(page.locator('.history-pending')).toContainText('已更新');await page.getByRole('button',{name:'核对服务器状态',exact:true}).click();await expect(page.locator('.history-pending')).toContainText('版本4');expect((await state(request,user,a.id)).status).toBe('deleted')
  await answer(page,()=>page.getByRole('button',{name:'结束本次核对',exact:true}).click(),false,END);await expect(page.locator('.history-pending')).toBeVisible();await answer(page,()=>page.getByRole('button',{name:'结束本次核对',exact:true}).click(),true,END)
})

test('撤销期限由服务器控制，过期不复活',async({page,request})=>{
  const {user,a}=await setup(page,request);await open(page,'甲');await deleteRow(page,'甲');const undo=page.getByRole('button',{name:/^撤销（/});await expect(undo).toBeVisible()
  expect((await request.post(HOME+`api/__qa/plan/expire-undo/${a.id}`,{headers:user.auth})).status()).toBe(200)
  await undo.click();await expect(page.locator('.history-pending')).toContainText('撤销期限已结束');expect((await state(request,user,a.id)).can_restore).toBe(false)
})

test('未知快照保护刷新和换号，取消后草稿仍在',async({page,request})=>{
  const {a}=await setup(page,request);await open(page,'甲');await page.route(`**/api/plan/${a.id}/snapshot/edit`,r=>r.fulfill({status:503,json:{detail:'隔离快照故障'}}));await map(page,[120,30],10);await expect(page.locator('.snapshot-feedback')).toContainText('隔离快照故障')
  await answer(page,()=>page.evaluate(()=>location.reload()),false,null,'beforeunload');expect((await localMap(page)).zoom).toBe(10)
  await answer(page,()=>page.getByRole('button',{name:'退出',exact:true}).click(),false,CHANGE);await expect(page.locator('.msg.ai .bubble')).toHaveText('历史甲对话');expect((await localMap(page)).zoom).toBe(10)
})

test('不匹配的详情200保留现有历史，不显示另一条内容',async({page,request})=>{
  const {a,b}=await setup(page,request);await open(page,'乙');await page.route(`**/api/plan/${a.id}`,r=>r.fulfill({json:{...b,chat:[{role:'ai',text:'错误详情'}]}}));await list(page);await page.getByRole('button',{name:/^历史方案甲/}).click();await expect(page.getByRole('alert')).toContainText('无法确认历史方案');await expect(page.locator('.msg.ai .bubble')).toHaveText('历史乙对话')
})

test('生成保存完整回复，刷新后的已确认版本仍可继续保存地图',async({page,request})=>{
  const {user}=await setup(page,request)
  const created=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/jobs/create'))
  await page.getByPlaceholder('输入目的地或告诉我你的想法',{exact:true}).fill('正常生成完整回复');await page.getByRole('button',{name:'发送',exact:true}).click();const jid=(await(await created).json()).job_id
  let snap,pid,p
  await expect.poll(async()=>{snap=await(await request.get(HOME+`api/plan/jobs/${jid}`,{headers:user.auth})).json();pid=snap.plan_id;return snap.status}).toBe('done')
  await expect.poll(async()=>{p=await detail(request,user,pid);return p.chat.at(-1)?.text.includes('已生成')&&p.version===2}).toBe(true)
  await page.reload();await expect(page.locator('.msg.ai .bubble').last()).toHaveText(p.chat.at(-1).text);await map(page,[120,30],12);await expect.poll(async()=>(await detail(request,user,pid)).version).toBe(3)
})
