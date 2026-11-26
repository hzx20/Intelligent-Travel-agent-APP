import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'
const ORIGIN='http://127.0.0.1:5264',HOME=ORIGIN+'/travel/',PASSWORD='isolated-spot-writes-password'
const LEAVE='本页还有未保存输入、待确认结果或正在进行的操作。离开后本页内容可能丢失，确定离开吗？'
const DISCARD='这次评论可能已经发布。放弃只清除本标签重试信息，不删除评论；请先核对评论列表，确定放弃吗？'
async function localOnly(page){await page.route('**/*',r=>new URL(r.request().url()).origin===ORIGIN?r.continue():r.abort())}
async function account(request){const username='sw_'+randomUUID().replaceAll('-','').slice(0,16);expect((await request.post(HOME+'api/auth/register',{data:{username,password:PASSWORD}})).status()).toBe(201);const r=await request.post(HOME+'api/auth/login',{data:{username,password:PASSWORD}}),data=await r.json();const auth={Authorization:'Bearer '+data.token};const me=await (await request.get(HOME+'api/auth/me',{headers:auth})).json();return {username,auth,id:me.id}}
async function login(page,user){await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click();const d=page.getByRole('dialog',{name:'登录',exact:true});await d.getByRole('textbox',{name:'用户名',exact:true}).fill(user.username);await d.getByLabel('密码',{exact:true}).fill(PASSWORD);await d.getByRole('button',{name:'登录',exact:true}).last().click();await expect(d).toHaveCount(0)}
async function setup(page,request){await localOnly(page);const user=await account(request);const spots=(await (await request.get(HOME+'api/spots?page_size=10')).json()).items;const first=spots.find(s=>s.name==='隔离景点一'),second=spots.find(s=>s.name==='隔离景点二');await page.goto(HOME);await login(page,user);await page.goto(HOME+`spots/${first.id}`);await expect(page.getByRole('heading',{name:first.name,exact:true})).toBeVisible();return {user,first,second}}
const text=p=>p.getByRole('textbox',{name:'游览评论',exact:true}),send=p=>p.getByRole('button',{name:'发布评论',exact:true}),resume=p=>p.getByRole('button',{name:'继续这次发布',exact:true}),fav=p=>p.getByRole('button',{name:/♡ 收藏/}),liked=p=>p.getByRole('button',{name:/❤ 已收藏/})
const key=(user,spot)=>`travel.comment-creation.v1.${user.id}.${spot.id}`
async function detail(request,user,spot){return (await request.get(HOME+`api/spots/${spot.id}`,{headers:user.auth})).json()}
async function pending(page,user,spot){return page.evaluate(k=>JSON.parse(sessionStorage.getItem(k)),key(user,spot))}
async function answer(page,trigger,accept,message=LEAVE){const dialog=page.waitForEvent('dialog').then(async d=>{expect(d.message()).toBe(message);if(accept)await d.accept();else await d.dismiss()});await Promise.all([dialog,trigger()])}
async function navigate(page,spot){await page.evaluate(async id=>{const {default:router}=await import('/travel/src/router/index.js');await router.push(`/spots/${id}`)},spot.id)}
async function publish(page,content){await text(page).fill(content);await send(page).click()}
async function lostComment(page,spot){const attempts=[];await page.route(`**/api/spots/${spot.id}/comments/create`,async r=>{attempts.push(r.request().postDataJSON());const response=await r.fetch();if(attempts.length===1){expect(response.status()).toBe(201);await r.abort('failed')}else{expect(response.status()).toBe(200);await r.fulfill({response})}});return attempts}
async function screenshot(page,info,name){
  const isPending=name.includes('pending'),target=isPending?page.getByRole('button',{name:'放弃本次发布',exact:true}):page.locator('.editor')
  await target.evaluate(el=>el.scrollIntoView({block:'center'}))
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  if(isPending){
    const box=await target.boundingBox(),mobileNav=page.getByRole('navigation',{name:'主要导航',exact:true})
    const nav=await mobileNav.isVisible()?await mobileNav.boundingBox():null
    expect(box.y).toBeGreaterThanOrEqual(56)
    if(nav)expect(box.y+box.height).toBeLessThanOrEqual(nav.y)
  }
  await page.screenshot({path:info.outputPath(name)})
}

test('提交丢响应后同标识恢复，同页核对不增加第二条评论',async({page,request},info)=>{
  const {user,first}=await setup(page,request),attempts=await lostComment(page,first),content='可靠评论 '+randomUUID()
  await page.getByRole('button',{name:'评分 4 星',exact:true}).click();await publish(page,content);await expect(resume(page)).toBeEnabled();await expect(text(page)).toHaveAttribute('readonly','')
  const saved=await pending(page,user,first);expect(saved.payload).toEqual({content,rating:4});expect(saved.request_id).toBe(attempts[0].request_id)
  await page.getByRole('button',{name:'核对收藏和评论',exact:true}).click();await expect(page.locator('.comments').getByText(content,{exact:true})).toHaveCount(1);expect(attempts).toHaveLength(1)
  await screenshot(page,info,'comment-pending.png');await resume(page).click();await expect(page.getByRole('status').filter({hasText:'发布成功'})).toBeVisible()
  expect(attempts[1]).toEqual(attempts[0]);await expect(page.locator('.comments').getByText(content,{exact:true})).toHaveCount(1);expect(await pending(page,user,first)).toBeNull();await expect(text(page)).toHaveValue('')
  expect((await detail(request,user,first)).comments.filter(c=>c.content===content)).toHaveLength(1);await screenshot(page,info,'comment-recovered.png')
})

test('刷新后恢复同一评论正文评分和标识，不重新创建',async({page,request})=>{
  const {user,first}=await setup(page,request),attempts=await lostComment(page,first),content='刷新找回 '+randomUUID();await publish(page,content);await expect(resume(page)).toBeEnabled()
  const before=await pending(page,user,first);const reloading=page.waitForEvent('dialog').then(async d=>{expect(d.type()).toBe('beforeunload');await d.accept()});await Promise.all([reloading,page.reload()])
  await expect(text(page)).toHaveValue(content);await expect(resume(page)).toBeEnabled();expect(await pending(page,user,first)).toEqual(before)
  await resume(page).click();await expect(send(page)).toBeEnabled();expect(attempts).toHaveLength(2);expect(attempts[1]).toEqual(attempts[0]);expect((await detail(request,user,first)).comments.filter(c=>c.content===content)).toHaveLength(1)
})

test('浏览器存储不可写时保留输入且不发送创建请求',async({page,request})=>{
  const {user,first}=await setup(page,request);let writes=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/comments'))writes++})
  await page.evaluate(()=>{const original=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k.startsWith('travel.comment-creation.'))throw new DOMException('quota','QuotaExceededError');return original.call(this,k,v)}})
  await publish(page,'存储失败输入');await expect(page.getByRole('status').filter({hasText:'无法保留本次评论信息'})).toBeVisible();expect(writes).toBe(0);await expect(text(page)).toHaveValue('存储失败输入');await expect(text(page)).not.toHaveAttribute('readonly','');expect(await pending(page,user,first)).toBeNull()
})

test('跨景点保留各自已提交评论，明确放弃不删除已经发布的记录',async({page,request})=>{
  const {user,first,second}=await setup(page,request),attempts=await lostComment(page,first),content='隔离景点评论 '+randomUUID();await publish(page,content);await expect(resume(page)).toBeEnabled()
  await answer(page,()=>navigate(page,second),true);await expect(text(page)).toHaveValue('');await expect(resume(page)).toHaveCount(0);expect(await pending(page,user,first)).not.toBeNull();expect(await pending(page,user,second)).toBeNull()
  await navigate(page,first);await expect(text(page)).toHaveValue(content)
  await answer(page,()=>page.getByRole('button',{name:'放弃本次发布',exact:true}).click(),false,DISCARD);expect(await pending(page,user,first)).not.toBeNull();await expect(text(page)).toHaveAttribute('readonly','')
  await answer(page,()=>page.getByRole('button',{name:'放弃本次发布',exact:true}).click(),true,DISCARD);expect(await pending(page,user,first)).toBeNull();await expect(text(page)).toHaveValue(content);await expect(text(page)).not.toHaveAttribute('readonly','');expect(attempts).toHaveLength(1);expect((await detail(request,user,first)).comments.filter(c=>c.content===content)).toHaveLength(1)
})

test('同标签换号不带入旧账号待确认评论，切回才恢复',async({page,request})=>{
  const {user,first}=await setup(page,request),other=await account(request);await lostComment(page,first);const content='账号专属评论 '+randomUUID();await publish(page,content);await expect(resume(page)).toBeEnabled()
  await answer(page,()=>page.getByRole('button',{name:'退出',exact:true}).click(),true);await login(page,other);await expect(text(page)).toHaveValue('');await expect(resume(page)).toHaveCount(0);expect(await pending(page,other,first)).toBeNull();expect(await pending(page,user,first)).not.toBeNull()
  await page.getByRole('button',{name:'退出',exact:true}).click();await login(page,user);await expect(text(page)).toHaveValue(content);await expect(resume(page)).toBeEnabled()
})

test('旧服务不支持可靠评论时不回退旧接口',async({page,request})=>{
  const {user,first}=await setup(page,request);let writes=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/comments'))writes++})
  await page.route('**/api/spots/write-capabilities',r=>r.fulfill({status:200,json:{}}));await publish(page,'旧服务输入');await expect(page.getByRole('status').filter({hasText:'不支持可靠评论'})).toBeVisible();expect(writes).toBe(0);expect(await pending(page,user,first)).toBeNull()
  await page.route('**/api/spots/write-capabilities',r=>r.fulfill({status:200,json:{idempotent_comments:true}}));await page.route(`**/api/spots/${first.id}/comments/create`,r=>r.fulfill({status:405,json:{detail:'旧服务'}}))
  await send(page).click();await expect(page.getByRole('status').filter({hasText:'不会退回旧发布接口'})).toBeVisible();expect(writes).toBe(1);expect(await pending(page,user,first)).not.toBeNull();await expect(resume(page)).toBeEnabled()
})

test('错误成功回执保留原标识，真正回执才能清除输入',async({page,request})=>{
  const {user,first}=await setup(page,request),attempts=[];let count=0;const content='回执校验 '+randomUUID()
  await page.route(`**/api/spots/${first.id}/comments/create`,async r=>{attempts.push(r.request().postDataJSON());const response=await r.fetch();if(++count===1)return r.fulfill({status:200,json:{...await response.json(),request_id:randomUUID()}});return r.fulfill({response})})
  await publish(page,content);await expect(page.getByRole('status').filter({hasText:'评论结果无法确认'})).toBeVisible();expect(await pending(page,user,first)).not.toBeNull();await expect(text(page)).toHaveValue(content)
  await resume(page).click();await expect(text(page)).toHaveValue('');expect(attempts[1]).toEqual(attempts[0]);expect((await detail(request,user,first)).comments.filter(c=>c.content===content)).toHaveLength(1)
})

test('损坏或错账号快照不发布，明确放弃后才解除锁定',async({page,request})=>{
  const {user,first}=await setup(page,request);let writes=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().includes('/comments'))writes++})
  await page.evaluate(k=>sessionStorage.setItem(k,JSON.stringify({schema:1,user_id:-1,spot_id:1})),key(user,first));await page.reload()
  await expect(page.locator('.comment-pending')).toContainText('不一致');await expect(send(page)).toBeDisabled();await expect(text(page)).toHaveAttribute('readonly','');expect(writes).toBe(0)
  await answer(page,()=>page.getByRole('button',{name:'放弃本次发布',exact:true}).click(),true,DISCARD);await expect(send(page)).toBeEnabled();expect(await pending(page,user,first)).toBeNull()
})

test('收藏丢响应后继续相同目标版本，不反转收藏',async({page,request},info)=>{
  const {user,first}=await setup(page,request),attempts=[]
  await page.route(`**/api/spots/${first.id}/favorite-state`,async r=>{attempts.push(r.request().postDataJSON());const response=await r.fetch();expect(response.status()).toBe(200);if(attempts.length===1)return r.abort('failed');return r.fulfill({response})})
  await fav(page).click();await expect(page.getByRole('button',{name:'继续这次收藏操作',exact:true})).toBeEnabled();await expect(fav(page)).toBeDisabled()
  await page.getByRole('button',{name:'继续这次收藏操作',exact:true}).click();await expect(liked(page)).toBeEnabled();expect(attempts).toEqual([{favorited:true,version:0},{favorited:true,version:0}])
  expect((await detail(request,user,first)).favorite_version).toBe(1);await page.locator('.meta').scrollIntoViewIfNeeded();await page.screenshot({path:info.outputPath('favorite-recovered.png')})
})

test('两个标签同版本收藏不重复，旧相反决定409后需核对',async({page,request,context})=>{
  const {user,first}=await setup(page,request),other=await context.newPage();await localOnly(other);await other.goto(HOME+`spots/${first.id}`);await expect(fav(other)).toBeEnabled()
  await fav(page).click();await expect(liked(page)).toBeEnabled();await fav(other).click();await expect(liked(other)).toBeEnabled();expect((await detail(request,user,first)).favorite_version).toBe(1)
  // First tab is still at version 1 after the other tab changes away and back.
  await liked(other).click();await expect(fav(other)).toBeEnabled();await fav(other).click();await expect(liked(other)).toBeEnabled()
  await liked(page).click();await expect(page.getByRole('alert')).toContainText('收藏状态已更新');await expect(page.getByRole('button',{name:'继续这次收藏操作',exact:true})).toBeDisabled()
  expect((await detail(request,user,first)).favorite_version).toBe(3);await page.getByRole('button',{name:'核对收藏和评论',exact:true}).click();await expect(liked(page)).toBeEnabled();await liked(page).click();await expect(fav(page)).toBeEnabled();expect((await detail(request,user,first)).favorite_version).toBe(4);await other.close()
})

test('旧收藏服务缺版本或拒绝PUT，不调用无条件切换接口',async({page,request})=>{
  const {first}=await setup(page,request);let writes=0,legacy=0;page.on('request',r=>{if(r.method()==='PUT')writes++;if(r.method()==='POST'&&r.url().endsWith('/favorite'))legacy++})
  await page.route(`**/api/spots/${first.id}`,async r=>{const response=await r.fetch(),data=await response.json();delete data.favorite_version;await r.fulfill({json:data})});await page.reload();await fav(page).click();await expect(page.getByRole('alert')).toContainText('不支持可靠收藏');expect(writes).toBe(0)
  await page.unroute(`**/api/spots/${first.id}`);await page.reload();await page.route(`**/api/spots/${first.id}/favorite-state`,r=>r.fulfill({status:405,json:{detail:'旧服务'}}));await fav(page).click();await expect(page.getByRole('alert')).toContainText('无法可靠保存收藏');expect(writes).toBe(1);expect(legacy).toBe(0)
})

test('收藏坏的成功回执不显示成功，原目标重试可恢复',async({page,request})=>{
  const {user,first}=await setup(page,request);let count=0
  await page.route(`**/api/spots/${first.id}/favorite-state`,async r=>{const response=await r.fetch();if(++count===1)return r.fulfill({status:200,json:{favorited:false,favorite_count:0,favorite_version:1}});return r.fulfill({response})})
  await fav(page).click();await expect(page.getByRole('alert')).toContainText('收藏结果无法确认');await expect(fav(page)).toBeDisabled();await page.getByRole('button',{name:'继续这次收藏操作',exact:true}).click();await expect(liked(page)).toBeEnabled();expect((await detail(request,user,first)).favorite_version).toBe(1)
})
