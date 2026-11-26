import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'

const ORIGIN='http://127.0.0.1:5264',HOME=ORIGIN+'/travel/',PASSWORD='isolated-guide-detail-password'
const DELETE='删除后不可恢复，确定要删除这篇攻略吗？'
const LEAVE='删除请求仍在处理，离开后请在“我的攻略”核对结果。确定离开吗？'
async function localOnly(page){
  await page.route('**/*',route=>new URL(route.request().url()).origin===ORIGIN?route.continue():route.abort())
  await page.route('**/qa/guide-*.svg',route=>route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60"><rect width="100" height="60" fill="#81b99b"/></svg>'}))
}
async function account(request,nickname='攻略验收'){
  const username='gdt_'+randomUUID().replaceAll('-','').slice(0,16)
  expect((await request.post(HOME+'api/auth/register',{data:{username,password:PASSWORD,nickname}})).status()).toBe(201)
  const signed=await request.post(HOME+'api/auth/login',{data:{username,password:PASSWORD}}),data=await signed.json()
  return {username,auth:{Authorization:'Bearer '+data.token}}
}
async function login(page,user){
  await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click()
  const dialog=page.getByRole('dialog',{name:'登录',exact:true})
  await dialog.getByRole('textbox',{name:'用户名',exact:true}).fill(user.username)
  await dialog.getByLabel('密码',{exact:true}).fill(PASSWORD)
  await dialog.getByRole('button',{name:'登录',exact:true}).last().click();await expect(dialog).toHaveCount(0)
}
async function setup(page,request,draft=false){
  await localOnly(page);const user=await account(request)
  const make=async body=>{const response=await request.post(HOME+'api/guides',{headers:user.auth,data:body});expect(response.status()).toBe(201);return {...body,...await response.json()}}
  const first=await make({title:'第一篇攻略',content:'第一篇的正文',is_draft:draft,images:[HOME+'qa/guide-one.svg',HOME+'qa/guide-two.svg']})
  const second=await make({title:'第二篇攻略',content:'第二篇的正文',is_draft:false,images:[]})
  const response=await request.post(HOME+'api/trips/create',{headers:user.auth,data:{title:'账号旅行',days:1,request_id:randomUUID()}})
  expect(response.status()).toBe(201);const trip=await response.json()
  await page.goto(HOME);await login(page,user);await page.goto(HOME+`guides/${first.id}`)
  await expect(heading(page,first)).toBeVisible()
  return {user,first,second,trip}
}
const heading=(page,guide)=>page.getByRole('heading',{name:guide.title+(guide.is_draft?' 草稿':''),exact:true})
const collect=page=>page.getByRole('button',{name:'从这篇攻略收集地点',exact:true})
const remove=page=>page.getByRole('button',{name:'🗑 删除',exact:true})
async function picker(page){await collect(page).click();const dialog=page.getByRole('dialog',{name:'选择一段旅行',exact:true});await expect(dialog).toBeVisible();return dialog}
async function navigate(page,path){
  // Use the running app's actual router to exercise reuse of its :id component.
  await page.evaluate(async path=>{const {default:router}=await import('/travel/src/router/index.js');await router.push(path)},path)
}
async function localTrip(page){return page.evaluate(async()=>{const {guestTrips}=await import('/travel/src/store/guestTrips.js');return guestTrips.create({title:'本机旅行',destination:'杭州',days:1})})}
async function answer(page,trigger,accept,message=DELETE){
  const handled=page.waitForEvent('dialog').then(async dialog=>{
    const actual={type:dialog.type(),message:dialog.message()};if(accept)await dialog.accept();else await dialog.dismiss()
    expect(actual).toEqual({type:'confirm',message})
  });await Promise.all([handled,trigger()])
}
async function myGuides(request,user){return (await request.get(HOME+'api/me/guides',{headers:user.auth})).json()}

test('同组件切换攻略后旧读取不能覆盖当前正文',async({page,request})=>{
  const {first,second}=await setup(page,request);let release,arrived,finished
  const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),done=new Promise(r=>finished=r)
  await page.route(`**/api/guides/${second.id}`,async route=>{const response=await route.fetch();arrived();await gate;await route.fulfill({response});finished()})
  try{
    await navigate(page,`/guides/${second.id}`);await received;await expect(heading(page,first)).toHaveCount(0)
    await navigate(page,`/guides/${first.id}`);await expect(heading(page,first)).toBeVisible()
    release();await done;await expect(heading(page,first)).toBeVisible();await expect(page.getByText('第二篇的正文',{exact:true})).toHaveCount(0)
  }finally{release()}
})

test('返回错误攻略编号的成功响应不能显示或开放删除，重试恢复正确内容',async({page,request})=>{
  const {first,second}=await setup(page,request)
  const mismatch=async route=>{const response=await route.fetch(),data=await response.json();data.id=first.id;await route.fulfill({response,json:data})}
  await page.route(`**/api/guides/${second.id}`,mismatch);await navigate(page,`/guides/${second.id}`)
  await expect(page.getByRole('alert')).toContainText('无法确认这篇攻略');await expect(remove(page)).toHaveCount(0);await expect(collect(page)).toHaveCount(0)
  await page.unroute(`**/api/guides/${second.id}`,mismatch);await page.getByRole('button',{name:'重新加载攻略',exact:true}).click()
  await expect(heading(page,second)).toBeVisible()
})

test('画廊支持键盘切换图片，换攻略后重置图片位置',async({page,request})=>{
  const {first,second}=await setup(page,request)
  const next=page.getByRole('button',{name:'下一张攻略图片',exact:true});await next.focus();await next.press('Enter')
  await expect(page.locator('.gallery .gmain')).toHaveAttribute('src',HOME+'qa/guide-two.svg')
  const thumb=page.getByRole('button',{name:'查看攻略图片 1',exact:true});await thumb.focus();await thumb.press('Space')
  await expect(thumb).toHaveAttribute('aria-pressed','true')
  await next.click();await navigate(page,`/guides/${second.id}`);await expect(heading(page,second)).toBeVisible()
  await expect(page.getByText('🖼️ 图片画廊 · 共 0 图',{exact:true})).toBeVisible()
  await navigate(page,`/guides/${first.id}`);await expect(heading(page,first)).toBeVisible()
  await expect(page.locator('.gallery .gmain')).toHaveAttribute('src',HOME+'qa/guide-one.svg')
})

test('旅行选择为真实模态，Tab限制焦点，Esc关闭并恢复入口焦点',async({page,request},testInfo)=>{
  await setup(page,request);await localTrip(page);const dialog=await picker(page)
  await expect(dialog.getByRole('combobox',{name:'选择旅行',exact:true})).toBeVisible()
  expect(await dialog.evaluate(el=>el.matches(':modal'))).toBe(true)
  const close=dialog.getByRole('button',{name:'关闭',exact:true}),create=dialog.getByRole('link',{name:'创建旅行 →',exact:true})
  await close.focus();await page.keyboard.press('Shift+Tab');await expect(create).toBeFocused()
  await page.keyboard.press('Tab');await expect(close).toBeFocused()
  for(let i=0;i<7;i++){await page.keyboard.press('Tab');expect(await page.evaluate(()=>Boolean(document.activeElement?.closest('dialog')))).toBe(true)}
  await page.screenshot({path:testInfo.outputPath('guide-picker.png')})
  await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);await expect(collect(page)).toBeFocused()
})

test('加载中关闭再重开选择器，旧列表不会回填或覆盖新旅行',async({page,request})=>{
  const {user,trip}=await setup(page,request);let first=true,release,arrived,finished
  const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),done=new Promise(r=>finished=r)
  await page.route('**/api/trips',async route=>{if(!first)return route.continue();first=false;const response=await route.fetch();arrived();await gate;await route.fulfill({response});finished()})
  try{
    let dialog=await picker(page);await received;await expect(dialog.getByText('正在加载旅行…',{exact:true})).toBeVisible()
    await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0)
    expect((await request.patch(HOME+`api/trips/${trip.id}`,{headers:user.auth,data:{version:trip.version,title:'更新后的账号旅行'}})).status()).toBe(200)
    dialog=await picker(page);const select=dialog.getByRole('combobox',{name:'选择旅行',exact:true})
    await expect(select.locator('option',{hasText:'更新后的账号旅行'})).toHaveCount(1)
    release();await done;await expect(select.locator('option',{hasText:'更新后的账号旅行'})).toHaveCount(1)
    await expect(select.locator('option',{hasText:/^账号旅行 ·/})).toHaveCount(0)
  }finally{release()}
})

test('公开攻略列表部分故障仍可选本机，重试后账号预览不自动导入',async({page,request})=>{
  const {user,trip,first}=await setup(page,request);await localTrip(page);let fail=true,writes=0
  page.on('request',r=>{if(r.method()==='POST'&&/guide-import\/(preview|apply)$/.test(r.url()))writes++})
  await page.route('**/api/trips',route=>fail?route.fulfill({status:503,json:{detail:'列表故障'}}):route.continue())
  const dialog=await picker(page);await expect(dialog.getByRole('alert')).toContainText('部分旅行列表')
  const select=dialog.getByRole('combobox',{name:'选择旅行',exact:true});await expect(select.locator('option',{hasText:'本机旅行'})).toHaveCount(1)
  fail=false;await dialog.getByRole('button',{name:'重试',exact:true}).click();await expect(dialog.getByRole('alert')).toHaveCount(0)
  await select.selectOption(String(trip.id));await dialog.getByRole('button',{name:'打开攻略预览',exact:true}).click()
  await expect(page).toHaveURL(HOME+`trips/${trip.id}?guide=${first.id}`)
  await expect(page.getByRole('textbox',{name:'正文预览',exact:true})).toHaveValue(/第一篇的正文/)
  expect(writes).toBe(0);const loaded=await request.get(HOME+`api/trips/${trip.id}`,{headers:user.auth});expect((await loaded.json()).places).toHaveLength(0)
})

test('草稿攻略只列出账号旅行，进入正文预览仍不自动写入',async({page,request})=>{
  const {trip,first}=await setup(page,request,true);await localTrip(page);const dialog=await picker(page)
  await expect(dialog.getByText('草稿攻略仅可导入有权限的账号旅行。',{exact:true})).toBeVisible()
  const select=dialog.getByRole('combobox',{name:'选择旅行',exact:true})
  await expect(select.locator('option',{hasText:'本机旅行'})).toHaveCount(0);await expect(select.locator('option',{hasText:'账号旅行'})).toHaveCount(1)
  await select.selectOption(String(trip.id));await dialog.getByRole('button',{name:'打开攻略预览',exact:true}).click()
  await expect(page).toHaveURL(HOME+`trips/${trip.id}?guide=${first.id}`)
  await expect(page.getByRole('textbox',{name:'正文预览',exact:true})).toHaveValue(/第一篇的正文/)
})

test('游客可从公开攻略选择本机预览，不请求账号旅行或自动识别',async({page,request})=>{
  const {first}=await setup(page,request);const local=await localTrip(page)
  await page.getByRole('button',{name:'退出',exact:true}).click();await expect(heading(page,first)).toBeVisible();await expect(remove(page)).toHaveCount(0)
  let cloudReads=0,writes=0;page.on('request',r=>{if(r.url().endsWith('/api/trips'))cloudReads++;if(r.method()==='POST'&&r.url().includes('guide-preview'))writes++})
  const dialog=await picker(page);await dialog.getByRole('combobox',{name:'选择旅行',exact:true}).selectOption(local.id)
  await dialog.getByRole('button',{name:'打开攻略预览',exact:true}).click()
  await expect(page).toHaveURL(HOME+`trips/${local.id}?guide=${first.id}`)
  await expect(page.getByRole('textbox',{name:'正文预览',exact:true})).toHaveValue(/第一篇的正文/)
  expect(cloudReads).toBe(0);expect(writes).toBe(0)
})

test('真实删除后响应丢失暂停再次操作，重载404并直达我的攻略标签',async({page,request},testInfo)=>{
  const {user,first,second}=await setup(page,request);let deletes=0
  await page.route(`**/api/guides/${first.id}/content?*`,async route=>{deletes++;const response=await route.fetch();expect(response.status()).toBe(204);await route.abort('failed')})
  await answer(page,()=>remove(page).click(),true)
  await expect(page.getByRole('status')).toContainText('删除结果尚未确认');await expect(remove(page)).toBeDisabled();await expect(collect(page)).toBeDisabled()
  await expect(heading(page,first)).toBeVisible();expect(deletes).toBe(1);expect(await myGuides(request,user)).toHaveLength(1)
  await page.getByRole('status').scrollIntoViewIfNeeded()
  const recovery=page.getByRole('link',{name:'查看我的攻略',exact:true})
  await expect(recovery).toBeInViewport()
  if(await page.locator('.mobile-nav').isVisible()){
    const link=await recovery.boundingBox(),nav=await page.locator('.mobile-nav').boundingBox()
    expect(link.y+link.height).toBeLessThanOrEqual(nav.y+1)
  }
  await page.screenshot({path:testInfo.outputPath('uncertain-delete.png')})
  await page.getByRole('button',{name:'重新加载攻略',exact:true}).click();await expect(page.getByRole('alert')).toContainText('攻略不存在')
  await expect(heading(page,first)).toHaveCount(0);await page.getByRole('link',{name:'返回我的攻略',exact:true}).click()
  await expect(page).toHaveURL(HOME+'me?tab=guides');await expect(page.getByRole('button',{name:/^我发布的攻略/})).toHaveAttribute('aria-pressed','true')
  await expect(page.getByRole('link',{name:second.title,exact:true})).toBeVisible()
})

test('删除503仍保留详情，核对后才允许再次确认删除且不多弹离开确认',async({page,request})=>{
  const {user,first}=await setup(page,request)
  const fail=route=>route.fulfill({status:503,json:{detail:'隔离删除故障'}})
  await page.route(`**/api/guides/${first.id}/content?*`,fail);await answer(page,()=>remove(page).click(),true)
  await expect(page.getByRole('alert')).toContainText('隔离删除故障');await expect(heading(page,first)).toBeVisible();await expect(remove(page)).toBeDisabled()
  expect(await myGuides(request,user)).toHaveLength(2)
  await page.unroute(`**/api/guides/${first.id}/content?*`,fail);await page.getByRole('button',{name:'重新加载攻略',exact:true}).click();await expect(remove(page)).toBeEnabled()
  await answer(page,()=>remove(page).click(),true);await expect(page).toHaveURL(HOME+'guides');expect(await myGuides(request,user)).toHaveLength(1)
})

test('删除等待保护退出和离开，确认换攻略后迟到成功不跳走',async({page,request})=>{
  const {first,second}=await setup(page,request);let release,arrived,finished
  const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),done=new Promise(r=>finished=r)
  await page.route(`**/api/guides/${first.id}/content?*`,async route=>{const response=await route.fetch();arrived();await gate;await route.fulfill({response});finished()})
  try{
    await answer(page,()=>remove(page).click(),true);await received
    await answer(page,()=>page.getByRole('button',{name:'退出',exact:true}).click(),false,LEAVE)
    await answer(page,()=>navigate(page,`/guides/${second.id}`),false,LEAVE);await expect(heading(page,first)).toBeVisible()
    await answer(page,()=>navigate(page,`/guides/${second.id}`),true,LEAVE);await expect(heading(page,second)).toBeVisible()
    release();await done;await expect(page).toHaveURL(HOME+`guides/${second.id}`);await expect(heading(page,second)).toBeVisible();await expect(remove(page)).toBeEnabled()
  }finally{release()}
})

test('草稿详情退出立即清旧正文和权限，再登录才可恢复',async({page,request})=>{
  const {user,first}=await setup(page,request,true)
  await page.getByRole('button',{name:'退出',exact:true}).click();await expect(page.getByRole('alert')).toContainText('攻略不存在')
  await expect(page.getByText('第一篇的正文',{exact:true})).toHaveCount(0);await expect(remove(page)).toHaveCount(0);await expect(collect(page)).toHaveCount(0)
  await login(page,user);await expect(heading(page,first)).toBeVisible();await expect(remove(page)).toBeEnabled()
})

test('其他标签换号后不发送旧攻略删除，显式同步后清除旧私有正文',async({page,request,context})=>{
  const {user,first}=await setup(page,request,true),second=await account(request,'另一账号');let deletes=0
  page.on('request',r=>{if(r.method()==='DELETE')deletes++})
  const other=await context.newPage();await localOnly(other);await other.goto(HOME)
  await other.getByRole('button',{name:'退出',exact:true}).click();await login(other,second)
  await expect(page.getByRole('button',{name:'同步账号',exact:true})).toBeVisible()
  await answer(page,()=>remove(page).click(),true);await expect(page.getByRole('status').filter({hasText:'删除结果尚未确认'})).toBeVisible();expect(deletes).toBe(0)
  await page.getByRole('button',{name:'同步账号',exact:true}).click();await expect(page.getByRole('alert')).toContainText('攻略不存在')
  await expect(page.getByText('第一篇的正文',{exact:true})).toHaveCount(0);await expect(remove(page)).toHaveCount(0)
  expect((await request.get(HOME+`api/guides/${first.id}`,{headers:user.auth})).status()).toBe(200)
  expect((await request.get(HOME+`api/guides/${first.id}`,{headers:second.auth})).status()).toBe(404);await other.close()
})
