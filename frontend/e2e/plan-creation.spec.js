import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'
const ORIGIN='http://127.0.0.1:5264',HOME=ORIGIN+'/travel/',PASSWORD='isolated-plan-creation-password'
const CREATE='**/api/plan/jobs/create',CANCEL='**/api/plan/jobs/create/cancel'
const CHANGE='当前规划还有未发送输入或正在生成的任务。切换账号会保留本标签原账号会话，后台任务可能继续，确定切换吗？'
const DISCARD='这次提交可能已在后台生成。放弃只清除本标签重试信息，不会停止任务或删除历史，确定放弃吗？'
const input=p=>p.getByPlaceholder('输入目的地或告诉我你的想法',{exact:true}),resume=p=>p.getByRole('button',{name:'继续确认这次提交',exact:true}),stop=p=>p.getByRole('button',{name:'停止这次提交',exact:true})
async function localOnly(page){await page.route('**/*',r=>new URL(r.request().url()).origin===ORIGIN?r.continue():r.abort())}
async function account(request){const username='pc_'+randomUUID().replaceAll('-','').slice(0,16);expect((await request.post(HOME+'api/auth/register',{data:{username,password:PASSWORD}})).status()).toBe(201);const response=await request.post(HOME+'api/auth/login',{data:{username,password:PASSWORD}}),body=await response.json();const auth={Authorization:'Bearer '+body.token},me=await(await request.get(HOME+'api/auth/me',{headers:auth})).json();return {username,auth,id:me.id}}
async function login(page,user){await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click();const d=page.getByRole('dialog',{name:'登录',exact:true});await d.getByRole('textbox',{name:'用户名',exact:true}).fill(user.username);await d.getByLabel('密码',{exact:true}).fill(PASSWORD);await d.getByRole('button',{name:'登录',exact:true}).last().click();await expect(d).toHaveCount(0)}
async function setup(page,request,guest=false){await localOnly(page);await page.goto(HOME);const user=guest?null:await account(request);if(user)await login(page,user);await page.goto(HOME+'plan');await expect(input(page)).toBeVisible();if(user)await expect(page.getByRole('button',{name:'退出',exact:true})).toBeVisible();return user}
async function publish(page,label){const content=label+' '+randomUUID();await input(page).fill(content);await page.getByRole('button',{name:'发送',exact:true}).click();return content}
async function done(page){await expect(page.locator('.msg.ai .bubble').last()).toContainText('已生成「隔离原规划结果」',{timeout:15000});await expect(input(page)).toBeEnabled();await expect(resume(page)).toHaveCount(0)}
async function saved(page,user){return page.evaluate(uid=>JSON.parse(sessionStorage.getItem('travel.plan-creation.v1.'+uid)),user?.id||0)}
async function history(request,user){return (await(await request.get(HOME+'api/plan/history',{headers:user.auth})).json()).items}
async function state(request,user,id){return (await request.get(HOME+`api/plan/jobs/${id}`,{headers:user?.auth||{}})).json()}
async function answer(page,action,accept,message){const response=page.waitForEvent('dialog').then(async d=>{expect(d.message()).toBe(message);if(accept)await d.accept();else await d.dismiss()});await Promise.all([response,action()])}
async function lost(page){const calls=[],ids=[];await page.route(CREATE,async r=>{calls.push(r.request().postDataJSON());const response=await r.fetch();ids.push((await response.json()).job_id);if(calls.length===1){expect(response.status()).toBe(201);await r.abort('failed')}else{expect(response.status()).toBe(200);await r.fulfill({response})}});return {calls,ids}}
async function capture(page,info,name){
  if(name.includes('pending'))await page.getByRole('button',{name:'放弃本标签恢复',exact:true}).evaluate(el=>el.scrollIntoView({block:'center'}));else await page.evaluate(()=>window.scrollTo(0,0))
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  if(name.includes('pending')){const box=await page.getByRole('button',{name:'放弃本标签恢复',exact:true}).boundingBox(),nav=page.getByRole('navigation',{name:'主要导航',exact:true});expect(box.y).toBeGreaterThanOrEqual(56);if(await nav.isVisible())expect(box.y+box.height).toBeLessThanOrEqual((await nav.boundingBox()).y)}
  await page.screenshot({path:info.outputPath(name)})
}

test('已提交后丢响应，原标识与内容找回同任务，只有一条历史',async({page,request},info)=>{
  const user=await setup(page,request),proof=await lost(page),content=await publish(page,'正常生成')
  await expect(resume(page)).toBeEnabled();await expect(input(page)).toBeDisabled();await expect(page.locator('.chat-addons')).toHaveAttribute('inert','');const before=await saved(page,user);expect(before.payload.user_input).toBe(content);expect(before.request_id).toBe(proof.calls[0].request_id)
  await capture(page,info,'creation-pending.png');await resume(page).click();await done(page)
  expect(proof.calls).toHaveLength(2);expect(proof.calls[1]).toEqual(proof.calls[0]);expect(proof.ids[1]).toBe(proof.ids[0]);expect(await history(request,user)).toHaveLength(1);expect(await saved(page,user)).toBeNull();await capture(page,info,'creation-recovered.png')
})

test('刷新恢复固定快照，继续不改UUID和正文',async({page,request})=>{
  const user=await setup(page,request),proof=await lost(page),content=await publish(page,'正常生成');await expect(resume(page)).toBeEnabled();const before=await saved(page,user)
  await page.reload();await expect(resume(page)).toBeEnabled();await expect(page.locator('.msg.user .bubble')).toContainText(content);expect(await saved(page,user)).toEqual(before)
  await resume(page).click();await done(page);expect(proof.calls[1]).toEqual(proof.calls[0]);expect(await history(request,user)).toHaveLength(1)
})

test('游客凭证随本标签恢复，独立新标签不带入待确认提交',async({page,request,context})=>{
  await setup(page,request,true);const proof=await lost(page);await publish(page,'正常生成');await expect(resume(page)).toBeEnabled();const before=await saved(page,null);expect(before.guest_key).toBeTruthy()
  const other=await context.newPage();await localOnly(other);await other.goto(HOME+'plan');await expect(resume(other)).toHaveCount(0);expect(await saved(other,null)).toBeNull();await other.close()
  await page.reload();await expect(resume(page)).toBeEnabled();expect((await saved(page,null)).guest_key).toBe(before.guest_key);await resume(page).click();await done(page);expect(proof.calls[1]).toEqual(proof.calls[0]);expect((await state(request,null,proof.ids[0])).result.saved).toBe(false)
})

test('浏览器存储不可写时保留输入，不发送创建',async({page,request})=>{
  const user=await setup(page,request);let creates=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/plan/jobs/create'))creates++})
  await page.evaluate(()=>{const original=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k.startsWith('travel.plan-creation.'))throw new DOMException('quota','QuotaExceededError');return original.call(this,k,v)}})
  const content=await publish(page,'存储故障');await expect(page.locator('.creation-message')).toContainText('无法保留本次规划提交');await expect(input(page)).toHaveValue(content);await expect(input(page)).toBeEnabled();expect(creates).toBe(0);expect(await saved(page,user)).toBeNull()
})

test('旧能力或专用接口405不退回无标识创建',async({page,request})=>{
  const user=await setup(page,request);let legacy=0,creates=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/plan/jobs'))legacy++;if(r.method()==='POST'&&r.url().endsWith('/api/plan/jobs/create'))creates++})
  await page.route('**/api/plan/jobs/capabilities',r=>r.fulfill({json:{}}));await publish(page,'旧服务');await expect(page.locator('.creation-message')).toContainText('不支持可靠提交');expect(creates).toBe(0);expect(await saved(page,user)).toBeNull()
  await page.unroute('**/api/plan/jobs/capabilities');await page.route(CREATE,r=>r.fulfill({status:405,json:{detail:'旧服务'}}));await page.getByRole('button',{name:'发送',exact:true}).click();await expect(resume(page)).toBeEnabled();await expect(page.locator('.creation-panel')).toContainText('不会退回旧创建接口');expect(creates).toBe(1);expect(legacy).toBe(0)
})

test('错UUID成功回执不清快照，真回执才接回任务',async({page,request})=>{
  const user=await setup(page,request),calls=[];let count=0
  await page.route(CREATE,async r=>{calls.push(r.request().postDataJSON());const response=await r.fetch();if(++count===1)return r.fulfill({json:{...await response.json(),request_id:randomUUID()}});await r.fulfill({response})})
  await publish(page,'正常生成');await expect(resume(page)).toBeEnabled();await expect(page.locator('.creation-panel')).toContainText('回执无法确认');expect(await saved(page,user)).not.toBeNull()
  await resume(page).click();await done(page);expect(calls[1]).toEqual(calls[0]);expect(await history(request,user)).toHaveLength(1)
})

test('停止比创建先到，迟到POST不创建任务且不污染之后的新需求',async({page,request})=>{
  const user=await setup(page,request);let release,arrived,finished,first=true,body
  const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),complete=new Promise(r=>finished=r)
  await page.route(CREATE,async r=>{if(!first)return r.continue();first=false;body=r.request().postDataJSON();arrived();await gate;const response=await r.fetch();expect(response.status()).toBe(410);await r.fulfill({response});finished()})
  try{
    await publish(page,'等待生成');await received;await stop(page).click();await expect(page.locator('.msg.ai .bubble').last()).toContainText('已取消这次生成');await expect(input(page)).toBeEnabled();expect(await saved(page,user)).toBeNull()
    release();await complete;expect(await history(request,user)).toHaveLength(0)
    await publish(page,'正常生成');await done(page);expect(await history(request,user)).toHaveLength(1);expect(body.request_id).toBeTruthy()
  }finally{release()}
})

test('创建已保存但回执迟到，停止后旧201不能恢复生成界面',async({page,request})=>{
  const user=await setup(page,request);let release,arrived,finished,jid
  const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),complete=new Promise(r=>finished=r)
  await page.route(CREATE,async r=>{const response=await r.fetch();jid=(await response.json()).job_id;arrived();await gate;await r.fulfill({response});finished()})
  try{
    await publish(page,'等待生成');await received;await stop(page).click();await expect(page.locator('.msg.ai .bubble').last()).toContainText('已取消这次生成');release();await complete
    await expect(input(page)).toBeEnabled();expect((await state(request,user,jid)).status).toBe('cancelled');expect(await history(request,user)).toHaveLength(0)
  }finally{release()}
})

test('创建和停止都丢响应，刷新继续只重复停止，不再创建',async({page,request},info)=>{
  const user=await setup(page,request),proof=await lost(page),cancels=[]
  await page.route(CANCEL,async r=>{cancels.push(r.request().postDataJSON());const response=await r.fetch();if(cancels.length===1)return r.abort('failed');await r.fulfill({response})})
  await publish(page,'等待生成');await expect(resume(page)).toBeEnabled();await stop(page).click();await expect(resume(page)).toBeEnabled();expect((await saved(page,user)).mode).toBe('cancel')
  await page.reload();await expect(page.locator('.creation-panel')).toContainText('停止提交待确认');expect((await saved(page,user)).mode).toBe('cancel');await capture(page,info,'cancel-pending.png')
  await resume(page).click();await expect(page.locator('.msg.ai .bubble').last()).toContainText('已取消这次生成');await expect(input(page)).toBeEnabled();expect(proof.calls).toHaveLength(1);expect(cancels).toHaveLength(2);expect(cancels[1]).toEqual(cancels[0]);expect(await history(request,user)).toHaveLength(0)
})

test('换号不带入未确认提交，回原账号仍可停止同次任务',async({page,request})=>{
  const user=await setup(page,request),other=await account(request);await lost(page);const content=await publish(page,'等待生成');await expect(resume(page)).toBeEnabled();const before=await saved(page,user)
  await answer(page,()=>page.getByRole('button',{name:'退出',exact:true}).click(),true,CHANGE);await login(page,other);await expect(resume(page)).toHaveCount(0);await expect(page.locator('.msg')).toHaveCount(0);expect(await saved(page,other)).toBeNull();expect(await saved(page,user)).toEqual(before)
  await page.getByRole('button',{name:'退出',exact:true}).click();await login(page,user);await expect(page.locator('.msg.user .bubble')).toContainText(content);await expect(resume(page)).toBeEnabled();await stop(page).click();await expect(input(page)).toBeEnabled();expect(await history(request,user)).toHaveLength(0)
})

test('明确放弃只清标签快照，不删除已生成历史',async({page,request})=>{
  const user=await setup(page,request),proof=await lost(page);await publish(page,'正常生成');await expect(resume(page)).toBeEnabled();await expect.poll(async()=>(await state(request,user,proof.ids[0])).status).toBe('done')
  await answer(page,()=>page.getByRole('button',{name:'放弃本标签恢复',exact:true}).click(),false,DISCARD);expect(await saved(page,user)).not.toBeNull()
  await answer(page,()=>page.getByRole('button',{name:'放弃本标签恢复',exact:true}).click(),true,DISCARD);await expect(input(page)).toBeEnabled();expect(await saved(page,user)).toBeNull();expect(await history(request,user)).toHaveLength(1);expect(proof.calls).toHaveLength(1)
})

test('新需求链接不覆盖原次待确认内容，不自动另建',async({page,request})=>{
  const user=await setup(page,request),proof=await lost(page);await publish(page,'正常生成');await expect(resume(page)).toBeEnabled();const before=await saved(page,user)
  await page.evaluate(async()=>{const {default:router}=await import('/travel/src/router/index.js');await router.push('/spots');await router.push({path:'/plan',query:{q:'新需求',auto:'1'}})})
  await expect(page.locator('.creation-panel')).toContainText('请先确认或停止上次提交');expect(await saved(page,user)).toEqual(before);expect(proof.calls).toHaveLength(1)
  await resume(page).click();await done(page);expect(proof.calls[1]).toEqual(proof.calls[0]);expect(await history(request,user)).toHaveLength(1)
})

test('损坏快照锁定提交，确认放弃前不发送任何创建',async({page,request})=>{
  const user=await setup(page,request);let creates=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/plan/jobs/create'))creates++})
  await page.evaluate(uid=>sessionStorage.setItem('travel.plan-creation.v1.'+uid,'broken'),user.id);await page.reload();await expect(page.locator('.creation-panel')).toContainText('无法恢复上次规划提交');await expect(input(page)).toBeDisabled();await expect(resume(page)).toBeDisabled();expect(creates).toBe(0)
  await answer(page,()=>page.getByRole('button',{name:'放弃本标签恢复',exact:true}).click(),true,DISCARD);await expect(input(page)).toBeEnabled();expect(await saved(page,user)).toBeNull()
})

test('已知任务过期后原标识不再创建，确认过期可结束本次恢复',async({page,request})=>{
  const user=await setup(page,request);let failing=true,createdId,creates=0
  await page.route(CREATE,async r=>{creates++;const response=await r.fetch();if(response.status()===201)createdId=(await response.json()).job_id;await r.fulfill({response})})
  await page.route('**/api/plan/jobs/*',r=>r.request().method()==='GET'&&/\/jobs\/[a-f0-9]{32}(?:\?|$)/i.test(r.request().url())&&failing?r.fulfill({status:503,json:{detail:'隔离轮询故障'}}):r.fallback())
  await publish(page,'正常生成');await expect(page.getByRole('status').filter({hasText:'正在自动重试'})).toBeVisible();await expect.poll(async()=>(await state(request,user,createdId)).status).toBe('done')
  expect((await request.post(HOME+`api/__qa/plan/expire/${createdId}`,{headers:user.auth})).status()).toBe(200);failing=false
  await expect(resume(page)).toBeEnabled();await expect(page.locator('.creation-panel')).toContainText('不存在或已过期')
  await resume(page).click();await expect(resume(page)).toBeEnabled();await expect(page.locator('.creation-panel')).toContainText('已取消或过期');expect(creates).toBe(2)
  await stop(page).click();await expect(input(page)).toBeEnabled();expect(await saved(page,user)).toBeNull();expect(await history(request,user)).toHaveLength(1);expect(creates).toBe(2)
})

test('回看旧历史不改待确认快照，找回后回原对话且旧历史不变',async({page,request})=>{
  const user=await setup(page,request)
  // Start an empty independent conversation before seeding another historical job.
  // This also invalidates the initial active-job lookup, which must not claim the fixture.
  await page.evaluate(async()=>{const {usePlanSession}=await import('/travel/src/store/planSession.js');usePlanSession().startFresh()})
  const old=await(await request.post(HOME+'api/plan/jobs/create',{headers:user.auth,data:{request_id:randomUUID(),user_input:'正常生成旧历史',messages:[{role:'user',text:'旧历史输入'},{role:'ai',text:'历史气泡原文'}]}})).json()
  await expect.poll(async()=>(await state(request,user,old.job_id)).status).toBe('done');const previous=(await history(request,user))[0]
  const proof=await lost(page),content=await publish(page,'正常生成');await expect(resume(page)).toBeEnabled();const before=await saved(page,user)
  await page.evaluate(async()=>{const {default:router}=await import('/travel/src/router/index.js');await router.push('/spots');await router.push('/plan')})
  await page.getByRole('button',{name:/历史方案/}).click();await page.locator('.hist-item').last().click();await expect(page.locator('.msg.ai .bubble')).toHaveCount(1);await expect(page.locator('.msg.ai .bubble')).toHaveText('历史气泡原文')
  expect(await saved(page,user)).toEqual(before);await resume(page).click();await done(page);await expect(page.locator('.msg.user .bubble')).toContainText(content)
  expect(proof.calls[1]).toEqual(proof.calls[0]);expect(await history(request,user)).toHaveLength(2)
  const snapshot=await(await request.get(HOME+`api/plan/${previous.id}`,{headers:user.auth})).json();expect(snapshot.chat.at(-1).text).toBe('历史气泡原文')
})
