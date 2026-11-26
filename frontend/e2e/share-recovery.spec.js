import {workspace} from './workspace-helper.js'
import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'
const ORIGIN='http://127.0.0.1:5264',HOME=ORIGIN+'/travel/',PASSWORD='isolated-sharing-recovery-password'
async function setup(page,request){
  const username='share_'+randomUUID().replaceAll('-','').slice(0,16)
  expect((await request.post(ORIGIN+'/travel/api/auth/register',{data:{username,password:PASSWORD}})).status()).toBe(201)
  const signed=await request.post(ORIGIN+'/travel/api/auth/login',{data:{username,password:PASSWORD}})
  const auth={Authorization:'Bearer '+(await signed.json()).token}
  await page.route('**/*',route=>new URL(route.request().url()).origin===ORIGIN?route.continue():route.abort())
  await page.goto(HOME)
  await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click()
  const dialog=page.getByRole('dialog',{name:'登录',exact:true})
  await dialog.getByRole('textbox',{name:'用户名',exact:true}).fill(username)
  await dialog.getByLabel('密码',{exact:true}).fill(PASSWORD)
  await dialog.getByRole('button',{name:'登录',exact:true}).last().click()
  await expect(dialog).toHaveCount(0)
  await page.getByRole('link',{name:'＋ 创建旅行',exact:true}).click()
  await page.getByRole('textbox',{name:'旅行名称',exact:true}).fill('分享恢复隔离验收')
  await page.getByRole('button',{name:'创建旅行 →',exact:true}).click()
  await expect(page.getByRole('heading',{name:'分享恢复隔离验收',exact:true})).toBeVisible()
  await workspace(page,'同行')
  await expect(page.getByRole('button',{name:'生成只读链接',exact:true})).toBeVisible()
  const id=new URL(page.url()).pathname.split('/').at(-1)
  return {auth,path:ORIGIN+`/travel/api/trips/${id}/sharing`}
}
async function info(request,trip){return (await (await request.get(trip.path,{headers:trip.auth})).json()).links}
async function dropActivation(page){
  const state={proofs:[],results:[],statuses:[]}
  await page.route('**/sharing/links/activate',async route=>{
    state.proofs.push(route.request().postDataJSON().preparation)
    const response=await route.fetch()
    state.statuses.push(response.status())
    state.results.push(await response.json())
    if(state.proofs.length===1)await route.abort('failed')
    else await route.fulfill({response})
  })
  return state
}
async function lost(page,mode='view'){
  await page.getByRole('button',{name:mode==='view'?'生成只读链接':'生成编辑邀请',exact:true}).click()
  await expect(page.getByRole('button',{name:'继续生成这条链接',exact:true})).toBeEnabled()
  await expect(page.getByRole('alert')).toContainText('结果暂时无法确认')
}
async function answer(page,trigger,accept,type='confirm'){
  const handled=page.waitForEvent('dialog').then(async dialog=>{
    const actual=dialog.type();if(accept)await dialog.accept();else await dialog.dismiss();expect(actual).toBe(type)
  })
  await Promise.all([handled,trigger()])
}

test('准备响应丢失不产生链接，再次生成只有一次有效创建',async({page,request})=>{
  const trip=await setup(page,request)
  let first=true
  await page.route('**/sharing/links/prepare',async route=>{
    if(!first)return route.continue()
    first=false;await route.fetch();await route.abort('failed')
  })
  await page.getByRole('button',{name:'生成只读链接',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('暂时无法准备分享链接')
  expect(await info(request,trip)).toHaveLength(0)
  await expect(page.getByRole('button',{name:'继续生成这条链接',exact:true})).toHaveCount(0)
  await page.getByRole('button',{name:'生成只读链接',exact:true}).click()
  await expect(page.getByRole('textbox',{name:'分享地址',exact:true})).toBeVisible()
  expect(await info(request,trip)).toHaveLength(1)
})

for(const mode of ['view','edit'])test(`${mode}已创建但响应丢失：当前页继续返回相同链接且不写入浏览器存储`,async({page,request})=>{
  const trip=await setup(page,request),state=await dropActivation(page)
  await lost(page,mode)
  await expect(page.getByRole('textbox',{name:'分享地址',exact:true})).toHaveCount(0)
  await expect(page.getByRole('button',{name:'生成只读链接',exact:true})).toBeDisabled()
  expect(await info(request,trip)).toHaveLength(1)
  const stored=await page.evaluate(()=>JSON.stringify({local:{...localStorage},session:{...sessionStorage}}))
  expect(stored).not.toContain(state.proofs[0])
  await page.getByRole('button',{name:'继续生成这条链接',exact:true}).click()
  const address=page.getByRole('textbox',{name:'分享地址',exact:true})
  await expect(address).toBeVisible()
  expect(new URL(await address.inputValue()).hash.slice(1)).toBe(state.results[0].token)
  expect(state.proofs[0]).toBe(state.proofs[1])
  expect(state.statuses).toEqual([201,200])
  expect(state.results[0]).toEqual(state.results[1])
  expect(await info(request,trip)).toHaveLength(1)
})

test('未知结果中的链接被撤销后重试不复活，放弃只清本页重试信息',async({page,request})=>{
  const trip=await setup(page,request),state=await dropActivation(page)
  await lost(page)
  const link=(await info(request,trip))[0]
  expect((await request.delete(trip.path+`/links/${link.id}`,{headers:trip.auth})).status()).toBe(204)
  await page.getByRole('button',{name:'继续生成这条链接',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('不能通过重试重新启用')
  await expect(page.getByRole('textbox',{name:'分享地址',exact:true})).toHaveCount(0)
  const discard=page.getByRole('button',{name:'放弃本次重试',exact:true})
  await answer(page,()=>discard.click(),false)
  await expect(page.getByRole('button',{name:'继续生成这条链接',exact:true})).toBeVisible()
  await answer(page,()=>discard.click(),true)
  await expect(page.getByRole('button',{name:'继续生成这条链接',exact:true})).toHaveCount(0)
  const links=await info(request,trip)
  expect(links).toHaveLength(1);expect(links[0].status).toBe('revoked')
  expect(state.statuses).toEqual([201,409])
})

test('刷新后不找回分享地址或准备信息，管理列表仍能核对已生成链接',async({page,request})=>{
  const trip=await setup(page,request)
  await dropActivation(page);await lost(page)
  await answer(page,()=>page.reload(),true,'beforeunload')
  await expect(page.getByRole('button',{name:'生成只读链接',exact:true})).toBeEnabled()
  await expect(page.getByRole('button',{name:'继续生成这条链接',exact:true})).toHaveCount(0)
  await expect(page.getByRole('textbox',{name:'分享地址',exact:true})).toHaveCount(0)
  await expect(page.getByText('管理链接（1）',{exact:true})).toBeVisible()
  expect(await info(request,trip)).toHaveLength(1)
})

test('旧服务缺少准备接口时不创建普通链接',async({page,request})=>{
  const trip=await setup(page,request),legacy=[]
  page.on('request',req=>{if(req.method()==='POST'&&req.url().endsWith('/sharing/links'))legacy.push(req.url())})
  await page.route('**/sharing/links/prepare',route=>route.fulfill({status:404,json:{detail:'Old server'}}))
  await page.getByRole('button',{name:'生成只读链接',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('无法准备可靠链接')
  expect(legacy).toEqual([]);expect(await info(request,trip)).toHaveLength(0)
})

test('创建接口暂不可用时保留准备信息，恢复后继续而不重新准备',async({page,request})=>{
  const trip=await setup(page,request),proofs=[],prepares=[]
  page.on('request',req=>{
    if(req.url().endsWith('/links/prepare'))prepares.push(req.url())
    if(req.url().endsWith('/links/activate'))proofs.push(req.postDataJSON().preparation)
  })
  const unsupported=route=>route.fulfill({status:405,json:{detail:'Old route'}})
  await page.route('**/sharing/links/activate',unsupported)
  await page.getByRole('button',{name:'生成只读链接',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('无法继续可靠生成链接')
  expect(await info(request,trip)).toHaveLength(0)
  await page.unroute('**/sharing/links/activate',unsupported)
  await page.getByRole('button',{name:'继续生成这条链接',exact:true}).click()
  await expect(page.getByRole('textbox',{name:'分享地址',exact:true})).toBeVisible()
  expect(prepares).toHaveLength(1);expect(proofs[0]).toBe(proofs[1])
  expect(await info(request,trip)).toHaveLength(1)
})

test('已提交但返回不完整的成功响应时保留原尝试，继续可找回同一链接',async({page,request})=>{
  const trip=await setup(page,request),proofs=[]
  let first=true
  await page.route('**/sharing/links/activate',async route=>{
    proofs.push(route.request().postDataJSON().preparation)
    const response=await route.fetch()
    if(first){first=false;return route.fulfill({status:200,json:{}})}
    return route.fulfill({response})
  })
  await page.getByRole('button',{name:'生成只读链接',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('结果暂时无法确认')
  await expect(page.getByRole('textbox',{name:'分享地址',exact:true})).toHaveCount(0)
  expect(await info(request,trip)).toHaveLength(1)
  await page.getByRole('button',{name:'继续生成这条链接',exact:true}).click()
  await expect(page.getByRole('textbox',{name:'分享地址',exact:true})).toBeVisible()
  expect(proofs[0]).toBe(proofs[1])
  expect(await info(request,trip)).toHaveLength(1)
})
