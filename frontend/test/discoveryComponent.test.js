import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,createVNode,nextTick,ref,reactive} from 'vue'
const tick=async()=>{await new Promise(r=>setImmediate(r));await nextTick()}
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve}}
const spot={id:1,name:'景点',city:'杭州',tags:'风景',is_free:true}
async function mount(file,api={},router={}){
  const user=ref(null),guards={},events=new Map(),navigations=[];const route=reactive({query:{},fullPath:'/spots'})
  const context={user,guards,route,router:{push:async x=>{navigations.push(x)},...router},api:{get:async p=>p.includes('regions')?{cities:[],total:0}:{items:[spot],total:1,page:1,page_size:12},...api}}
  globalThis.__discovery=context;globalThis.window={addEventListener:(k,f)=>events.set(k,f),removeEventListener:k=>events.delete(k),confirm:()=>false,scrollTo(){}}
  const url=new URL('../src/'+file,import.meta.url),{descriptor}=parse(await readFile(url,'utf8'))
  const code=compileScript(descriptor,{id:'discovery-test'}).content
    .replace(/from ['"]vue['"]/g,`from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace(/import \{[^\n]+\} from ['"]vue-router['"]/,`const useRouter=()=>globalThis.__discovery.router,useRoute=()=>globalThis.__discovery.route,onBeforeRouteLeave=f=>globalThis.__discovery.guards.leave=f`)
    .replace(/import (SpotCard|PlannerHero|RegionPicker|TripMap|FootprintPhoto) from [^\n]+/g,(_,name)=>`const ${name}={}`)
    .replace(/import \{\s*api\s*\} from ['"]\.\.\/api['"]/, 'const api=globalThis.__discovery.api')
    .replace(/import \{\s*useAuth\s*\} from ['"]\.\.\/store\/auth['"]/, 'const useAuth=()=>({user:globalThis.__discovery.user,openLogin(){}})')
    .replace(/import \{\s*registerAuthChangeGuard\s*\} from ['"]\.\.\/store\/authChangeGuard['"]/, 'const registerAuthChangeGuard=f=>{globalThis.__discovery.guards.auth=f;return ()=>delete globalThis.__discovery.guards.auth}')
    .replace(/from ['"](\.\.[^'"]+)['"]/g,(_,path)=>`from ${JSON.stringify(new URL(path+'.js',url).href)}`)
  const {default:c}=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}#${Math.random()}`);c.render=()=>null
  const renderer=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createText:()=>({}),setText(){},createElement:()=>({}),setElementText(){},patchProp(){}})
  const vnode=createVNode(c),root={};renderer.render(vnode,root);await tick()
  return {s:vnode.component.setupState,user,guards,events,navigations,route,unmount(){renderer.render(null,root)}}
}
test('灵感和规划入口组件行为',async t=>{
  const oldWindow=globalThis.window
  try{
    await t.test('目的地故障不阻止推荐，个人推荐换号时清空并拒绝旧响应',async()=>{
      const late=deferred();let privateCalls=0
      const m=await mount('views/HomeView.vue',{get:async p=>{if(p.includes('regions'))throw Error('目的地故障');if(p.includes('for-you?')&&++privateCalls===1)return late.promise;return {items:[spot],total:1,page:1,page_size:12}}})
      try{assert.equal(m.s.guess.length,1);assert.match(m.s.regionState.error,/目的地故障/);m.user.value={id:1};await tick();assert.equal(m.s.guess.length,0);m.user.value={id:2};await tick();late.resolve({items:[{...spot,name:'旧账号'}],total:1,page:1,page_size:12});await tick();assert.equal(m.s.guess[0].name,'景点')}finally{m.unmount()}
    })
    await t.test('城市预填后改天数使用新条件，示例清掉旧城市参数',async()=>{
      const m=await mount('components/PlannerHero.vue')
      try{m.s.prefill('杭州');m.s.days=5;assert.equal(m.s.composed,'去杭州玩 5 天');await m.s.submit();assert.equal(m.navigations[0].query.days,'5');window.confirm=()=>true;m.s.useExample({q:'西安 2 天'});await m.s.submit();assert.equal(m.navigations[1].query.d,undefined);assert.equal(m.navigations[1].query.q,'西安 2 天')}finally{m.unmount()}
    })
    await t.test('取消替换/离开/换号保护原文字，刷新提示及卸载清理',async()=>{
      const m=await mount('components/PlannerHero.vue')
      m.s.text='自己的需求';assert.equal(m.s.prefill('杭州'),false);m.s.useExample({q:'替换需求'});assert.equal(m.s.text,'自己的需求');assert.equal(m.guards.leave(),false);assert.equal(m.guards.auth(),false)
      let prevented=false;m.events.get('beforeunload')({preventDefault(){prevented=true}});assert(prevented);m.unmount();assert.equal(m.events.size,0);assert.equal(m.guards.auth,undefined)
    })
    await t.test('手动目的地可组成需求；跳转等待期间锁定重复提交',async()=>{
      const pending=deferred(),calls=[];const m=await mount('components/PlannerHero.vue',{}, {push:q=>{calls.push(q);return pending.promise}})
      try{m.s.regionDraft='未收录城市';const sending=m.s.submit();await m.s.submit();assert.equal(calls.length,1);assert.equal(calls[0].query.q,'去未收录城市玩 3 天');assert.equal(m.guards.leave(),true);pending.resolve();await sending;m.user.value={id:3};await tick();assert.equal(m.s.hasDraft,false)}finally{m.unmount()}
    })
    await t.test('足迹刷新失败保留筛选和选中地点，换号清除',async()=>{
      const f={id:1,trip_id:4,trip_title:'旅行',city:'杭州',place_name:'湖边',photo_ids:[],location_status:'associated',lng:120,lat:30};let fail=false
      const m=await mount('views/FootprintsView.vue',{get:async()=>{if(fail)throw Error('足迹故障');return {items:[f]}}})
      try{m.user.value={id:1};await tick();m.s.city='杭州';m.s.tripId='4';m.s.selected=1;fail=true;await m.s.resource.load();assert.equal(m.s.records.length,1);assert.equal(m.s.city,'杭州');assert.equal(m.s.selected,1);m.user.value=null;await tick();assert.equal(m.s.records.length,0);assert.equal(m.s.city,'');assert.equal(m.s.selected,null)}finally{m.unmount()}
    })
  }finally{globalThis.window=oldWindow;delete globalThis.__discovery}
})
