import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'

const ORIGIN='http://127.0.0.1:5264',HOME=ORIGIN+'/travel/',PASSWORD='isolated-profile-password'
const DEPARTURE='昵称还有未保存的输入或待确认的保存。离开后本页输入会丢失，确定离开吗？'
const ADOPT='重新载入会用云端昵称替换本页输入。确定继续吗？'
const field=page=>page.getByRole('textbox',{name:'昵称',exact:true})
const area=page=>page.getByRole('region',{name:'个人资料',exact:true})
const save=page=>page.getByRole('button',{name:'保存昵称',exact:true})
async function localOnly(page){await page.route('**/*',route=>new URL(route.request().url()).origin===ORIGIN?route.continue():route.abort())}
async function account(request,nickname='原昵称'){
  const username='prof_'+randomUUID().replaceAll('-','').slice(0,16)
  expect((await request.post(HOME+'api/auth/register',{data:{username,password:PASSWORD,nickname}})).status()).toBe(201)
  const signed=await request.post(HOME+'api/auth/login',{data:{username,password:PASSWORD}})
  expect(signed.status()).toBe(200);const data=await signed.json()
  return {username,nickname,auth:{Authorization:'Bearer '+data.token}}
}
async function login(page,user){
  await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click()
  const dialog=page.getByRole('dialog',{name:'登录',exact:true})
  await dialog.getByRole('textbox',{name:'用户名',exact:true}).fill(user.username)
  await dialog.getByLabel('密码',{exact:true}).fill(PASSWORD)
  await dialog.getByRole('button',{name:'登录',exact:true}).last().click();await expect(dialog).toHaveCount(0)
}
async function setup(page,request,beforeOpen){
  await localOnly(page);const user=await account(request)
  await page.goto(HOME);await login(page,user)
  if(beforeOpen)await beforeOpen(user)
  await page.goto(HOME+'me')
  return user
}
async function answer(page,trigger,accept,message=DEPARTURE,type='confirm'){
  const handled=page.waitForEvent('dialog').then(async d=>{
    const actual={type:d.type(),message:d.message()}
    if(accept)await d.accept();else await d.dismiss()
    expect(actual.type).toBe(type);if(type==='confirm')expect(actual.message).toBe(message)
  })
  await Promise.all([handled,trigger()])
}
async function cloud(request,user){const response=await request.get(HOME+'api/me/profile',{headers:user.auth});expect(response.status()).toBe(200);return response.json()}

test('昵称离开取消保留输入，空收藏入口遵循子路径和离开保护',async({page,request})=>{
  const user=await setup(page,request);await field(page).fill('本页未保存昵称')
  const spots=page.getByRole('link',{name:'列表页',exact:true})
  await expect(spots).toHaveAttribute('href','/travel/spots')
  await answer(page,()=>spots.click(),false);await expect(field(page)).toHaveValue('本页未保存昵称')
  await answer(page,()=>spots.click(),true);await expect(page).toHaveURL(HOME+'spots')
  expect((await cloud(request,user)).nickname).toBe('原昵称')
})

test('刷新取消保留昵称输入，确认重载才恢复云端已保存内容',async({page,request})=>{
  await setup(page,request);await field(page).fill('刷新前输入')
  await answer(page,()=>page.evaluate(()=>location.reload()),false,null,'beforeunload');await expect(field(page)).toHaveValue('刷新前输入')
  await answer(page,()=>page.reload(),true,null,'beforeunload');await expect(field(page)).toHaveValue('原昵称')
})

test('退出账号取消保留输入，确认后清除本页个人资料',async({page,request})=>{
  await setup(page,request);await field(page).fill('退出前草稿')
  const logout=()=>page.getByRole('button',{name:'退出',exact:true}).click()
  await answer(page,logout,false);await expect(field(page)).toHaveValue('退出前草稿')
  await answer(page,logout,true);await expect(field(page)).toHaveCount(0)
  await expect(page.getByText('请先登录后访问个人中心',{exact:false})).toBeVisible()
})

test('收藏与攻略独立失败及重试，不隐藏资料或清除昵称草稿',async({page,request})=>{
  let favoritesFail=true,guidesFail=true,profileReads=0
  await setup(page,request,async()=>{
    page.on('request',r=>{if(r.method()==='GET'&&r.url().endsWith('/api/me/profile'))profileReads++})
    await page.route('**/api/me/favorites',route=>favoritesFail?route.fulfill({status:503,json:{detail:'隔离收藏故障'}}):route.continue())
    await page.route('**/api/me/guides',route=>guidesFail?route.fulfill({status:503,json:{detail:'隔离攻略故障'}}):route.continue())
  })
  await field(page).fill('列表失败仍保留')
  await expect(page.getByRole('alert')).toContainText('隔离收藏故障')
  favoritesFail=false;await page.getByRole('button',{name:'重试收藏',exact:true}).click()
  await expect(page.getByRole('link',{name:'列表页',exact:true})).toBeVisible();await expect(field(page)).toHaveValue('列表失败仍保留')
  await page.getByRole('button',{name:/^我发布的攻略/}).click();await expect(page.getByRole('alert')).toContainText('隔离攻略故障')
  guidesFail=false;await page.getByRole('button',{name:'重试攻略',exact:true}).click()
  await expect(page.getByText('还没有发布攻略，点右上角「写攻略」开始第一篇 ✍️',{exact:true})).toBeVisible()
  await expect(field(page)).toHaveValue('列表失败仍保留');expect(profileReads).toBe(1)
})

test('资料首次加载失败仍可查看攻略列表，重试只恢复资料',async({page,request})=>{
  let fail=true
  await setup(page,request,async user=>{
    expect((await request.post(HOME+'api/guides',{headers:user.auth,data:{title:'隔离已有攻略',content:'正文',is_draft:true}})).status()).toBe(201)
    await page.route('**/api/me/profile',route=>fail?route.fulfill({status:503,json:{detail:'隔离资料故障'}}):route.continue())
  })
  await expect(area(page).getByRole('alert')).toContainText('隔离资料故障');await expect(field(page)).toHaveCount(0)
  await page.getByRole('button',{name:/^我发布的攻略/}).click()
  await expect(page.getByRole('link',{name:'隔离已有攻略 草稿',exact:true})).toBeVisible()
  fail=false;await page.getByRole('button',{name:'重试资料',exact:true}).click();await expect(field(page)).toHaveValue('原昵称')
})

test('保存期间锁定重复操作，成功后卡片和导航栏同步昵称',async({page,request},testInfo)=>{
  const user=await setup(page,request);await field(page).fill('保存后的昵称')
  let release,arrived,writes=0;const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r)
  await page.route('**/api/me/profile/edit',async route=>{writes++;arrived();await gate;await route.continue()})
  try{
    await save(page).click();await received;await expect(field(page)).toBeDisabled()
    await expect(page.getByRole('button',{name:'保存中…',exact:true})).toBeDisabled()
    release();await expect(area(page).getByRole('status')).toHaveText('昵称已保存。')
    await expect(page.locator('nav .u-hello')).toContainText('保存后的昵称');await expect(page.locator('.p-name')).toHaveText('保存后的昵称')
    expect(writes).toBe(1);expect((await cloud(request,user)).version).toBe(2)
    await page.screenshot({path:testInfo.outputPath('saved-profile.png')})
    await page.locator('nav .logo').click();await expect(page).toHaveURL(HOME)
  }finally{release()}
})

test('已提交后响应丢失：核对保留输入和原版本，原次重试拒绝覆盖',async({page,request},testInfo)=>{
  const user=await setup(page,request),versions=[];let first=true
  await page.route('**/api/me/profile/edit',async route=>{
    versions.push(route.request().postDataJSON().version);const response=await route.fetch()
    if(first){first=false;expect(response.status()).toBe(200);return route.abort('failed')}
    return route.fulfill({response})
  })
  await field(page).fill('已写入云端的昵称');await save(page).click()
  await expect(area(page).getByRole('alert')).toContainText('结果暂时无法确认')
  await expect(field(page)).toHaveValue('已写入云端的昵称');await expect(field(page)).toHaveAttribute('readonly','')
  await page.getByRole('button',{name:'核对云端昵称',exact:true}).click()
  await expect(area(page).getByRole('status')).toContainText('原保存版本仍保留')
  await page.getByRole('button',{name:'继续原次保存',exact:true}).click()
  await expect(area(page).getByRole('alert')).toContainText('未覆盖任何内容');expect(versions).toEqual([1,1])
  expect((await cloud(request,user)).version).toBe(2)
  await page.screenshot({path:testInfo.outputPath('uncertain-profile.png')})
  const adopt=()=>page.getByRole('button',{name:'重新载入云端昵称',exact:true}).click()
  await answer(page,adopt,false,ADOPT);await expect(field(page)).toHaveValue('已写入云端的昵称')
  await answer(page,adopt,true,ADOPT);await expect(save(page)).toBeDisabled();await expect(field(page)).not.toHaveAttribute('readonly','')
  await page.locator('nav .logo').click();await expect(page).toHaveURL(HOME)
})

test('同账号双标签保存冲突：保留本页草稿，确认载入新版本后才能继续',async({page,request,context})=>{
  const user=await setup(page,request),other=await context.newPage();await localOnly(other);await other.goto(HOME+'me')
  await expect(field(other)).toHaveValue('原昵称');await field(page).fill('旧页草稿')
  await field(other).fill('另一页已保存');await save(other).click();await expect(area(other).getByRole('status')).toHaveText('昵称已保存。')
  await save(page).click();await expect(area(page).getByRole('alert')).toContainText('未覆盖任何内容');await expect(field(page)).toHaveValue('旧页草稿')
  expect((await cloud(request,user)).nickname).toBe('另一页已保存')
  await page.getByRole('button',{name:'核对云端昵称',exact:true}).click();await expect(field(page)).toHaveValue('旧页草稿')
  await answer(page,()=>page.getByRole('button',{name:'重新载入云端昵称',exact:true}).click(),true,ADOPT)
  await expect(field(page)).toHaveValue('另一页已保存');await field(page).fill('基于新版本编辑');await save(page).click()
  await expect(area(page).getByRole('status')).toHaveText('昵称已保存。');expect((await cloud(request,user)).version).toBe(3)
  await other.close()
})

test('后端已提交但返回不完整200时不清草稿、不宣称保存成功',async({page,request})=>{
  const user=await setup(page,request);let writes=0
  await page.route('**/api/me/profile/edit',async route=>{writes++;const response=await route.fetch();expect(response.status()).toBe(200);await route.fulfill({status:200,json:{}})})
  await field(page).fill('不完整响应昵称');await save(page).click()
  await expect(area(page).getByRole('alert')).toContainText('无法确认');await expect(field(page)).toHaveValue('不完整响应昵称')
  await expect(page.getByRole('button',{name:'继续原次保存',exact:true})).toBeVisible();await expect(save(page)).toBeDisabled()
  expect(writes).toBe(1);expect((await cloud(request,user)).version).toBe(2)
})

test('旧服务缺资料版本或专用写入时不退回旧保存',async({page,request})=>{
  const legacy=async route=>{const response=await route.fetch(),data=await response.json();delete data.version;await route.fulfill({response,json:data})}
  await setup(page,request,()=>page.route('**/api/me/profile',legacy))
  await expect(area(page).getByRole('alert')).toContainText('版本保护');await expect(field(page)).toHaveCount(0)
  await page.unroute('**/api/me/profile',legacy);await page.getByRole('button',{name:'重试资料',exact:true}).click();await field(page).fill('不能降级的昵称')
  let oldWrites=0;page.on('request',r=>{if(r.method()==='PATCH'&&r.url().endsWith('/api/me/profile'))oldWrites++})
  await page.route('**/api/me/profile/edit',route=>route.fulfill({status:405,json:{detail:'unsupported'}}))
  await save(page).click();await expect(area(page).getByRole('alert')).toContainText('无法安全保存资料');expect(oldWrites).toBe(0)
})

test('空白昵称不发送，明确校验拒绝保留输入，修改后可手动重试',async({page,request})=>{
  const user=await setup(page,request);let writes=0
  page.on('request',r=>{if(r.method()==='PATCH'&&r.url().endsWith('/api/me/profile/edit'))writes++})
  await field(page).fill('  ');await save(page).click();await expect(area(page).getByRole('alert')).toContainText('请填写1到50字');expect(writes).toBe(0)
  const reject=route=>route.fulfill({status:422,json:{detail:'隔离校验拒绝'}})
  await page.route('**/api/me/profile/edit',reject);await field(page).fill('保留输入');await save(page).click()
  await expect(area(page).getByRole('alert')).toContainText('隔离校验拒绝');await expect(field(page)).toBeEnabled()
  await page.unroute('**/api/me/profile/edit',reject);await field(page).fill('修改后提交');await save(page).click()
  await expect(area(page).getByRole('status')).toHaveText('昵称已保存。');expect((await cloud(request,user)).nickname).toBe('修改后提交')
})

test('另一标签换号后本页保留昵称草稿，取消同步不发送跨账号保存',async({page,request,context})=>{
  const first=await setup(page,request),second=await account(request,'另一账号昵称');await field(page).fill('旧账号草稿')
  let writes=0;page.on('request',r=>{if(r.method()==='PATCH'&&r.url().endsWith('/api/me/profile/edit'))writes++})
  const other=await context.newPage();await localOnly(other);await other.goto(HOME)
  await other.getByRole('button',{name:'退出',exact:true}).click();await login(other,second)
  await expect(page.getByRole('button',{name:'同步账号',exact:true})).toBeVisible();await save(page).click()
  await expect(field(page)).toHaveValue('旧账号草稿');expect(writes).toBe(0)
  const sync=()=>page.getByRole('button',{name:'同步账号',exact:true}).click()
  await answer(page,sync,false);await expect(field(page)).toHaveValue('旧账号草稿')
  await answer(page,sync,true);await expect(field(page)).toHaveValue('另一账号昵称')
  expect((await cloud(request,first)).nickname).toBe('原昵称');expect((await cloud(request,second)).nickname).toBe('另一账号昵称')
  await other.close()
})
