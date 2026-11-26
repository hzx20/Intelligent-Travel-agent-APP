import {randomUUID} from 'node:crypto'
import {test,expect} from '@playwright/test'
const ORIGIN='http://127.0.0.1:5264',HOME=ORIGIN+'/travel/',PASSWORD='isolated-spot-password'
const LEAVE='本页还有未保存输入、待确认结果或正在进行的操作。离开后本页内容可能丢失，确定离开吗？'
const CLOSE='加入结果可能已经保存。请先核对待安排清单；关闭只清除本页重试信息，不会撤销地点，确定关闭吗？'
async function localOnly(page){await page.route('**/*',r=>new URL(r.request().url()).origin===ORIGIN?r.continue():r.abort())}
async function account(request){const username='sc_'+randomUUID().replaceAll('-','').slice(0,16);expect((await request.post(HOME+'api/auth/register',{data:{username,password:PASSWORD}})).status()).toBe(201);const r=await request.post(HOME+'api/auth/login',{data:{username,password:PASSWORD}});return {username,auth:{Authorization:'Bearer '+(await r.json()).token}}}
async function login(page,user){await page.getByRole('navigation').first().getByRole('button',{name:'登录',exact:true}).click();const d=page.getByRole('dialog',{name:'登录',exact:true});await d.getByRole('textbox',{name:'用户名',exact:true}).fill(user.username);await d.getByLabel('密码',{exact:true}).fill(PASSWORD);await d.getByRole('button',{name:'登录',exact:true}).last().click();await expect(d).toHaveCount(0)}
async function setup(page,request){await localOnly(page);const user=await account(request);const spots=(await (await request.get(HOME+'api/spots?page_size=10')).json()).items;const first=spots.find(s=>s.name==='隔离景点一'),second=spots.find(s=>s.name==='隔离景点二');expect(first.amap_id).toBe('');const r=await request.post(HOME+'api/trips/create',{headers:user.auth,data:{request_id:randomUUID(),title:'收集验收旅行',days:1}});expect(r.status()).toBe(201);const trip=await r.json();await page.goto(HOME);await login(page,user);await page.goto(HOME+`spots/${first.id}`);await expect(page.getByRole('heading',{name:first.name,exact:true})).toBeVisible();return {user,trip,first,second}}
async function picker(page){await page.getByRole('button',{name:'＋ 加入旅行',exact:true}).click();const d=page.getByRole('dialog',{name:'加入哪段旅行？',exact:true});await expect(d).toBeVisible();return d}
async function choose(page,trip){const d=await picker(page);await d.getByRole('combobox',{name:'选择旅行',exact:true}).selectOption('cloud:'+trip.id);return d}
async function navigate(page,path){await page.evaluate(async path=>{const {default:router}=await import('/travel/src/router/index.js');await router.push(path)},path)}
async function answer(page,trigger,accept,message=LEAVE,type='confirm'){const handled=page.waitForEvent('dialog').then(async d=>{const actual={type:d.type(),message:d.message()};if(accept)await d.accept();else await d.dismiss();expect(actual.type).toBe(type);if(type==='confirm')expect(actual.message).toBe(message)});await Promise.all([handled,trigger()])}
async function loaded(request,user,trip){return (await request.get(HOME+`api/trips/${trip.id}`,{headers:user.auth})).json()}
async function localTrip(page){return page.evaluate(async()=>{const {guestTrips}=await import('/travel/src/store/guestTrips.js');return guestTrips.create({title:'本机收集旅行',days:1})})}

test('原生选择弹窗约束焦点，Esc恢复入口，正常加入账号旅行只写一次',async({page,request},testInfo)=>{
  const {user,trip,first}=await setup(page,request),d=await choose(page,trip)
  expect(await d.evaluate(el=>el.matches(':modal'))).toBe(true)
  await d.getByRole('button',{name:'关闭',exact:true}).focus();await page.keyboard.press('Shift+Tab');await expect(d.getByRole('button',{name:'加入待安排',exact:true})).toBeFocused()
  await page.keyboard.press('Escape');await expect(d).toHaveCount(0);await expect(page.getByRole('button',{name:'＋ 加入旅行',exact:true})).toBeFocused()
  const reopened=await choose(page,trip);await reopened.getByRole('button',{name:'加入待安排',exact:true}).click()
  await expect(reopened).toHaveCount(0);await expect(page.getByRole('status').filter({hasText:'已加入'})).toContainText('收集验收旅行')
  const saved=await loaded(request,user,trip);expect(saved.places).toHaveLength(1);expect(saved.places[0].name).toBe(first.name);await page.screenshot({path:testInfo.outputPath('collection-success.png')})
})

test('云端列表故障保留本机旅行，可加入并重载读取本机地点',async({page,request})=>{
  await setup(page,request);const trip=await localTrip(page)
  await page.route('**/api/trips',r=>r.fulfill({status:503,json:{detail:'隔离列表故障'}}))
  const d=await picker(page);await expect(d.getByRole('alert')).toContainText('隔离列表故障')
  await d.getByRole('combobox',{name:'选择旅行',exact:true}).selectOption('local:'+trip.id);await d.getByRole('button',{name:'加入待安排',exact:true}).click()
  await expect(d).toHaveCount(0);await page.reload()
  const count=await page.evaluate(async id=>{const {guestTrips}=await import('/travel/src/store/guestTrips.js');return (await guestTrips.get(id)).places.length},trip.id);expect(count).toBe(1)
})

test('已提交但响应丢失，核对和重试仍用原版本，不重复无POI标识地点',async({page,request},testInfo)=>{
  const {user,trip}=await setup(page,request),versions=[];let first=true
  await page.route(`**/api/trips/${trip.id}/places`,async r=>{versions.push(r.request().postDataJSON().version);const response=await r.fetch();if(first){first=false;expect(response.status()).toBe(201);return r.abort('failed')}return r.fulfill({response})})
  const d=await choose(page,trip);await d.getByRole('button',{name:'加入待安排',exact:true}).click();await expect(d.getByRole('alert')).toContainText('结果暂时无法确认')
  await expect(d.getByRole('combobox')).toBeDisabled();await d.getByRole('button',{name:'核对待安排清单',exact:true}).click();await expect(d.getByRole('status')).toContainText('原次提交版本保持不变')
  await d.getByRole('button',{name:'继续原次加入',exact:true}).click();await expect(d.getByRole('alert')).toContainText('原次重试不会采用新版本另加一条')
  expect(versions).toEqual([1,1]);expect((await loaded(request,user,trip)).places).toHaveLength(1)
  await page.screenshot({path:testInfo.outputPath('collection-unknown.png')})
  await answer(page,()=>page.keyboard.press('Escape'),false,CLOSE);await expect(d).toBeVisible()
  await answer(page,()=>page.keyboard.press('Escape'),true,CLOSE);await expect(d).toHaveCount(0)
})

test('首次读取旅行时确认换景点，迟到读取不再发送新增',async({page,request})=>{
  const {user,trip,second}=await setup(page,request);let release,arrived,finished,writes=0
  const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),done=new Promise(r=>finished=r)
  await page.route(`**/api/trips/${trip.id}`,async r=>{const response=await r.fetch();arrived();await gate;await r.fulfill({response});finished()})
  page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/places'))writes++})
  try{const d=await choose(page,trip);await d.getByRole('button',{name:'加入待安排',exact:true}).click();await received
    await answer(page,()=>navigate(page,`/spots/${second.id}`),false);await expect(d).toBeVisible()
    await answer(page,()=>navigate(page,`/spots/${second.id}`),true);await expect(page.getByRole('heading',{name:second.name,exact:true})).toBeVisible()
    release();await done;expect(writes).toBe(0);expect((await loaded(request,user,trip)).places).toHaveLength(0)
  }finally{release()}
})

test('写入已发送后确认离开，旧成功不能在新景点显示已加入',async({page,request})=>{
  const {user,trip,first,second}=await setup(page,request);let release,arrived,finished
  const gate=new Promise(r=>release=r),received=new Promise(r=>arrived=r),done=new Promise(r=>finished=r)
  await page.route(`**/api/trips/${trip.id}/places`,async r=>{const response=await r.fetch();arrived();await gate;await r.fulfill({response});finished()})
  try{const d=await choose(page,trip);await d.getByRole('button',{name:'加入待安排',exact:true}).click();await received;await page.keyboard.press('Escape');await expect(d).toBeVisible()
    await answer(page,()=>navigate(page,`/spots/${second.id}`),true);release();await done
    await expect(page.getByRole('heading',{name:second.name,exact:true})).toBeVisible();await expect(page.getByRole('status').filter({hasText:'已加入'})).toHaveCount(0)
    expect((await loaded(request,user,trip)).places[0].name).toBe(first.name)
  }finally{release()}
})

test('不完整成功响应保留原次加入，不显示虚假成功',async({page,request})=>{
  const {user,trip}=await setup(page,request)
  await page.route(`**/api/trips/${trip.id}/places`,async r=>{const response=await r.fetch();expect(response.status()).toBe(201);return r.fulfill({status:200,json:{}})})
  const d=await choose(page,trip);await d.getByRole('button',{name:'加入待安排',exact:true}).click()
  await expect(d.getByRole('alert')).toContainText('无法确认');await expect(d.getByRole('button',{name:'继续原次加入',exact:true})).toBeVisible();expect((await loaded(request,user,trip)).places).toHaveLength(1)
})

test('换号后旧弹窗不发送新增，显式同步后只显示新账号旅行',async({page,request,context})=>{
  const {user,trip}=await setup(page,request),d=await choose(page,trip),second=await account(request);let writes=0
  page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/places'))writes++})
  const other=await context.newPage();await localOnly(other);await other.goto(HOME);await other.getByRole('button',{name:'退出',exact:true}).click();await login(other,second)
  await d.getByRole('button',{name:'加入待安排',exact:true}).click();await expect(d.getByRole('alert')).toContainText('登录状态');expect(writes).toBe(0)
  await page.keyboard.press('Escape');await page.getByRole('button',{name:'同步账号',exact:true}).click()
  await expect(page.getByRole('button',{name:/^(同步账号|正在同步…)$/})).toHaveCount(0)
  await expect(page.getByRole('heading',{name:'隔离景点一',exact:true})).toBeVisible()
  const reopened=await picker(page);await expect(reopened.getByText('还没有旅行，可以先创建一段。',{exact:true})).toBeVisible()
  expect((await loaded(request,user,trip)).places).toHaveLength(0);await other.close()
})

test('评论草稿与评分保护离开和刷新，确认换景点才清除',async({page,request})=>{
  const {second}=await setup(page,request),text=page.getByRole('textbox',{name:'游览评论',exact:true})
  await text.fill('未发布的评论');await page.getByRole('button',{name:'评分 4 星',exact:true}).click()
  await answer(page,()=>navigate(page,`/spots/${second.id}`),false);await expect(text).toHaveValue('未发布的评论')
  await answer(page,()=>page.evaluate(()=>location.reload()),false,null,'beforeunload');await expect(text).toHaveValue('未发布的评论')
  await answer(page,()=>navigate(page,`/spots/${second.id}`),true);await expect(text).toHaveValue('');await expect(page.getByRole('button',{name:'评分 4 星',exact:true})).toHaveAttribute('aria-pressed','false')
})

test('收藏响应丢失后先核对状态，不重复切换成未收藏',async({page,request})=>{
  const {user,first}=await setup(page,request);let writes=0
  await page.route(`**/api/spots/${first.id}/favorite-state`,async r=>{writes++;expect(r.request().method()).toBe('PUT');await r.fetch();await r.abort('failed')})
  await page.getByRole('button',{name:/♡ 收藏/}).click();await expect(page.getByRole('alert')).toContainText('结果暂时无法确认')
  await expect(page.getByRole('button',{name:/♡ 收藏/})).toBeDisabled();await page.getByRole('button',{name:'核对收藏和评论',exact:true}).click()
  await expect(page.getByRole('button',{name:/❤ 已收藏/})).toBeEnabled();expect(writes).toBe(1)
  const data=await (await request.get(HOME+`api/spots/${first.id}`,{headers:user.auth})).json();expect(data.favorited).toBe(true)
})

test('评论已提交后丢响应保留原次发布，核对不自动重发',async({page,request})=>{
  const {first}=await setup(page,request),content='评论结果核对 '+randomUUID();let writes=0
  await page.route(`**/api/spots/${first.id}/comments/create`,async r=>{writes++;const response=await r.fetch();expect(response.status()).toBe(201);await r.abort('failed')})
  const text=page.getByRole('textbox',{name:'游览评论',exact:true});await text.fill(content);await page.getByRole('button',{name:'发布评论',exact:true}).click()
  await expect(text).toHaveValue(content);await expect(text).toHaveAttribute('readonly','');await expect(page.getByRole('button',{name:'继续这次发布',exact:true})).toBeEnabled()
  await page.getByRole('button',{name:'核对收藏和评论',exact:true}).click();await expect(page.locator('.comments').getByText(content,{exact:true})).toBeVisible();expect(writes).toBe(1)
  await answer(page,()=>page.getByRole('button',{name:'放弃本次发布',exact:true}).click(),false,'这次评论可能已经发布。放弃只清除本标签重试信息，不删除评论；请先核对评论列表，确定放弃吗？')
  await expect(text).toHaveAttribute('readonly','')
})
