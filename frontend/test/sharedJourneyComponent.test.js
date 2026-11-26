import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,createVNode,nextTick,reactive,ref} from 'vue'
import {validSharedJourney,validShareToken,validInvitationReceipt,validInvitationStatus} from '../src/utils/sharedJourney.js'

const settle=async()=>{await new Promise(r=>setImmediate(r));await nextTick()}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const trip=(title='合成旅行')=>({title,destination:'杭州',start_date:null,days:[{day_number:1,theme:null,items:[{name:'合成公园',kind:'地点'}]}]})
async function mountShared(api,mode='view'){
  const user=ref({id:1}),route=reactive({hash:'#'+'a'.repeat(43)}),guards={},navigations=[],listeners=new Map()
  const context={api,user,route,guards,router:{replace:async path=>navigations.push(path)}}
  globalThis.__sharedJourneyTest=context
  globalThis.window={confirm:()=>false,addEventListener:(k,f)=>listeners.set(k,f),removeEventListener:k=>listeners.delete(k)}
  const url=new URL('../src/views/TripSharedView.vue',import.meta.url),{descriptor}=parse(await readFile(url,'utf8'))
  const code=compileScript(descriptor,{id:'shared-regression'}).content
    .replace(/from ['"]vue['"]/g,`from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace(/import \{[^\n]+\} from ['"]vue-router['"]/,`const {guards}=globalThis.__sharedJourneyTest;const useRoute=()=>globalThis.__sharedJourneyTest.route,useRouter=()=>globalThis.__sharedJourneyTest.router,onBeforeRouteLeave=f=>guards.leave=f,onBeforeRouteUpdate=f=>guards.update=f`)
    .replace(/import \{ api \} from ['"]\.\.\/api['"]/, 'const api=globalThis.__sharedJourneyTest.api')
    .replace(/import \{ useAuth \} from ['"]\.\.\/store\/auth['"]/, 'const useAuth=()=>({user:globalThis.__sharedJourneyTest.user,openLogin(){}})')
    .replace(/import \{ registerAuthChangeGuard \} from ['"]\.\.\/store\/authChangeGuard['"]/, 'const registerAuthChangeGuard=f=>{guards.auth=f;return ()=>delete guards.auth}')
    .replace(/from ['"](\.\.[^'"]+)['"]/g,(_,p)=>`from ${JSON.stringify(new URL(p+'.js',url).href)}`)
  const {default:component}=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}#${Math.random()}`);component.render=()=>null
  const renderer=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createText:()=>({}),setText(){},createElement:()=>({}),setElementText(){},patchProp(){}})
  const root={},vnode=createVNode(component,{mode});renderer.render(vnode,root);await settle()
  return {state:vnode.component.setupState,user,route,guards,navigations,listeners,unmount:()=>renderer.render(null,root)}
}

test('分享内容与邀请回执拒绝损坏结构和危险跳转',()=>{
  assert(validSharedJourney(trip()));assert(!validSharedJourney({...trip(),days:null}));assert(!validSharedJourney({...trip(),days:[...trip().days,...trip().days]}))
  assert(!validSharedJourney({...trip(),start_date:'2026-02-31'}));assert(!validSharedJourney({...trip(),start_date:'tomorrow'}))
  assert(!validSharedJourney({...trip(),days:[{day_number:1,items:[{name:'公园',kind:'地点',duration_min:'20'}]}]}))
  assert(validShareToken('a'.repeat(43)));assert(!validShareToken('../trips/1'))
  assert(validInvitationReceipt({trip_id:1,role:'editor'}));assert(!validInvitationReceipt({trip_id:'../../me',role:'editor'}))
  assert(validInvitationStatus({state:'not_joined'}));assert(!validInvitationStatus({state:'not_joined',trip_id:1}));assert(!validInvitationStatus({state:'joined',trip_id:1,role:'visitor'}))
})

test('分享接收页异步读取与原账号邀请恢复',async t=>{
  const previous=globalThis.window
  try{
    await t.test('暂时失败和坏200保留旧内容，撤销404清空',async()=>{
      let result=trip(),failure
      const m=await mountShared({post:async()=>{if(failure)throw failure;return result}})
      try{failure=Object.assign(Error('offline'),{status:503});await m.state.load();assert.equal(m.state.trip.title,'合成旅行');assert.equal(m.state.retryable,true)
        failure=null;result={days:null};await m.state.load();assert.equal(m.state.trip.title,'合成旅行')
        failure=Object.assign(Error('revoked'),{status:404});await m.state.load();assert.equal(m.state.trip,null);assert.equal(m.state.retryable,false)
      }finally{m.unmount()}
    })
    await t.test('切换链接清旧内容，迟到结果不能覆盖新分享',async()=>{
      const held=deferred();let calls=0
      const m=await mountShared({post:async()=>++calls===1?held.promise:trip('新链接旅行')})
      try{m.route.hash='#'+'b'.repeat(43);await settle();assert.equal(m.state.trip.title,'新链接旅行');held.resolve(trip('旧链接旅行'));await settle();assert.equal(m.state.trip.title,'新链接旅行')}finally{m.unmount()}
    })
    await t.test('未知接受必须先核对，只读核对成功不重复接受也不自动跳页',async()=>{
      const calls=[],m=await mountShared({post:async(path)=>{calls.push(path);if(path.endsWith('/accept'))throw Error('response lost');return {state:'joined',trip_id:3,role:'editor'}}},'edit')
      try{await m.state.accept();assert(m.state.pending);assert.equal(m.guards.leave(),false);await m.state.accept();assert.equal(calls.length,1)
        await m.state.checkMembership();assert.equal(calls.length,2);assert.equal(m.state.pending,null);assert.equal(m.state.receipt.trip_id,3);assert.deepEqual(m.navigations,[]);assert.equal(m.guards.leave(),true)
      }finally{m.unmount()}
    })
    await t.test('核对未加入后才允许原账号显式继续',async()=>{
      let accepts=0;const m=await mountShared({post:async path=>{if(path.endsWith('/invitation-status'))return {state:'not_joined'};if(++accepts===1)throw Error('offline');return {trip_id:3,role:'editor'}}},'edit')
      try{await m.state.accept();await m.state.checkMembership();assert.equal(accepts,1);assert(m.state.checked);await m.state.accept();assert.equal(accepts,2);assert.deepEqual(m.navigations,['/trips/3'])}finally{m.unmount()}
    })
    await t.test('错误成功回执保留待核对，旧服务核对404不降级重发',async()=>{
      let calls=0;const m=await mountShared({post:async path=>{calls++;if(path.endsWith('/accept'))return {trip_id:'../me',role:'editor'};throw Object.assign(Error('Not Found'),{status:404})}},'edit')
      try{await m.state.accept();assert(m.state.pending);assert.deepEqual(m.navigations,[]);await m.state.checkMembership();assert.match(m.state.error,/不支持核对/);assert(m.state.pending);await m.state.accept();assert.equal(calls,2)}finally{m.unmount()}
    })
    await t.test('换账号后旧接受回执既不跳页，也不取消新账号核对状态',async()=>{
      const held=deferred(),m=await mountShared({post:async path=>path.endsWith('/accept')?held.promise:{state:'not_joined'}},'edit')
      try{const first=m.state.accept();m.user.value={id:2};await settle();await m.state.checkMembership();assert(m.state.checked);held.resolve({trip_id:3,role:'editor'});await first;assert.deepEqual(m.navigations,[]);assert.equal(m.state.receipt,null);assert(m.state.checked)}finally{m.unmount()}
    })
    await t.test('路由及刷新保护仅在结果未确认时生效，卸载移除监听',async()=>{
      const m=await mountShared({post:async()=>{throw Error('offline')}},'edit')
      await m.state.accept();assert.equal(m.guards.auth(),false);assert.equal(m.guards.update({path:'/invite',hash:'#b'},{path:'/invite',hash:'#a'}),false)
      let prevented=false;m.listeners.get('beforeunload')({preventDefault(){prevented=true}});assert(prevented);m.unmount();assert.equal(m.listeners.size,0);assert.equal(m.guards.auth,undefined)
    })
  }finally{globalThis.window=previous}
})
