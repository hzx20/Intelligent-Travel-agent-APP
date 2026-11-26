import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,createVNode,nextTick,reactive,ref} from 'vue'

const settle=async()=>{await new Promise(resolve=>setImmediate(resolve));await nextTick()}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
const revision='55555555-5555-4555-8555-555555555555'
const guide=id=>({id,title:'攻略'+id,content:'正文'+id,is_owner:true,is_draft:false,images:[],spots:[],version:1,revision})
async function mountSurface(kind,api={},guestTrips={list:async()=>({items:[]})}){
  const user=ref({id:1}),route=reactive({params:{id:'1'}}),guards={},listeners=new Map(),navigations=[]
  globalThis.window={addEventListener:(k,f)=>listeners.set(k,f),removeEventListener:k=>listeners.delete(k),confirm:()=>true}
  const context={api,user,route,guestTrips,guards,router:{push:async path=>{if(!guards.leave||guards.leave())navigations.push(path)}}}
  globalThis.__guideDetailTest=context
  const url=new URL(kind==='detail'?'../src/views/GuideDetailView.vue':'../src/components/GuideToTrip.vue',import.meta.url)
  const {descriptor}=parse(await readFile(url,'utf8'))
  const code=compileScript(descriptor,{id:'guide-detail-regression'}).content
    .replace(/from ['"]vue['"]/g,`from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace(/import \{[^\n]+\} from ['"]vue-router['"]/,`const {guards}=globalThis.__guideDetailTest; const useRoute=()=>globalThis.__guideDetailTest.route,useRouter=()=>globalThis.__guideDetailTest.router,onBeforeRouteLeave=f=>guards.leave=f,onBeforeRouteUpdate=f=>guards.update=f`)
    .replace(/import \{ api \} from ['"]\.\.\/api['"]/, 'const api=globalThis.__guideDetailTest.api')
    .replace(/import \{ useAuth \} from ['"]\.\.\/store\/auth['"]/, 'const useAuth=()=>({user:globalThis.__guideDetailTest.user})')
    .replace(/import GuideToTrip from [^\n]+/,'const GuideToTrip={}')
    .replace(/import TripEditDialog from [^\n]+/,'const TripEditDialog={}')
    .replace(/import \{ guestTrips, isGuestTripId \} from [^\n]+/,`const guestTrips=globalThis.__guideDetailTest.guestTrips,isGuestTripId=id=>typeof id==='string'&&/^guest-[a-zA-Z0-9-]+$/.test(id)`)
    .replace(/import \{ registerAuthChangeGuard \} from ['"]\.\.\/store\/authChangeGuard['"]/, 'const registerAuthChangeGuard=f=>{guards.auth=f;return ()=>delete guards.auth}')
    .replace(/from ['"](\.\.[^'"]+)['"]/g,(_,path)=>`from ${JSON.stringify(new URL(path+'.js',url).href)}`)
  const {default:component}=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}#${Math.random()}`);component.render=()=>null
  const renderer=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createText:()=>({}),setText(){},createElement:()=>({}),setElementText(){},patchProp(){}})
  const root={},props=kind==='detail'?{}:{guideId:1,guideIsDraft:false,disabled:false}
  let vnode=createVNode(component,{...props});renderer.render(vnode,root);await settle()
  return {state:vnode.component.setupState,user,route,guards,listeners,navigations,
    update(next){Object.assign(props,next);vnode=createVNode(component,{...props});renderer.render(vnode,root)},unmount(){renderer.render(null,root)}}
}

test('攻略详情和旅行选择的异步上下文',async t=>{
  const previousWindow=globalThis.window
  try{
    await t.test('复用攻略路由后拒绝旧读取，画廊重置',async()=>{
      const old=deferred(),m=await mountSurface('detail',{get:path=>path.endsWith('/1')?old.promise:Promise.resolve(guide(2))})
      try{m.state.gi=3;m.route.params.id='2';await settle();assert.equal(m.state.guide.id,2);assert.equal(m.state.gi,0);old.resolve(guide(1));await settle();assert.equal(m.state.guide.id,2)}finally{m.unmount()}
    })
    await t.test('账号切换后旧所有者响应不能恢复管理权限',async()=>{
      const old=deferred();let reads=0
      const m=await mountSurface('detail',{get:()=>++reads===1?old.promise:Promise.resolve({...guide(1),is_owner:false})})
      try{m.user.value={id:2};await settle();assert.equal(m.state.guide.is_owner,false);old.resolve(guide(1));await settle();assert.equal(m.state.guide.is_owner,false)}finally{m.unmount()}
    })
    await t.test('不匹配编号的成功响应不可显示或删除',async()=>{
      let writes=0;const m=await mountSurface('detail',{get:async()=>guide(2),del:async()=>writes++})
      try{assert.equal(m.state.guide,null);assert.match(m.state.error,/无法确认/);await m.state.removeGuide();assert.equal(writes,0)}finally{m.unmount()}
    })
    await t.test('删除结果未知先核对，重复点击不重发，核对404清掉旧正文',async()=>{
      let writes=0,removed=false
      const m=await mountSurface('detail',{get:async()=>{if(removed){const e=Error('攻略不存在');e.status=404;throw e}return guide(1)},del:async()=>{writes++;removed=true;const e=Error('结果未知');e.outcomeUnknown=true;throw e}})
      try{await m.state.removeGuide();await m.state.removeGuide();assert.equal(writes,1);assert.equal(m.state.deletionUncertain,true);assert.equal(m.state.guide.id,1);await m.state.load();assert.equal(m.state.guide,null);assert.equal(m.state.error,'攻略不存在')}finally{m.unmount()}
    })
    await t.test('删除等待保护路由/换号/刷新，确认换攻略后迟到不跳转',async()=>{
      const pending=deferred(),m=await mountSurface('detail',{get:async path=>guide(Number(path.split('/').at(-1))),del:()=>pending.promise})
      try{
        const deleting=m.state.removeGuide();window.confirm=()=>false;assert.equal(m.guards.leave(),false);assert.equal(m.guards.auth(),false)
        let prevented=false;m.listeners.get('beforeunload')({preventDefault(){prevented=true}});assert(prevented)
        m.route.params.id='2';await settle();pending.resolve(null);await deleting
        assert.equal(m.state.guide.id,2);assert.equal(m.state.deleting,false);assert.deepEqual(m.navigations,[])
      }finally{m.unmount()}
      assert.equal(m.listeners.size,0);assert.equal(m.guards.auth,undefined)
    })
    await t.test('已知删除成功正常离开，不再次确认；卸载后删除迟到不导航',async()=>{
      let confirmations=0;const m=await mountSurface('detail',{get:async()=>guide(1),del:async()=>null})
      try{window.confirm=()=>{confirmations++;return true};await m.state.removeGuide();assert.equal(confirmations,1);assert.deepEqual(m.navigations,['/guides'])}finally{m.unmount()}
      const pending=deferred(),late=await mountSurface('detail',{get:async()=>guide(1),del:()=>pending.promise})
      const deleting=late.state.removeGuide();late.unmount();pending.resolve(null);await deleting;assert.deepEqual(late.navigations,[])
    })
    await t.test('旅行选择关闭和重开后迟到列表不能污染新选择',async()=>{
      const old=deferred();let reads=0
      const m=await mountSurface('picker',{get:()=>++reads===1?old.promise:Promise.resolve({items:[{id:2,title:'新旅行'}]})})
      try{const a=m.state.show();m.state.close();await m.state.show();m.state.selected='2';old.resolve({items:[{id:1,title:'旧旅行'}]});await a;assert.deepEqual(m.state.trips.map(x=>x.id),[2]);assert.equal(m.state.selected,'2')}finally{m.unmount()}
    })
    await t.test('账号/攻略/草稿可见性或禁用变化立即关闭选择并丢弃旧列表',async()=>{
      const pending=deferred(),m=await mountSurface('picker',{get:()=>pending.promise})
      try{
        const loading=m.state.show();m.update({guideIsDraft:true});await settle();pending.resolve({items:[{id:1,title:'旧旅行'}]});await loading
        assert.equal(m.state.open,false);assert.deepEqual(m.state.trips,[])
        await m.state.show();m.state.selected='1';m.update({disabled:true});await settle();m.state.proceed();assert.deepEqual(m.navigations,[]);assert.equal(m.state.selected,'')
        m.update({disabled:false,guideId:2});await settle();await m.state.show();m.user.value={id:2};assert.equal(m.state.open,false);assert.equal(m.state.error,'')
      }finally{m.unmount()}
    })
    await t.test('公开攻略部分列表失败可保留本机旅行，草稿不读取本机列表',async()=>{
      let localReads=0
      const m=await mountSurface('picker',{get:async()=>{throw Error('cloud')}},{list:async()=>{localReads++;return {items:[{id:'guest-one',title:'本机旅行'}]}}})
      try{
        await m.state.show();assert.equal(m.state.trips.length,1);assert.match(m.state.error,/部分旅行/)
        m.state.selected='guest-one';m.state.proceed();assert.deepEqual(m.navigations,[{path:'/trips/guest-one',query:{guide:'1'}}])
        m.update({guideIsDraft:true});await settle();await m.state.show();assert.equal(localReads,1);assert.deepEqual(m.state.trips,[])
      }finally{m.unmount()}
    })
    await t.test('卸载后旅行列表迟到不重开弹窗或继续导航',async()=>{
      const pending=deferred(),m=await mountSurface('picker',{get:()=>pending.promise})
      const loading=m.state.show();m.unmount();pending.resolve({items:[{id:1,title:'迟到'}]});await loading
      assert.deepEqual(m.state.trips,[]);assert.deepEqual(m.navigations,[])
    })
  }finally{globalThis.window=previousWindow;delete globalThis.__guideDetailTest}
})
