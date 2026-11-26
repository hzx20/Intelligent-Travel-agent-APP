import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createRenderer, createVNode, nextTick, reactive, ref } from 'vue'

const sourceUrl = new URL('../src/views/TripDetailView.vue', import.meta.url)
const trip = (id, version = 1) => ({id, version, title:`旅行 ${id}`, destination:'杭州', days:[{id:id*10,day_number:1,items:[]}],places:[]})
const settle = async () => { await new Promise(resolve => setImmediate(resolve)); await nextTick() }
function deferred() { let resolve, reject; const promise = new Promise((yes,no) => {resolve=yes;reject=no}); return {promise,resolve,reject} }

async function mountDetail(api) {
  const port = {api, route:reactive({params:{id:'1'},query:{}}),auth:{user:ref({id:1}),openLogin(){}},leave:null,update:null}
  globalThis.__tripDetailTest = port
  const {descriptor} = parse(await readFile(sourceUrl,'utf8'))
  const code = compileScript(descriptor,{id:'detail-regression'}).content
    .replace(/import \{ onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter \} from ['"]vue-router['"]/, 'const useRouter=()=>({replace:async({query})=>{globalThis.__tripDetailTest.route.query=query}}); const useRoute=()=>globalThis.__tripDetailTest.route; const onBeforeRouteLeave=fn=>globalThis.__tripDetailTest.leave=fn; const onBeforeRouteUpdate=fn=>globalThis.__tripDetailTest.update=fn')
    .replace(/import \{ api \} from ['"]\.\.\/api['"]/, 'const api=globalThis.__tripDetailTest.api')
    .replace(/import \{ useAuth \} from ['"]\.\.\/store\/auth['"]/, 'const useAuth=()=>globalThis.__tripDetailTest.auth')
    .replace(/import (\w+) from ['"][^'"]+\.vue['"]/g, 'const $1 = {}')
    .replace(/from ['"]vue['"]/g, `from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace(/from ['"](\.\.[^'"]+)['"]/g, (_,path)=>`from ${JSON.stringify(new URL(path+'.js',sourceUrl).href)}`)
  const {default:component} = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}#${Math.random()}`)
  component.render=()=>null
  const renderer=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createText:()=>({}),setText(){},createElement:()=>({}),setElementText(){},patchProp(){}})
  const root={}, vnode=createVNode(component)
  renderer.render(vnode,root); await settle()
  return {state:vnode.component.setupState,port,unmount:()=>renderer.render(null,root)}
}

test('行程详情草稿和异步上下文回归', async t => {
  const oldWindow=globalThis.window,oldDocument=globalThis.document
  globalThis.window={setInterval:()=>1,clearInterval(){},addEventListener(){},removeEventListener(){},confirm:()=>false}
  globalThis.document={hidden:false,addEventListener(){},removeEventListener(){},getElementById:()=>null}
  try {
    await t.test('有安排和分享草稿时拒绝覆盖，取消刷新仍保留输入',async()=>{
      let latest=trip(1)
      const mounted=await mountDetail({get:async()=>latest})
      try {
        const s=mounted.state
        s.newItem();s.form.note='需要保留的备注';s.childDirty.sharing=true
        latest=trip(1,2);await s.syncTrip()
        assert.equal(s.trip.version,1);assert.equal(s.remoteUpdate,true)
        assert.equal(s.form.note,'需要保留的备注')
        assert.equal(mounted.port.leave(),false)
        await s.refreshRemote()
        assert.equal(s.showForm,true);assert.equal(s.form.note,'需要保留的备注')
        assert.equal(s.childDirty.sharing,true)
        s.clearDrafts();await s.syncTrip()
        assert.equal(s.trip.version,2);assert.equal(s.remoteUpdate,false)
      } finally {mounted.unmount()}
    })
    await t.test('旧旅行保存失败不能污染新旅行的错误与忙碌状态',async()=>{
      const pending=deferred(),nextSave=deferred()
      const mounted=await mountDetail({get:async path=>trip(Number(path.split('/').at(-1))),patch:path=>path.endsWith('/1')?pending.promise:nextSave.promise})
      try {
        const saving=mounted.state.mutate('/api/trips/1',{title:'旧旅行修改'},'patch')
        mounted.port.route.params.id='2';await settle()
        assert.equal(mounted.state.trip.id,2)
        assert.equal(mounted.state.busy,false)
        const newSaving=mounted.state.mutate('/api/trips/2',{title:'新旅行修改'},'patch')
        pending.reject(new Error('旧请求失败'));await saving
        assert.equal(mounted.state.error,'')
        assert.equal(mounted.state.busy,true)
        nextSave.resolve(trip(2,2));await newSaving
        assert.equal(mounted.state.busy,false)
        assert.equal(mounted.state.trip.version,2)
      } finally {mounted.unmount()}
    })
    await t.test('子组件与主编辑保存乱序返回时只保留当前旅行最新版本',async()=>{
      const pending=deferred()
      const mounted=await mountDetail({get:async()=>trip(1),patch:()=>pending.promise})
      try {
        const s=mounted.state
        const saving=s.mutate('/api/trips/1',{title:'较早保存'},'patch')
        s.receiveTripUpdate({...trip(1,3),title:'后续预订保存'})
        pending.resolve({...trip(1,2),title:'较早响应'});await saving
        assert.equal(s.trip.version,3);assert.equal(s.trip.title,'后续预订保存')
        s.receiveTripUpdate(trip(1,1));s.receiveTripUpdate(trip(2,99))
        assert.equal(s.trip.version,3);assert.equal(s.trip.id,1)
        s.receiveTripUpdate({...trip(1,4),title:'账本最新保存'})
        assert.equal(s.trip.version,4)
      } finally {mounted.unmount()}
    })
    await t.test('中间保存响应不能清除已知更高版本的更新提示',async()=>{
      let latest=trip(1)
      const mounted=await mountDetail({get:async()=>latest})
      try {
        const s=mounted.state;s.childDirty.expenses=true
        latest=trip(1,5);await s.syncTrip()
        s.receiveTripUpdate(trip(1,4))
        assert.equal(s.trip.version,4);assert.equal(s.remoteUpdate,true)
        s.receiveTripUpdate(trip(1,5))
        assert.equal(s.remoteUpdate,false)
      } finally {mounted.unmount()}
    })
    await t.test('读取失败或错编号不清行程与已经确认可放弃的草稿',async()=>{
      let response=trip(1),fail=false
      const m=await mountDetail({get:async()=>{if(fail)throw Error('读取故障');return response}})
      try{
        const s=m.state;s.pendingName='保留地点';s.childDirty.ai=true;const reset=s.editorReset
        window.confirm=()=>true;fail=true;await s.refreshRemote()
        assert.equal(s.trip.id,1);assert.equal(s.pendingName,'保留地点');assert.equal(s.childDirty.ai,true);assert.equal(s.editorReset,reset)
        fail=false;response=trip(2);await s.refreshRemote();assert.equal(s.trip.id,1);assert.match(s.error,/无法确认/);assert.equal(s.pendingName,'保留地点')
        response=trip(1,2);await s.refreshRemote();assert.equal(s.trip.version,2);assert.equal(s.pendingName,'');assert.equal(s.childDirty.ai,false)
      }finally{window.confirm=()=>false;m.unmount()}
    })
    await t.test('低于已知版本的刷新不降级也不清草稿',async()=>{
      let value=trip(1);const m=await mountDetail({get:async()=>value})
      try{const s=m.state;s.childDirty.expenses=true;value=trip(1,5);await s.syncTrip();value=trip(1,3);window.confirm=()=>true;await s.refreshRemote();assert.equal(s.trip.version,1);assert.equal(s.remoteVersion,5);assert.equal(s.childDirty.expenses,true);assert.match(s.error,/较早/)}finally{window.confirm=()=>false;m.unmount()}
    })
    await t.test('工作区切换保留全部草稿，URL视图不触发重新加载',async()=>{
      let reads=0;const m=await mountDetail({get:async()=>{reads++;return trip(1)}})
      try{const s=m.state;s.pendingName='待收集';s.childDirty.ai=true;const reset=s.editorReset;await s.openWorkspace('assistant');await s.openWorkspace('records');assert.equal(s.activeSection,'records');assert.equal(s.pendingName,'待收集');assert.equal(s.childDirty.ai,true);assert.equal(s.editorReset,reset);assert.equal(reads,1);assert.equal(m.port.route.query.panel,'records')}finally{m.unmount()}
    })
    await t.test('未处理足迹表单阻止换天；清理后可按URL选择第二天',async()=>{
      const value={...trip(1),days:[{id:10,day_number:1,items:[]},{id:11,day_number:2,items:[]}]},m=await mountDetail({get:async()=>value})
      try{const s=m.state;s.childDirty.footprints=true;await s.changeDay(value.days[1]);assert.equal(s.dayId,10);assert.equal(s.activeSection,'records');assert.match(s.error,/足迹表单/);s.childDirty.footprints=false;await s.changeDay(value.days[1]);assert.equal(s.dayId,11);assert.equal(m.port.route.query.day,'2')}finally{m.unmount()}
    })
    await t.test('刷新等待时旧内容保留并阻止新的父写入',async()=>{
      const waiting=deferred();let reads=0,writes=0;const m=await mountDetail({get:()=>++reads===1?Promise.resolve(trip(1)):waiting.promise,post:async()=>{writes++;return trip(1,2)}})
      try{const s=m.state;const loading=s.refreshRemote();assert.equal(s.trip.id,1);assert.equal(s.loading,true);assert.equal(await s.mutate('/api/trips/1/days',{}),false);assert.equal(writes,0);waiting.resolve(trip(1,2));await loading;assert.equal(s.trip.version,2)}finally{m.unmount()}
    })
    await t.test('错误保存回执保留编辑窗口，不误作成功关闭',async()=>{
      const m=await mountDetail({get:async()=>trip(1),patch:async()=>trip(2,2)})
      try{const s=m.state;s.editTrip();s.tripForm.title='保留标题';await s.saveTrip();assert.equal(s.showTripForm,true);assert.equal(s.trip.id,1);assert.equal(s.tripForm.title,'保留标题');assert.match(s.error,/回执无法确认/)}finally{m.unmount()}
    })
  } finally {globalThis.window=oldWindow;globalThis.document=oldDocument;delete globalThis.__tripDetailTest}
})
