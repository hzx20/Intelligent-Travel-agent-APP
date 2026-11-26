import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'

const ORIGIN='http://127.0.0.1:5264',HOME=ORIGIN+'/travel/',PASSWORD='isolated-guide-editor-password'
const DEPARTURE='攻略还有未保存的输入或待确认的提交。离开后本页内容会丢失，确定离开吗？'
const RETRY='再次保存仍使用原版本；若云端已更新会拒绝覆盖。确定继续编辑吗？'
const ABANDON='这次尝试可能已经创建攻略。放弃只清除本标签的重试信息，不删除云端攻略；请先核对，确定放弃吗？'
async function setup(page,request,existing=false){
  await page.route('**/*',route=>new URL(route.request().url()).origin===ORIGIN?route.continue():route.abort())
  const username='ged_'+randomUUID().replaceAll('-','').slice(0,16)
  expect((await request.post(HOME+'api/auth/register',{data:{username,password:PASSWORD}})).status()).toBe(201)
  const signed=await request.post(HOME+'api/auth/login',{data:{username,password:PASSWORD}})
  const auth={Authorization:'Bearer '+(await signed.json()).token}
  let id
  if(existing){
    const created=await request.post(HOME+'api/guides',{headers:auth,data:{title:'原有攻略',content:'原有正文',city:'杭州',is_draft:true,images:[],spot_ids:[]}})
    expect(created.status()).toBe(201);id=(await created.json()).id
  }
  await page.goto(HOME)
  await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click()
  const login=page.getByRole('dialog',{name:'登录',exact:true})
  await login.getByRole('textbox',{name:'用户名',exact:true}).fill(username)
  await login.getByLabel('密码',{exact:true}).fill(PASSWORD)
  await login.getByRole('button',{name:'登录',exact:true}).last().click()
  await expect(login).toHaveCount(0)
  await page.goto(HOME+(id?`guides/${id}/edit`:'guides/new'))
  await expect(page.getByRole('textbox',{name:'攻略标题',exact:true})).toBeVisible()
  return {auth,id}
}
const title=page=>page.getByRole('textbox',{name:'攻略标题',exact:true})
const content=page=>page.getByRole('textbox',{name:'攻略正文',exact:true})
const home=page=>page.locator('nav .logo').click()
async function fill(page){await title(page).fill('待保存攻略');await content(page).fill('只在本页的正文')}
async function answer(page,trigger,accept,message=DEPARTURE,type='confirm'){
  const handled=page.waitForEvent('dialog').then(async d=>{
    const actual={type:d.type(),message:d.message()}
    if(accept)await d.accept();else await d.dismiss()
    expect(actual.type).toBe(type);if(type==='confirm')expect(actual.message).toBe(message)
  })
  await Promise.all([handled,trigger()])
}
async function guides(request,auth){const response=await request.get(HOME+'api/me/guides',{headers:auth});expect(response.status()).toBe(200);return response.json()}

test('新攻略离开取消保留全部输入，确认后离开且未发布',async({page,request})=>{
  const {auth}=await setup(page,request)
  await fill(page);await page.getByRole('textbox',{name:'待添加图片链接',exact:true}).fill('https://example.invalid/not-added.png')
  await answer(page,()=>home(page),false)
  await expect(title(page)).toHaveValue('待保存攻略');await expect(content(page)).toHaveValue('只在本页的正文')
  await expect(page.getByRole('textbox',{name:'待添加图片链接',exact:true})).toHaveValue('https://example.invalid/not-added.png')
  await answer(page,()=>home(page),true);await expect(page).toHaveURL(HOME)
  expect(await guides(request,auth)).toHaveLength(0)
})

test('原攻略未修改可直接离开，修改后刷新取消保留、确认恢复云端原文',async({page,request})=>{
  const {id}=await setup(page,request,true)
  await expect(title(page)).toHaveValue('原有攻略')
  let unexpected=0;const reject=d=>{unexpected++;d.dismiss()};page.on('dialog',reject)
  await home(page);await expect(page).toHaveURL(HOME);page.off('dialog',reject);expect(unexpected).toBe(0)
  await page.goto(HOME+`guides/${id}/edit`);await content(page).fill('未保存修改')
  await answer(page,()=>page.evaluate(()=>window.location.reload()),false,null,'beforeunload')
  await expect(content(page)).toHaveValue('未保存修改')
  await answer(page,()=>page.reload(),true,null,'beforeunload')
  await expect(content(page)).toHaveValue('原有正文')
})

test('退出账号取消保留攻略，确认后清除本页内容',async({page,request})=>{
  await setup(page,request);await fill(page)
  const logout=()=>page.getByRole('button',{name:'退出',exact:true}).click()
  await answer(page,logout,false);await expect(content(page)).toHaveValue('只在本页的正文')
  await answer(page,logout,true);await expect(title(page)).toHaveCount(0)
  await expect(page.getByText('登录后编写攻略。',{exact:false})).toBeVisible()
})

test('提交期间禁用表单和重复操作，草稿成功保存后正常离开',async({page,request})=>{
  const {auth}=await setup(page,request);await fill(page)
  let release,arrived;const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r);let writes=0
  await page.route('**/travel/api/guides/create',async route=>{
    if(route.request().method()!=='POST')return route.continue()
    writes++;arrived();await gate;await route.continue()
  })
  try{
    await page.getByRole('button',{name:'存草稿',exact:true}).click();await received
    await expect(title(page)).toBeDisabled();await expect(content(page)).toBeDisabled()
    await expect(page.getByRole('button',{name:'提交中…',exact:true})).toBeDisabled()
    release();await expect(page).toHaveURL(HOME+'me')
    expect(writes).toBe(1);const saved=await guides(request,auth);expect(saved).toHaveLength(1);expect(saved[0].is_draft).toBe(true)
  }finally{release()}
})

test('编辑读取失败不显示空白编辑器，重试成功才允许保存',async({page,request})=>{
  const {id}=await setup(page,request,true)
  const fail=route=>route.fulfill({status:503,json:{detail:'隔离读取故障'}})
  await page.route(`**/travel/api/guides/${id}`,fail)
  await page.reload();await expect(page.getByRole('alert')).toContainText('隔离读取故障')
  await expect(title(page)).toHaveCount(0);await expect(page.getByRole('button',{name:'保存修改',exact:true})).toHaveCount(0)
  await page.unroute(`**/travel/api/guides/${id}`,fail)
  await page.getByRole('button',{name:'重新加载攻略',exact:true}).click()
  await expect(title(page)).toHaveValue('原有攻略');await expect(content(page)).toHaveValue('原有正文')
})

for(const existing of [false,true])test(`${existing?'编辑':'新建'}真实提交后丢失响应暂停再次提交，保留输入并提供独立云端核对`,async({page,request,context},testInfo)=>{
  const {auth,id}=await setup(page,request,existing);await fill(page);let writes=0
  await page.route(existing?`**/travel/api/guides/${id}/content`:'**/travel/api/guides/create',async route=>{
    if(route.request().method()!==(existing?'PATCH':'POST'))return route.continue()
    writes++;const response=await route.fetch()
    if(writes===1){expect(response.status()).toBe(existing?200:201);return route.abort('failed')}
    await route.fulfill({response})
  })
  const save=page.getByRole('button',{name:existing?'保存修改':'发布攻略',exact:true})
  await save.click()
  await expect(page.getByRole('status')).toContainText(existing?'保存结果需核对':'按账号保留在此标签页')
  await expect(title(page)).toHaveValue('待保存攻略');await expect(content(page)).toHaveValue('只在本页的正文')
  await expect(save).toBeDisabled()
  const review=page.getByRole('link',{name:'在新标签页核对云端攻略',exact:true})
  const destination=existing?`guides/${id}`:'me'
  await expect(review).toHaveAttribute('href','/travel/'+destination);await expect(review).toHaveAttribute('target','_blank')
  const popupPromise=context.waitForEvent('page');await review.click();const popup=await popupPromise
  await popup.waitForLoadState();await expect(popup).toHaveURL(HOME+destination);await popup.close()
  expect(await guides(request,auth)).toHaveLength(1)
  await answer(page,()=>page.getByRole('button',{name:existing?'继续编辑原版本':'放弃本次创建',exact:true}).click(),false,existing?RETRY:ABANDON)
  await expect(save).toBeDisabled();expect(writes).toBe(1)
  if(!existing){await page.getByRole('status').scrollIntoViewIfNeeded();await page.screenshot({path:testInfo.outputPath('uncertain-guide.png')})}
  if(existing){
    await answer(page,()=>page.getByRole('button',{name:'继续编辑原版本',exact:true}).click(),true,RETRY)
    await save.click();await expect(page.getByRole('alert')).toContainText('未覆盖任何内容')
    await expect(content(page)).toHaveValue('只在本页的正文')
  }else{
    await page.getByRole('button',{name:'继续这次创建',exact:true}).click()
    await expect(page.getByRole('heading',{name:'待保存攻略',exact:true})).toBeVisible()
    expect(await guides(request,auth)).toHaveLength(1)
  }
})

test('编辑成功保留原攻略编号并重载显示修改内容',async({page,request})=>{
  const {auth,id}=await setup(page,request,true);await fill(page)
  await page.getByRole('button',{name:'保存修改',exact:true}).click()
  await expect(page).toHaveURL(HOME+`guides/${id}`)
  await expect(page.getByRole('heading',{name:'待保存攻略',exact:true})).toBeVisible()
  await page.reload();await expect(page.getByText('只在本页的正文',{exact:true})).toBeVisible()
  const saved=await guides(request,auth);expect(saved).toHaveLength(1);expect(saved[0].id).toBe(id);expect(saved[0].is_draft).toBe(false)
})

test('提交后收到不完整200仍保留表单并暂停再次发布',async({page,request})=>{
  const {auth}=await setup(page,request);await fill(page);let writes=0
  await page.route('**/travel/api/guides/create',async route=>{
    if(route.request().method()!=='POST')return route.continue()
    writes++;const saved=await route.fetch();expect(saved.status()).toBe(201)
    await route.fulfill({status:200,json:{}})
  })
  await page.getByRole('button',{name:'发布攻略',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('提交结果暂时无法确认')
  await expect(content(page)).toHaveValue('只在本页的正文')
  await expect(page.getByRole('button',{name:'发布攻略',exact:true})).toBeDisabled()
  expect(writes).toBe(1);expect(await guides(request,auth)).toHaveLength(1)
})

test('明确拒绝保留原创建信息，确认放弃后才可修改并生成新尝试',async({page,request})=>{
  const {auth}=await setup(page,request);await fill(page)
  const reject=route=>route.fulfill({status:422,json:{detail:'隔离校验拒绝'}})
  await page.route('**/travel/api/guides/create',reject)
  await page.getByRole('button',{name:'发布攻略',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('隔离校验拒绝');await expect(content(page)).toBeDisabled()
  await answer(page,()=>page.getByRole('button',{name:'放弃本次创建',exact:true}).click(),true,ABANDON)
  await expect(content(page)).toHaveValue('只在本页的正文');await content(page).fill('修改后正文')
  expect(await guides(request,auth)).toHaveLength(0)
  await page.unroute('**/travel/api/guides/create',reject)
  await page.getByRole('button',{name:'发布攻略',exact:true}).click()
  await expect(page.getByRole('heading',{name:'待保存攻略',exact:true})).toBeVisible()
  const saved=await guides(request,auth);expect(saved).toHaveLength(1)
  const detail=await request.get(HOME+`api/guides/${saved[0].id}`,{headers:auth});expect((await detail.json()).content).toBe('修改后正文')
})

test('创建响应丢失后刷新继续原标识，返回已编辑的同一攻略而不覆盖',async({page,request})=>{
  const {auth}=await setup(page,request);await fill(page);const keys=[],statuses=[];let first=true
  await page.route('**/travel/api/guides/create',async route=>{
    keys.push(route.request().postDataJSON().request_id)
    const response=await route.fetch();statuses.push(response.status())
    if(first){first=false;return route.abort('failed')}
    return route.fulfill({response})
  })
  await page.getByRole('button',{name:'发布攻略',exact:true}).click()
  await expect(page.getByRole('button',{name:'继续这次创建',exact:true})).toBeVisible()
  const list=await guides(request,auth),id=list[0].id
  const before=await request.get(HOME+`api/guides/${id}`,{headers:auth}),g=await before.json()
  const changed=await request.patch(HOME+`api/guides/${id}/content`,{headers:auth,data:{title:'创建后另页修改',content:'新的云端正文',version:g.version,revision:g.revision,is_draft:true}})
  expect(changed.status()).toBe(200)
  await answer(page,()=>page.reload(),true,null,'beforeunload')
  await expect(title(page)).toHaveValue('待保存攻略');await expect(content(page)).toHaveValue('只在本页的正文')
  await page.getByRole('button',{name:'继续这次创建',exact:true}).click()
  await expect(page).toHaveURL(HOME+'me')
  expect(keys[0]).toBe(keys[1]);expect(statuses).toEqual([201,200]);expect(await guides(request,auth)).toHaveLength(1)
  const after=await request.get(HOME+`api/guides/${id}`,{headers:auth});expect((await after.json()).content).toBe('新的云端正文')
  expect(await page.evaluate(()=>Object.keys(sessionStorage).filter(k=>k.startsWith('travel.guide-creation.')))).toEqual([])
})

test('创建信息无法暂存时保留输入且不发送写入',async({page,request})=>{
  const {auth}=await setup(page,request);await fill(page);let writes=0
  page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/guides/create'))writes++})
  await page.evaluate(()=>{Storage.prototype.setItem=function(){throw new Error('isolated quota')}})
  await page.getByRole('button',{name:'发布攻略',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('无法保留本次攻略创建信息')
  await expect(content(page)).toBeEnabled();await expect(content(page)).toHaveValue('只在本页的正文')
  expect(writes).toBe(0);expect(await guides(request,auth)).toHaveLength(0)
})

test('旧服务缺少写入能力或专用路径时不退回旧创建',async({page,request})=>{
  const {auth}=await setup(page,request);await fill(page);let legacy=0
  page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/guides'))legacy++})
  const unsupported=route=>route.fulfill({status:404,json:{detail:'unsupported'}})
  await page.route('**/api/guides/write-capabilities',unsupported)
  await page.getByRole('button',{name:'发布攻略',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('无法完成可靠攻略写入')
  await expect(page.getByRole('button',{name:'继续这次创建',exact:true})).toHaveCount(0)
  await page.unroute('**/api/guides/write-capabilities',unsupported)
  await page.route('**/api/guides/create',unsupported)
  await page.getByRole('button',{name:'发布攻略',exact:true}).click()
  await expect(page.getByRole('button',{name:'继续这次创建',exact:true})).toBeVisible()
  expect(legacy).toBe(0);expect(await guides(request,auth)).toHaveLength(0)
})

test('云端已更新时拒绝旧版本覆盖，取消重载保留输入，确认才采用新版本',async({page,request})=>{
  const {auth,id}=await setup(page,request,true);await fill(page)
  const loaded=await request.get(HOME+`api/guides/${id}`,{headers:auth}),g=await loaded.json()
  const updated=await request.patch(HOME+`api/guides/${id}/content`,{headers:auth,data:{title:'另一页新标题',content:'另一页新正文',version:g.version,revision:g.revision,is_draft:true}})
  expect(updated.status()).toBe(200)
  await page.getByRole('button',{name:'保存修改',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('未覆盖任何内容')
  await expect(content(page)).toHaveValue('只在本页的正文')
  const reload=()=>page.getByRole('button',{name:'重新载入最新攻略',exact:true}).click()
  const message='重新载入会用云端最新内容替换本页输入。请先复制需要保留的正文，确定继续吗？'
  await answer(page,reload,false,message);await expect(content(page)).toHaveValue('只在本页的正文')
  await answer(page,reload,true,message);await expect(content(page)).toHaveValue('另一页新正文')
  await content(page).fill('基于新版本继续编辑');await page.getByRole('button',{name:'保存修改',exact:true}).click()
  await expect(page).toHaveURL(HOME+`guides/${id}`)
  const after=await request.get(HOME+`api/guides/${id}`,{headers:auth});const result=await after.json()
  expect(result.version).toBe(3);expect(result.title).toBe('另一页新标题');expect(result.content).toBe('基于新版本继续编辑')
})

test('详情删除使用已显示版本，陈旧删除拒绝且重载后确认才可删除',async({page,request})=>{
  const {auth,id}=await setup(page,request,true)
  await page.goto(HOME+`guides/${id}`);await expect(page.getByRole('heading',{name:'原有攻略 草稿',exact:true})).toBeVisible()
  const loaded=await request.get(HOME+`api/guides/${id}`,{headers:auth}),g=await loaded.json()
  expect((await request.patch(HOME+`api/guides/${id}/content`,{headers:auth,data:{title:'已更新攻略',content:'保留新版',version:g.version,revision:g.revision,is_draft:true}})).status()).toBe(200)
  const remove=()=>page.getByRole('button',{name:'🗑 删除',exact:true}).click()
  await answer(page,remove,true,'删除后不可恢复，确定要删除这篇攻略吗？')
  await expect(page.getByRole('alert')).toContainText('未覆盖任何内容');expect(await guides(request,auth)).toHaveLength(1)
  await page.getByRole('button',{name:'重新加载攻略',exact:true}).click()
  await expect(page.getByRole('heading',{name:'已更新攻略 草稿',exact:true})).toBeVisible()
  await answer(page,remove,true,'删除后不可恢复，确定要删除这篇攻略吗？')
  await expect(page).toHaveURL(HOME+'guides');expect(await guides(request,auth)).toHaveLength(0)
})
