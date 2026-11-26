import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'
const ORIGIN='http://127.0.0.1:5264', HOME=ORIGIN+'/travel/'
const PASSWORD='isolated-creation-test-password'

async function account(request) {
  const username='create_'+randomUUID().replaceAll('-','').slice(0,16)
  expect((await request.post(ORIGIN+'/travel/api/auth/register',{data:{username,password:PASSWORD}})).status()).toBe(201)
  const signed=await request.post(ORIGIN+'/travel/api/auth/login',{data:{username,password:PASSWORD}})
  expect(signed.status()).toBe(200)
  const result=await signed.json()
  return {username,auth:{Authorization:'Bearer '+result.token}}
}
async function login(page,user) {
  await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click()
  const dialog=page.getByRole('dialog',{name:'登录',exact:true})
  await dialog.getByRole('textbox',{name:'用户名',exact:true}).fill(user.username)
  await dialog.getByLabel('密码',{exact:true}).fill(PASSWORD)
  await dialog.getByRole('button',{name:'登录',exact:true}).last().click()
  await expect(dialog).toHaveCount(0)
}
async function setup(page,request) {
  const user=await account(request)
  await page.route('**/*',route=>new URL(route.request().url()).origin===ORIGIN?route.continue():route.abort())
  await page.goto(HOME);await login(page,user)
  await page.getByRole('link',{name:'＋ 创建旅行',exact:true}).click()
  await page.getByRole('textbox',{name:'旅行名称',exact:true}).fill('可靠创建隔离验收')
  return user
}
async function loseFirstResponse(page) {
  const state={bodies:[],statuses:[],ids:[]}
  await page.route('**/travel/api/trips/create',async route=>{
    state.bodies.push(route.request().postDataJSON())
    const saved=await route.fetch()
    state.statuses.push(saved.status());state.ids.push((await saved.json()).id)
    if(state.bodies.length===1)await route.abort('failed')
    else await route.fulfill({response:saved})
  })
  return state
}
async function failFirst(page) {
  await page.getByRole('button',{name:'创建旅行 →',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('结果暂时无法确认')
  await expect(page.getByRole('button',{name:'继续这次创建',exact:true})).toBeEnabled()
}
async function trips(request,user) {
  return (await (await request.get(ORIGIN+'/travel/api/trips',{headers:user.auth})).json()).items
}
async function accept(page,trigger,type='confirm') {
  const handled=page.waitForEvent('dialog').then(async dialog=>{
    const actual=dialog.type();await dialog.accept();expect(actual).toBe(type)
  })
  await Promise.all([handled,trigger()])
}

test('已提交但响应丢失：继续使用同次信息，只返回一份旅行',async({page,request})=>{
  const user=await setup(page,request), state=await loseFirstResponse(page)
  await failFirst(page)
  await expect(page.getByRole('textbox',{name:'旅行名称',exact:true})).toHaveAttribute('readonly','')
  expect(await trips(request,user)).toHaveLength(1)
  await page.getByRole('button',{name:'继续这次创建',exact:true}).click()
  await expect(page.getByRole('heading',{name:'可靠创建隔离验收',exact:true})).toBeVisible()
  expect(state.statuses).toEqual([201,200])
  expect(state.bodies[0]).toEqual(state.bodies[1])
  expect(state.ids[0]).toBe(state.ids[1])
  expect(await trips(request,user)).toHaveLength(1)
  await page.getByRole('link',{name:'← 我的旅行',exact:true}).click()
  await page.getByRole('link',{name:'＋ 创建旅行',exact:true}).click()
  await expect(page.getByRole('textbox',{name:'旅行名称',exact:true})).toHaveValue('')
  await expect(page.getByRole('button',{name:'继续这次创建',exact:true})).toHaveCount(0)
})

test('刷新恢复同次创建，并打开已被编辑过的旅行而不覆盖新内容',async({page,request},testInfo)=>{
  const user=await setup(page,request),state=await loseFirstResponse(page)
  await failFirst(page)
  const saved=(await trips(request,user))[0]
  expect((await request.patch(ORIGIN+`/travel/api/trips/${saved.id}`,{headers:user.auth,
    data:{version:saved.version,title:'已从另一处改名'}})).status()).toBe(200)
  await accept(page,()=>page.reload(),'beforeunload')
  await expect(page.getByRole('button',{name:'继续这次创建',exact:true})).toBeVisible()
  await expect(page.getByRole('textbox',{name:'旅行名称',exact:true})).toHaveValue('可靠创建隔离验收')
  await page.getByRole('button',{name:'继续这次创建',exact:true}).evaluate(button=>button.scrollIntoView({block:'center'}))
  await page.screenshot({path:testInfo.outputPath('creation-resumed.png')})
  await page.getByRole('button',{name:'继续这次创建',exact:true}).click()
  await expect(page.getByRole('heading',{name:'已从另一处改名',exact:true})).toBeVisible()
  expect(state.bodies[0].request_id).toBe(state.bodies[1].request_id)
  expect((await trips(request,user))).toHaveLength(1)
})

test('服务器缺少可靠创建能力时不发送创建请求，也不走旧接口',async({page,request})=>{
  const user=await setup(page,request), writes=[]
  page.on('request',req=>{if(req.method()==='POST'&&/\/api\/trips(?:\/create)?$/.test(req.url()))writes.push(req.url())})
  await page.route('**/travel/api/trips/create-capabilities',route=>route.fulfill({status:404,json:{detail:'Old server'}}))
  await page.getByRole('button',{name:'创建旅行 →',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('尚不支持可靠创建')
  expect(writes).toEqual([])
  expect(await trips(request,user)).toHaveLength(0)
})

test('专用创建接口缺失时保留尝试，不降级；恢复后继续原尝试',async({page,request})=>{
  const user=await setup(page,request), bodies=[], legacy=[]
  page.on('request',req=>{
    if(req.method()==='POST'&&req.url().endsWith('/api/trips/create'))bodies.push(req.postDataJSON())
    if(req.method()==='POST'&&req.url().endsWith('/api/trips'))legacy.push(req.url())
  })
  const unsupported=route=>route.fulfill({status:405,json:{detail:'Old route'}})
  await page.route('**/travel/api/trips/create',unsupported)
  await page.getByRole('button',{name:'创建旅行 →',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('尚不支持可靠创建')
  expect(await trips(request,user)).toHaveLength(0)
  await page.unroute('**/travel/api/trips/create',unsupported)
  await page.getByRole('button',{name:'继续这次创建',exact:true}).click()
  await expect(page.getByRole('heading',{name:'可靠创建隔离验收',exact:true})).toBeVisible()
  expect(bodies[0].request_id).toBe(bodies[1].request_id)
  expect(legacy).toEqual([])
  expect(await trips(request,user)).toHaveLength(1)
})

test('切换账号不带入另一账号的创建信息，切回后可继续原旅行',async({page,request})=>{
  const owner=await setup(page,request),other=await account(request),state=await loseFirstResponse(page)
  await failFirst(page)
  await accept(page,()=>page.getByRole('button',{name:'退出',exact:true}).click())
  await login(page,other)
  await expect(page.getByRole('textbox',{name:'旅行名称',exact:true})).toHaveValue('')
  await expect(page.getByRole('button',{name:'继续这次创建',exact:true})).toHaveCount(0)
  expect(await trips(request,other)).toHaveLength(0)
  await page.getByRole('button',{name:'退出',exact:true}).click()
  await login(page,owner)
  await expect(page.getByRole('button',{name:'继续这次创建',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'继续这次创建',exact:true}).click()
  await expect(page.getByRole('heading',{name:'可靠创建隔离验收',exact:true})).toBeVisible()
  expect(state.statuses).toEqual([201,200])
  expect(await trips(request,owner)).toHaveLength(1)
})

test('无法保存创建尝试时保留输入且不发送云端创建',async({page,request})=>{
  await page.addInitScript(()=>{
    const original=Storage.prototype.setItem
    Storage.prototype.setItem=function(key,value){
      if(this===sessionStorage&&key.startsWith('travel.trip-creation.v1.'))throw new DOMException('Controlled quota','QuotaExceededError')
      return original.call(this,key,value)
    }
  })
  const user=await setup(page,request),writes=[]
  page.on('request',req=>{if(req.method()==='POST'&&req.url().endsWith('/api/trips/create'))writes.push(req.url())})
  await page.getByRole('button',{name:'创建旅行 →',exact:true}).click()
  await expect(page.getByRole('alert')).toContainText('无法保留这次创建信息')
  await expect(page.getByRole('textbox',{name:'旅行名称',exact:true})).toHaveValue('可靠创建隔离验收')
  expect(writes).toEqual([])
  expect(await trips(request,user)).toHaveLength(0)
})

test('放弃创建的取消分支保留尝试，确认只清除本页信息不删除云端旅行',async({page,request})=>{
  const user=await setup(page,request)
  await loseFirstResponse(page);await failFirst(page)
  const abandon=page.getByRole('button',{name:'放弃并重新填写',exact:true})
  const dismissed=page.waitForEvent('dialog').then(async dialog=>{
    const type=dialog.type();await dialog.dismiss();expect(type).toBe('confirm')
  })
  await Promise.all([dismissed,abandon.click()])
  await expect(page.getByRole('button',{name:'继续这次创建',exact:true})).toBeVisible()
  await expect(page.getByRole('textbox',{name:'旅行名称',exact:true})).toHaveValue('可靠创建隔离验收')
  await accept(page,()=>abandon.click())
  await expect(page.getByRole('textbox',{name:'旅行名称',exact:true})).toHaveValue('')
  await expect(page.getByRole('button',{name:'继续这次创建',exact:true})).toHaveCount(0)
  expect(await trips(request,user)).toHaveLength(1)
})
