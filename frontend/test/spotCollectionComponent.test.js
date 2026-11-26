import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,createVNode,nextTick,reactive,ref} from 'vue'
const settle=async()=>{await new Promise(r=>setImmediate(r));await nextTick()}
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}
const detail=id=>({spot:{id,name:'景点'+id,amap_id:''},address:'地址',source:'景点库',favorited:false,favorite_count:0,favorite_version:0,comments:[]})
const trip=(version=1,places=[])=>({id:1,title:'旅行',version,places})
const place=id=>({id:1,name:'景点'+id,amap_id:'',address:'地址'})
async function mount(kind,api,guestTrips={list:async()=>({items:[]})}){
  const user=ref({id:1}),route=reactive({params:{id:'1'}}),guards={},listeners=new Map(),dirty=[]
  globalThis.window={confirm:()=>true,addEventListener:(k,f)=>listeners.set(k,f),removeEventListener:k=>listeners.delete(k)}
  const storage=new Map();globalThis.sessionStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}
  globalThis.__spotTest={api:{...api,get:path=>path==='/api/spots/write-capabilities'?Promise.resolve({idempotent_comments:true,versioned_favorites:true}):api.get(path)},guestTrips,user,route,guards}
  const url=new URL(kind==='parent'?'../src/views/SpotDetailView.vue':'../src/components/SpotToTrip.vue',import.meta.url)
  const {descriptor}=parse(await readFile(url,'utf8'))
  const code=compileScript(descriptor,{id:'spot-regression'}).content
    .replace(/from ['"]vue['"]/g,`from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace(/import \{[^\n]+\} from ['"]vue-router['"]/,`const {guards}=globalThis.__spotTest; const useRoute=()=>globalThis.__spotTest.route,onBeforeRouteLeave=f=>guards.leave=f,onBeforeRouteUpdate=f=>guards.update=f`)
    .replace(/import \{ api(?:, BASE)? \} from ['"]\.\.\/api['"]/, 'const api=globalThis.__spotTest.api,BASE=""')
    .replace(/import \{ useAuth \} from ['"]\.\.\/store\/auth['"]/, 'const useAuth=()=>({user:globalThis.__spotTest.user,openLogin(){}})')
    .replace(/import \{ guestTrips, isGuestTripId \} from [^\n]+/,`const guestTrips=globalThis.__spotTest.guestTrips,isGuestTripId=id=>typeof id==='string'&&/^guest-[a-zA-Z0-9-]+$/.test(id)`)
    .replace(/import (TripEditDialog|SpotToTrip) from [^\n]+/g,(_,name)=>`const ${name}={}`)
    .replace(/import \{ registerAuthChangeGuard \} from ['"]\.\.\/store\/authChangeGuard['"]/, 'const registerAuthChangeGuard=f=>{guards.auth=f;return ()=>delete guards.auth}')
    .replace(/from ['"](\.\.[^'"]+)['"]/g,(_,path)=>`from ${JSON.stringify(new URL(path+'.js',url).href)}`)
  const {default:component}=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}#${Math.random()}`);component.render=()=>null
  const renderer=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createText:()=>({}),setText(){},createElement:()=>({}),setElementText(){},patchProp(){}})
  const root={},props=kind==='parent'?{}:{detail:detail(1),onDirty:value=>dirty.push(value)}
  let vnode=createVNode(component,{...props});renderer.render(vnode,root);await settle()
  return {state:vnode.component.setupState,user,route,guards,listeners,dirty,update(d){props.detail=d;vnode=createVNode(component,{...props});renderer.render(vnode,root)},unmount(){renderer.render(null,root)}}
}
const apiDefault=()=>({get:async path=>path==='/api/trips'?{items:[trip()]}:trip()})
test('景点收集和详情上下文保护',async t=>{
  const previousWindow=globalThis.window,previousStorage=globalThis.sessionStorage
  try{
    await t.test('关闭加载后迟到列表不回填，部分失败保留本机旅行',async()=>{
      const old=deferred(),api={get:()=>old.promise},m=await mount('child',api,{list:async()=>({items:[{id:'guest-one',title:'本机'}]})})
      try{const loading=m.state.show();m.state.close();old.resolve({items:[trip()]});await loading;assert.equal(m.state.open,false);assert.deepEqual(m.state.cloud,[])
        api.get=async()=>{throw Error('cloud')};await m.state.show();assert.equal(m.state.local.length,1);assert.equal(m.state.cloud.length,0);assert.match(m.state.error,/cloud/)
        api.get=async()=>({});await m.state.show();assert.equal(m.state.local.length,1);assert.deepEqual(m.state.cloud,[]);assert.match(m.state.error,/无法读取旅行列表/)
      }finally{m.unmount()}
    })
    await t.test('首个旅行读取期间卸载，返回后不再发送新增',async()=>{
      const read=deferred();let writes=0
      const m=await mount('child',{get:path=>path==='/api/trips'?Promise.resolve({items:[trip()]}):read.promise,post:async()=>{writes++}})
      await m.state.show();m.state.selected='cloud:1';const adding=m.state.add();m.unmount();read.resolve(trip());await adding;assert.equal(writes,0)
    })
    await t.test('未知结果核对和重试保持同一版本与不可变快照',async()=>{
      let saved=false;const bodies=[],api=apiDefault();api.get=async path=>path==='/api/trips'?{items:[trip()]}:trip(saved?2:1,saved?[place(1)]:[])
      api.post=async(path,body)=>{bodies.push(body);const e=Error(saved?'版本冲突':'断连');if(saved)e.status=409;else{saved=true;e.outcomeUnknown=true}throw e}
      const m=await mount('child',api)
      try{const s=m.state;await s.show();s.selected='cloud:1';await s.add();await s.check();assert.equal(s.checked.version,2);assert.equal(s.pending.payload.version,1);await s.add()
        assert.equal(bodies.length,2);assert.equal(bodies[0],bodies[1]);assert(Object.isFrozen(bodies[0]));assert.match(s.error,/原次重试/);assert.equal(m.dirty.at(-1),true)
        window.confirm=()=>false;s.close();assert.equal(s.open,true);window.confirm=()=>true;s.close();assert.equal(s.pending,null);assert.equal(m.dirty.at(-1),false)
      }finally{m.unmount()}
    })
    await t.test('旧景点新增迟到不清除新景点正在保存的状态',async()=>{
      const a=deferred(),b=deferred();let writes=0;const api=apiDefault();api.post=()=>++writes===1?a.promise:b.promise
      const m=await mount('child',api)
      try{const s=m.state;await s.show();s.selected='cloud:1';const first=s.add();await settle();m.update(detail(2));await settle();await s.show();s.selected='cloud:1';const second=s.add();await settle()
        a.resolve(trip(2,[place(1)]));await first;assert.equal(s.saving,true);assert.equal(s.success,null)
        b.resolve(trip(2,[place(2)]));await second;assert.equal(s.saving,false);assert.equal(s.success.id,'1')
      }finally{m.unmount()}
    })
    await t.test('未列出的目标或不匹配旅行响应不发送写入',async()=>{
      let writes=0;const api=apiDefault();api.post=async()=>{writes++};const m=await mount('child',api)
      try{await m.state.show();m.state.selected='cloud:999';await m.state.add();assert.equal(writes,0);m.state.selected='cloud:1';api.get=async()=>({...trip(),id:9});await m.state.add();assert.equal(writes,0);assert.equal(m.state.pending,null)}finally{m.unmount()}
    })
    await t.test('本机旅行真实返回形状成功后关闭，缺失成功内容保留尝试',async()=>{
      const localTrip={...trip(),id:'guest-one'},m=await mount('child',apiDefault(),{list:async()=>({items:[localTrip]}),get:async()=>localTrip,addPlace:async()=>({...localTrip,version:2,places:[place(1)]})})
      try{await m.state.show();m.state.selected='local:guest-one';await m.state.add();assert.equal(m.state.open,false);assert.equal(m.state.success.id,'guest-one')}finally{m.unmount()}
      const api=apiDefault();api.post=async()=>({});const bad=await mount('child',api)
      try{await bad.state.show();bad.state.selected='cloud:1';await bad.state.add();assert.equal(bad.state.pending.payload.version,1);assert.equal(bad.state.success,null);assert.match(bad.state.error,/无法确认/)}finally{bad.unmount()}
    })
    await t.test('评论与收集待确认状态统一保护离开、刷新和账号变化',async()=>{
      const m=await mount('parent',{get:async()=>detail(1)})
      try{const s=m.state;s.commentText='未发评论';window.confirm=()=>false;assert.equal(m.guards.leave(),false);assert.equal(m.guards.auth(),false);let prevented=false;m.listeners.get('beforeunload')({preventDefault(){prevented=true}});assert(prevented)
        s.commentText='';s.collectDirty=true;assert.equal(m.guards.leave(),false);s.collectDirty=false;assert.equal(m.guards.leave(),true)
      }finally{m.unmount()}
      assert.equal(m.listeners.size,0);assert.equal(m.guards.auth,undefined)
    })
    await t.test('旧评论和收藏响应不修改新景点，新输入不会被清掉',async()=>{
      const fav=deferred(),comment=deferred(),m=await mount('parent',{get:async path=>detail(Number(path.split('/').at(-1))),put:()=>fav.promise,post:()=>comment.promise})
      try{const s=m.state;s.commentText='旧评论';const a=s.toggleFav(),b=s.submitComment();await settle();m.route.params.id='2';await settle();s.commentText='新景点草稿'
        fav.resolve({favorited:true,favorite_count:9});comment.resolve({id:1,content:'旧评论'});await Promise.all([a,b]);assert.equal(s.detail.spot.id,2);assert.equal(s.detail.favorited,false);assert.equal(s.detail.comments.length,0);assert.equal(s.commentText,'新景点草稿')
      }finally{m.unmount()}
    })
    await t.test('未知收藏沿用目标，未知评论沿用标识，核对不会自动再发',async()=>{
      let writes=0;const targets=[],keys=[]
      const fail=()=>{writes++;const e=Error('结果未知');e.outcomeUnknown=true;throw e}
      const api={get:async()=>detail(1),put:async(path,body)=>{targets.push(body);fail()},post:async(path,body)=>{keys.push(body.request_id);fail()}},m=await mount('parent',api)
      try{const s=m.state;await s.toggleFav();await s.toggleFav();assert.equal(writes,2);assert.equal(targets[0],targets[1]);s.commentText='保留评论';await s.submitComment();await s.submitComment();assert.equal(writes,4);assert.equal(keys[0],keys[1]);assert.equal(s.commentUncertain,true)
        await s.checkInteractions();assert.equal(s.favUncertain,false);assert.equal(s.commentUncertain,true);assert.equal(s.commentText,'保留评论');window.confirm=()=>false;s.resumeComment();assert.equal(s.commentUncertain,true)
      }finally{m.unmount()}
    })
  }finally{globalThis.window=previousWindow;globalThis.sessionStorage=previousStorage;delete globalThis.__spotTest}
})
