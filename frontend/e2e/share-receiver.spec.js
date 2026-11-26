import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'
const ORIGIN='http://127.0.0.1:5264',HOME=ORIGIN+'/travel/',PASSWORD='isolated-share-receiver-password'
const LEAVE='邀请结果尚未核对。离开或切换账号不会撤销已提交的加入请求；之后可用原账号和邀请链接核对。确定继续？'
async function account(request){const username='recv_'+randomUUID().replaceAll('-','').slice(0,16);expect((await request.post(HOME+'api/auth/register',{data:{username,password:PASSWORD}})).status()).toBe(201);const result=await(await request.post(HOME+'api/auth/login',{data:{username,password:PASSWORD}})).json();return {username,id:result.user.id,auth:{Authorization:'Bearer '+result.token}}}
async function login(page,u){await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click();const d=page.getByRole('dialog',{name:'登录',exact:true});await d.getByRole('textbox',{name:'用户名',exact:true}).fill(u.username);await d.getByLabel('密码',{exact:true}).fill(PASSWORD);await d.getByRole('button',{name:'登录',exact:true}).last().click();await expect(d).toHaveCount(0)}
async function seed(request,title='合成分享旅行'){
  const owner=await account(request),editor=await account(request)
  let trip=await(await request.post(HOME+'api/trips',{headers:owner.auth,data:{title,days:2}})).json()
  trip=await(await request.post(HOME+`api/trips/${trip.id}/days/${trip.days[0].id}/items`,{headers:owner.auth,data:{version:trip.version,name:'合成公开散步安排',note:'合成公开备注'}})).json()
  await request.post(HOME+`api/trips/${trip.id}/reservations`,{headers:owner.auth,data:{version:trip.version,title:'不公开的合成酒店',reference:'PRIVATE-RECEIVER-QA'}})
  const links={}
  for(const mode of ['view','edit'])links[mode]=await(await request.post(HOME+`api/trips/${trip.id}/sharing/links`,{headers:owner.auth,data:{mode}})).json()
  return {owner,editor,trip,links,url:mode=>HOME+(mode==='view'?'share':'invite')+'#'+links[mode].token}
}
async function setup(page,url){await page.route('**/*',r=>new URL(r.request().url()).origin===ORIGIN?r.fallback():r.abort());await page.goto(url)}
async function answer(page,action,accept,type='confirm'){const done=page.waitForEvent('dialog').then(async d=>{expect(d.type()).toBe(type);if(type==='confirm')expect(d.message()).toBe(LEAVE);if(accept)await d.accept();else await d.dismiss()});await Promise.all([done,action()])}
async function navigate(page,url){await page.evaluate(async path=>{const {default:router}=await import('/travel/src/router/index.js');await router.push(path)},new URL(url).pathname.replace('/travel','')+new URL(url).hash)}
async function capture(page,info,name,target){if(target)await target.evaluate(el=>el.scrollIntoView({block:'center'}));await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);await page.screenshot({path:info.outputPath(name)})}
async function members(request,s){return (await(await request.get(HOME+`api/trips/${s.trip.id}/sharing`,{headers:s.owner.auth})).json()).members}

test('只读刷新503和坏200保留旧行程，撤销后清空，私有预订不进入响应',async({page,request},info)=>{
  const s=await seed(request);const received=page.waitForResponse(r=>r.url().endsWith('/api/trip-sharing/view'));await setup(page,s.url('view'))
  const data=await(await received).json();expect(JSON.stringify(data)).not.toContain('PRIVATE-RECEIVER-QA');expect(data).not.toHaveProperty('reservations')
  await expect(page.getByRole('heading',{name:'合成公开散步安排',exact:true})).toBeVisible()
  await page.route('**/api/trip-sharing/view',r=>r.fulfill({status:503,json:{detail:'隔离暂时不可用'}}));await page.getByRole('button',{name:'刷新分享行程',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('上次成功读取');await expect(page.getByRole('heading',{name:'合成公开散步安排',exact:true})).toBeVisible();await capture(page,info,'shared-retained.png',page.getByRole('alert'))
  await page.unroute('**/api/trip-sharing/view');await page.route('**/api/trip-sharing/view',r=>r.fulfill({json:{title:'错误内容',days:null}}));await page.getByRole('button',{name:'重试',exact:true}).click();await expect(page.getByRole('heading',{name:'合成分享旅行',exact:true})).toBeVisible()
  await page.unroute('**/api/trip-sharing/view');await request.delete(HOME+`api/trips/${s.trip.id}/sharing/links/${s.links.view.id}`,{headers:s.owner.auth})
  await page.getByRole('button',{name:'重试',exact:true}).click();await expect(page.getByRole('alert')).toContainText('已撤销或已过期');await expect(page.getByRole('heading',{name:'合成分享旅行',exact:true})).toHaveCount(0)
})

test('分享切换链接拒绝迟到旧响应，日期键盘跳转不覆盖令牌',async({page,request},info)=>{
  const first=await seed(request,'旧合成旅行'),second=await seed(request,'新合成旅行');let release,arrived,complete
  const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),finished=new Promise(r=>complete=r)
  await page.route('**/api/trip-sharing/view',async r=>{if(r.request().postDataJSON().token!==first.links.view.token)return r.fallback();const response=await r.fetch();arrived();await gate;await r.fulfill({response});complete()})
  try{await setup(page,first.url('view'));await received;await navigate(page,second.url('view'));await expect(page.getByRole('heading',{name:'新合成旅行',exact:true})).toBeVisible();release();await finished
    await expect(page.getByRole('heading',{name:'旧合成旅行',exact:true})).toHaveCount(0)
    const day=page.getByRole('navigation',{name:'分享行程日期'}).getByRole('button',{name:'第 2 天',exact:true});await day.focus();await page.keyboard.press('Enter');await expect(page.locator('#shared-day-2')).toBeFocused();expect(new URL(page.url()).hash).toBe('#'+second.links.view.token)
    await capture(page,info,'shared-day-focus.png',page.locator('#shared-day-2'))
  }finally{release()}
})

test('不完整链接不发送请求，完整链接初次失败可单独重试',async({page,request})=>{
  const s=await seed(request);let reads=0;page.on('request',r=>{if(r.url().endsWith('/api/trip-sharing/view'))reads++})
  await setup(page,HOME+'share#broken');await expect(page.getByRole('alert')).toContainText('链接不完整');expect(reads).toBe(0)
  await page.route('**/api/trip-sharing/view',r=>r.fulfill({status:503,json:{detail:'隔离故障'}}));await navigate(page,s.url('view'));await expect(page.getByRole('button',{name:'重试',exact:true})).toBeVisible()
  await page.unroute('**/api/trip-sharing/view');await page.getByRole('button',{name:'重试',exact:true}).click();await expect(page.getByRole('heading',{name:'合成分享旅行',exact:true})).toBeVisible();expect(reads).toBe(2)
})

test('核对未加入不授予权限，用户显式接受后只有一个成员',async({page,request})=>{
  const s=await seed(request);await setup(page,s.url('edit'));await login(page,s.editor);let accepts=0;page.on('request',r=>{if(r.url().endsWith('/api/trip-sharing/accept'))accepts++})
  await page.getByRole('button',{name:'核对是否已加入',exact:true}).click();await expect(page.getByRole('status')).toContainText('尚未加入');expect(await members(request,s)).toHaveLength(0);expect(accepts).toBe(0)
  await page.getByRole('button',{name:'接受邀请并加入',exact:true}).click();await expect(page.getByRole('heading',{name:'合成分享旅行',exact:true})).toBeVisible();expect(await members(request,s)).toHaveLength(1);expect(accepts).toBe(1)
})

test('接受已提交但丢响应，核对只读找回，显式打开旅行',async({page,request},info)=>{
  const s=await seed(request);await setup(page,s.url('edit'));await login(page,s.editor);let accepts=0
  await page.route('**/api/trip-sharing/accept',async r=>{accepts++;await r.fetch();await r.abort('failed')})
  await page.getByRole('button',{name:'接受邀请并加入',exact:true}).click();await expect(page.getByRole('alert')).toContainText('无法确认是否已加入')
  await expect(page.getByRole('button',{name:'接受邀请并加入',exact:true})).toHaveCount(0);await capture(page,info,'invite-pending.png',page.getByRole('button',{name:'核对是否已加入',exact:true}))
  await page.getByRole('button',{name:'核对是否已加入',exact:true}).click();await expect(page.getByRole('link',{name:'打开已确认旅行 →',exact:true})).toBeVisible();expect(new URL(page.url()).pathname).toBe('/travel/invite');expect(accepts).toBe(1)
  await capture(page,info,'invite-confirmed.png',page.getByRole('link',{name:'打开已确认旅行 →',exact:true}));await page.getByRole('link',{name:'打开已确认旅行 →',exact:true}).click();await expect(page.getByRole('heading',{name:'合成分享旅行',exact:true})).toBeVisible();expect(await members(request,s)).toHaveLength(1)
})

test('未送达接受，核对后显式继续，保留原令牌与账号',async({page,request})=>{
  const s=await seed(request);await setup(page,s.url('edit'));await login(page,s.editor);const bodies=[]
  await page.route('**/api/trip-sharing/accept',async r=>{bodies.push(r.request().postDataJSON());if(bodies.length===1)return r.abort('failed');await r.continue()})
  await page.getByRole('button',{name:'接受邀请并加入',exact:true}).click();await expect(page.getByRole('alert')).toBeVisible();await page.getByRole('button',{name:'核对是否已加入',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'当前账号尚未加入'})).toBeVisible();expect(await members(request,s)).toHaveLength(0)
  await page.getByRole('button',{name:'确认继续加入',exact:true}).click();await expect(page.getByRole('heading',{name:'合成分享旅行',exact:true})).toBeVisible();expect(bodies).toEqual([{token:s.links.edit.token},{token:s.links.edit.token}]);expect(await members(request,s)).toHaveLength(1)
})

test('错误200回执不跳页，核对实际权限后恢复',async({page,request})=>{
  const s=await seed(request);await setup(page,s.url('edit'));await login(page,s.editor)
  await page.route('**/api/trip-sharing/accept',async r=>{const response=await r.fetch();await r.fulfill({json:{...await response.json(),trip_id:'../me'}})})
  await page.getByRole('button',{name:'接受邀请并加入',exact:true}).click();await expect(page.getByRole('alert')).toBeVisible();expect(new URL(page.url()).pathname).toBe('/travel/invite')
  await page.getByRole('button',{name:'核对是否已加入',exact:true}).click();await expect(page.getByRole('link',{name:'打开已确认旅行 →',exact:true})).toHaveAttribute('href','/travel/trips/'+s.trip.id)
})

test('接受后邀请撤销仍可核对既有权限，成员移除后清除打开入口',async({page,request})=>{
  const s=await seed(request);await setup(page,s.url('edit'));await login(page,s.editor)
  await page.route('**/api/trip-sharing/accept',async r=>{await r.fetch();await r.abort('failed')});await page.getByRole('button',{name:'接受邀请并加入',exact:true}).click();await expect(page.getByRole('alert')).toBeVisible()
  await request.delete(HOME+`api/trips/${s.trip.id}/sharing/links/${s.links.edit.id}`,{headers:s.owner.auth});await page.getByRole('button',{name:'核对是否已加入',exact:true}).click();await expect(page.getByRole('link',{name:'打开已确认旅行 →',exact:true})).toBeVisible()
  const member=(await members(request,s))[0];await request.delete(HOME+`api/trips/${s.trip.id}/sharing/members/${member.user_id}`,{headers:s.owner.auth});await page.getByRole('button',{name:'重新核对权限',exact:true}).click();await expect(page.getByRole('alert')).toContainText('已撤销或已过期');await expect(page.getByRole('link',{name:'打开已确认旅行 →',exact:true})).toHaveCount(0);expect(await members(request,s)).toHaveLength(0)
})

test('待核对保护离开刷新和退出，旧服务不支持核对不重发',async({page,request},info)=>{
  const s=await seed(request);await setup(page,s.url('edit'));await login(page,s.editor);let accepts=0
  await page.route('**/api/trip-sharing/accept',r=>{accepts++;return r.abort('failed')});await page.getByRole('button',{name:'接受邀请并加入',exact:true}).click();await expect(page.getByRole('alert')).toBeVisible()
  await answer(page,()=>page.getByRole('link',{name:'← 旅行首页',exact:true}).click(),false);await answer(page,()=>page.evaluate(()=>location.reload()),false,'beforeunload');await answer(page,()=>page.getByRole('button',{name:'退出',exact:true}).click(),false)
  await page.route('**/api/trip-sharing/invitation-status',r=>r.fulfill({status:404,json:{detail:'Not Found'}}));await page.getByRole('button',{name:'核对是否已加入',exact:true}).click();await expect(page.getByRole('alert')).toContainText('服务暂不支持核对');expect(accepts).toBe(1);await expect(page.getByRole('button',{name:'确认继续加入',exact:true})).toHaveCount(0)
  await page.setViewportSize({width:320,height:740});await capture(page,info,'invite-unsupported-narrow.png',page.getByRole('alert'))
})

test('换号后迟到接受不跳页或污染新账号，原账号实际已加入',async({page,request})=>{
  const s=await seed(request),other=await account(request);await setup(page,s.url('edit'));await login(page,s.editor)
  let release,arrived,complete;const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),finished=new Promise(r=>complete=r)
  await page.route('**/api/trip-sharing/accept',async r=>{const response=await r.fetch();arrived();await gate;await r.fulfill({response});complete()})
  try{await page.getByRole('button',{name:'接受邀请并加入',exact:true}).click();await received;await answer(page,()=>page.getByRole('button',{name:'退出',exact:true}).click(),true);await login(page,other);await page.getByRole('button',{name:'核对是否已加入',exact:true}).click();await expect(page.getByRole('status')).toContainText('尚未加入');release();await finished
    await expect(page.getByRole('link',{name:'打开已确认旅行 →',exact:true})).toHaveCount(0);expect(new URL(page.url()).pathname).toBe('/travel/invite')
    expect((await request.get(HOME+'api/trips/'+s.trip.id,{headers:s.editor.auth})).status()).toBe(200);expect((await request.get(HOME+'api/trips/'+s.trip.id,{headers:other.auth})).status()).toBe(404)
  }finally{release()}
})
