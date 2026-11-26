import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'
const ORIGIN='http://127.0.0.1:5264',HOME=ORIGIN+'/travel/',PASSWORD='isolated-plan-password'
const CHANGE='当前规划还有未发送输入或正在生成的任务。切换账号会保留本标签原账号会话，后台任务可能继续，确定切换吗？'
const input=p=>p.getByPlaceholder('输入目的地或告诉我你的想法',{exact:true})
async function localOnly(page){await page.route('**/*',r=>new URL(r.request().url()).origin===ORIGIN?r.continue():r.abort())}
async function account(request){const username='pr_'+randomUUID().replaceAll('-','').slice(0,16);expect((await request.post(HOME+'api/auth/register',{data:{username,password:PASSWORD}})).status()).toBe(201);const r=await request.post(HOME+'api/auth/login',{data:{username,password:PASSWORD}}),body=await r.json();const auth={Authorization:'Bearer '+body.token};const me=await (await request.get(HOME+'api/auth/me',{headers:auth})).json();return {username,auth,id:me.id}}
async function login(page,user){await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click();const d=page.getByRole('dialog',{name:'登录',exact:true});await d.getByRole('textbox',{name:'用户名',exact:true}).fill(user.username);await d.getByLabel('密码',{exact:true}).fill(PASSWORD);await d.getByRole('button',{name:'登录',exact:true}).last().click();await expect(d).toHaveCount(0)}
async function setup(page,request,guest=false){await localOnly(page);await page.goto(HOME);const user=guest?null:await account(request);if(user)await login(page,user);await page.goto(HOME+'plan');await expect(input(page)).toBeVisible();return user}
async function send(page,label){const content=label+' '+randomUUID();const created=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith('/api/plan/jobs/create'));await input(page).fill(content);await page.getByRole('button',{name:'发送',exact:true}).click();const r=await created;expect(r.status()).toBe(201);return {content,id:(await r.json()).job_id}}
const isPoll=r=>r.request().method()==='GET'&&/\/api\/plan\/jobs\/[0-9a-f]{32}(?:\?|$)/i.test(r.request().url())
async function state(request,user,id){return (await request.get(HOME+`api/plan/jobs/${id}`,{headers:user?.auth||{}})).json()}
async function done(page){await expect(page.locator('.msg.ai .bubble')).toContainText('已生成「隔离原规划结果」',{timeout:15000});await expect(page.getByRole('button',{name:'发送',exact:true})).toBeDisabled();await expect(input(page)).toBeEnabled()}
async function navigate(page,path){await page.evaluate(async path=>{const {default:router}=await import('/travel/src/router/index.js');await router.push(path)},path)}
async function answer(page,action,accept){const response=page.waitForEvent('dialog').then(async d=>{expect(d.message()).toBe(CHANGE);if(accept)await d.accept();else await d.dismiss()});await Promise.all([response,action()])}
async function capture(page,info,name){
  await page.evaluate(()=>window.scrollTo(0,0))
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  const head=await page.locator('.chat-head').boundingBox();expect(head.y).toBeGreaterThanOrEqual(56)
  const boxes=await page.locator('.map-overlays > *').evaluateAll(nodes=>nodes.map(el=>{const r=el.getBoundingClientRect();return {top:r.top,bottom:r.bottom}}))
  for(let i=1;i<boxes.length;i++)expect(boxes[i].top).toBeGreaterThanOrEqual(boxes[i-1].bottom)
  await page.screenshot({path:info.outputPath(name)})
}

test('正常生成只保存一份历史，刷新后仍可读取',async({page,request},info)=>{
  const user=await setup(page,request);const job=await send(page,'正常生成');await done(page)
  const list=(await (await request.get(HOME+'api/plan/history',{headers:user.auth})).json()).items;expect(list).toHaveLength(1);expect((await state(request,user,job.id)).plan_id).toBe(list[0].id)
  await page.reload();await done(page);await capture(page,info,'plan-completed.png')
})

test('切走时后台继续，返回仍接收同一任务而不创建第二份',async({page,request})=>{
  const user=await setup(page,request);let writes=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/plan/jobs/create'))writes++})
  const job=await send(page,'慢速生成');await navigate(page,'/spots');await expect.poll(async()=>(await state(request,user,job.id)).status).toBe('done');await navigate(page,'/plan');await done(page);expect(writes).toBe(1)
})

test('追问刷新恢复不重复拼接旧token',async({page,request})=>{
  const user=await setup(page,request),job=await send(page,'追问');await expect(page.locator('.msg.ai .bubble')).toContainText('请补充出发日期。');await page.reload()
  await expect(input(page)).toBeEnabled();await expect(page.locator('.msg.ai .bubble')).toHaveText('请补充出发日期。');expect((await state(request,user,job.id)).result.question).toBe('请补充出发日期。')
})

test('执行者中断后同编号恢复，旧片段清除且只落一份历史',async({page,request},info)=>{
  const user=await setup(page,request),job=await send(page,'恢复生成');await expect(page.locator('.msg.ai .bubble')).toContainText('中断前的片段')
  expect((await request.post(HOME+`api/__qa/plan/recover/${job.id}`,{headers:user.auth})).status()).toBe(200);await done(page)
  await expect(page.locator('.msg.ai .bubble')).not.toContainText('中断前的片段');await expect(page.locator('.proc')).toContainText('正在恢复本次生成')
  expect((await (await request.get(HOME+'api/plan/history',{headers:user.auth})).json()).items).toHaveLength(1);await capture(page,info,'plan-recovered.png')
})

test('轮询503自动重连同编号，不重复发送生成请求',async({page,request},info)=>{
  const user=await setup(page,request);let failing=true,writes=0
  await page.route('**/api/plan/jobs/*',r=>isPoll(r)&&failing?r.fulfill({status:503,json:{detail:'隔离断连'}}):r.continue())
  page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/plan/jobs/create'))writes++})
  const job=await send(page,'正常生成');await expect(page.getByRole('status').filter({hasText:'正在自动重试'})).toBeVisible();await capture(page,info,'plan-reconnecting.png');failing=false;await done(page);expect(writes).toBe(1);expect((await state(request,user,job.id)).status).toBe('done')
})

test('停止生成确认后持久取消，刷新不继续运行',async({page,request})=>{
  const user=await setup(page,request),job=await send(page,'等待生成');await page.getByRole('button',{name:'停止生成',exact:true}).click();await expect(page.locator('.msg.ai .bubble')).toContainText('已取消这次生成');await expect(input(page)).toBeEnabled()
  expect((await state(request,user,job.id)).status).toBe('cancelled');await page.reload();await expect(input(page)).toBeEnabled();expect((await state(request,user,job.id)).status).toBe('cancelled');expect((await (await request.get(HOME+'api/plan/history',{headers:user.auth})).json()).items).toHaveLength(0)
})

test('取消响应丢失仍核对后台状态，不谎报继续生成',async({page,request})=>{
  const user=await setup(page,request),job=await send(page,'等待生成')
  await page.route('**/api/plan/jobs/create/cancel',async r=>{const response=await r.fetch();expect(response.status()).toBe(200);await r.abort('failed')})
  await page.getByRole('button',{name:'停止生成',exact:true}).click();await expect(page.locator('.msg.ai .bubble')).toContainText('已取消这次生成');expect((await state(request,user,job.id)).status).toBe('cancelled')
})

test('换号确认可取消，接受后旧任务不污染新账号，切回恢复',async({page,request})=>{
  const user=await setup(page,request),other=await account(request),job=await send(page,'等待生成')
  await answer(page,()=>page.getByRole('button',{name:'退出',exact:true}).click(),false);await expect(page.locator('.msg.user .bubble')).toContainText(job.content)
  await answer(page,()=>page.getByRole('button',{name:'退出',exact:true}).click(),true);await login(page,other);await expect(page.locator('.msg')).toHaveCount(0);await expect(input(page)).toBeEnabled()
  await page.getByRole('button',{name:'退出',exact:true}).click();await login(page,user);await expect(page.locator('.msg.user .bubble')).toContainText(job.content);await expect(input(page)).toBeDisabled();await page.getByRole('button',{name:'停止生成',exact:true}).click();await expect(input(page)).toBeEnabled()
})

test('游客刷新按不公开编号找回，新标签不列出其他游客任务',async({page,request,context})=>{
  await setup(page,request,true);const job=await send(page,'追问');await page.reload();await expect(page.locator('.msg.ai .bubble')).toHaveText('请补充出发日期。');expect((await state(request,null,job.id)).result.saved).toBe(false)
  const other=await context.newPage();await localOnly(other);await other.goto(HOME+'plan');await expect(other.locator('.msg')).toHaveCount(0);expect((await (await request.get(HOME+'api/plan/jobs/active')).json()).job).toBeNull();await other.close()
})

test('错编号200当作重连，真回执才更新当前任务',async({page,request})=>{
  await setup(page,request);let corrupt=true
  await page.route('**/api/plan/jobs/*',async r=>{if(!isPoll(r)||!corrupt)return r.continue();const response=await r.fetch();return r.fulfill({json:{...await response.json(),job_id:'wrong-job'}})})
  await send(page,'正常生成');await expect(page.getByRole('status').filter({hasText:'正在自动重试'})).toBeVisible();await expect(page.locator('.msg.ai .bubble')).not.toContainText('已生成');corrupt=false;await done(page)
})

test('旧账号历史删除迟到后不在新账号弹错或显示撤销条',async({page,request})=>{
  const user=await setup(page,request),other=await account(request);await send(page,'正常生成');await done(page)
  const row=(await (await request.get(HOME+'api/plan/history',{headers:user.auth})).json()).items[0]
  await page.evaluate(async()=>{const {api}=await import('/travel/src/api/index.js');const original=api.del;api.del=async(...args)=>{try{return await original(...args)}finally{window.__planDeleteSettled=true}}})
  let release,arrived,finished;const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),complete=new Promise(r=>finished=r)
  await page.route(`**/api/plan/${row.id}/content?*`,async r=>{if(r.request().method()!=='DELETE')return r.continue();const response=await r.fetch();arrived();await gate;await r.fulfill({response});finished()})
  const change='历史方案有未确认保存或操作。切换账号会结束本页核对状态，不会撤销已发请求，确定切换吗？'
  const dialogs=[];page.on('dialog',async d=>{dialogs.push(d.message());if(d.type()==='confirm'&&d.message()===change)await d.accept();else await d.dismiss()})
  try{
    await page.getByRole('button',{name:/历史方案/}).click();await page.getByTitle('删除这条方案',{exact:true}).click();await page.locator('.hist-confirm').getByRole('button',{name:'删除',exact:true}).click();await received
    await page.getByRole('button',{name:'退出',exact:true}).click();await login(page,other);release();await complete
    await page.waitForFunction(()=>window.__planDeleteSettled===true)
    await expect(page.locator('.msg')).toHaveCount(0);await expect(page.locator('.toast')).toHaveCount(0);expect(dialogs).toEqual([change])
  }finally{release()}
})
