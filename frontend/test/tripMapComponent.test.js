import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import {parse,compileScript} from '@vue/compiler-sfc'
import {createRenderer,createVNode,nextTick} from 'vue'
const tick=async()=>{await nextTick();await new Promise(r=>setImmediate(r));await nextTick()}
async function mountMap(initial,load,{autoComplete=true}={}){
  const maps=[],markers=[]
  class Map {constructor(el,options){this.options=options;this.fits=0;this.center=null;this.destroyed=false;this.events={};maps.push(this)}on(name,handler){this.events[name]=handler;if(name==='complete'&&autoComplete)queueMicrotask(()=>this.events.complete?.())}off(name,handler){if(this.events[name]===handler)delete this.events[name]}fire(name){this.events[name]?.()}add(){}remove(){}setFitView(){this.fits++}setCenter(p){this.center=p}destroy(){this.destroyed=true}}
  class Marker {constructor(options){this.options=options;markers.push(this)}on(name,handler){this[name]=handler}}
  const sdk={Map,Marker,Polyline:class{}},ctx={load:load||(()=>Promise.resolve(sdk))};globalThis.__mapTest=ctx
  const url=new URL('../src/components/TripMap.vue',import.meta.url),{descriptor}=parse(await readFile(url,'utf8'))
  const code=compileScript(descriptor,{id:'trip-map-test'}).content
    .replace(/from ['"]vue['"]/g,`from ${JSON.stringify(import.meta.resolve('vue'))}`)
    .replace(/import \{ loadAmap \} from [^\n]+/,'const loadAmap=()=>globalThis.__mapTest.load()')
    .replace(/from '([.][.]\/utils\/[a-zA-Z]+)'/g,(_,name)=>`from ${JSON.stringify(new URL('../src/'+name.slice(3)+'.js',import.meta.url).href)}`)
  const {default:c}=await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}#${Math.random()}`);c.render=()=>null
  const r=createRenderer({createComment:()=>({}),insert(){},remove(){},parentNode:()=>null,nextSibling:()=>null,createText:()=>({}),setText(){},createElement:()=>({}),setElementText(){},patchProp(){}}),root={},props={...initial}
  let node=createVNode(c,{...props});r.render(node,root);await tick()
  return {sdk,maps,markers,state:node.component.setupState,async update(p){Object.assign(props,p);node=createVNode(c,{...props});r.render(node,root);await tick()},unmount(){r.render(null,root)}}
}
test('地图工作区可见性与视角生命周期',async t=>{
  const items=[{id:1,name:'甲',lng:120,lat:30},{id:2,name:'乙',lng:121,lat:31}]
  await t.test('隐藏时不构造，首次显示构造一次，重复显示保留手动视角',async()=>{
    const m=await mountMap({items,active:false,selectedId:1})
    try{assert.equal(m.maps.length,0);await m.update({active:true});assert.equal(m.maps.length,1);const map=m.maps[0];assert.equal(map.options.resizeEnable,true);map.center=[122,32];const fits=map.fits;await m.update({active:false});await m.update({active:true});assert.equal(m.maps.length,1);assert.deepEqual(map.center,[122,32]);assert.equal(map.fits,fits)}finally{m.unmount()}
    assert.equal(m.maps[0].destroyed,true)
  })
  await t.test('隐藏时选择另一地点，再显示才定位，几何更新会重新取景',async()=>{
    const m=await mountMap({items,active:true,selectedId:1})
    try{const map=m.maps[0];await m.update({active:false,selectedId:2});assert.deepEqual(map.center,[120,30]);await m.update({active:true});assert.deepEqual(map.center,[121,31]);const fits=map.fits;await m.update({items:[...items,{id:3,name:'丙',lng:122,lat:32}]});assert.equal(map.fits,fits+1)}finally{m.unmount()}
  })
  await t.test('加载期间销毁后不创建地图，错误可在重新显示时恢复',async()=>{
    let finish;const pending=new Promise(r=>finish=r),late=await mountMap({active:true},()=>pending);late.unmount();finish(late.sdk);await tick();assert.equal(late.maps.length,0)
    let count=0;const m=await mountMap({active:false},()=>++count===1?Promise.reject(Error('故障')):Promise.resolve(m.sdk))
    try{await m.update({active:true});assert.match(m.state.error,/地图暂不可用/);await m.update({active:false});await m.update({active:true});assert.equal(m.state.error,'');assert.equal(m.maps.length,1)}finally{m.unmount()}
  })
  await t.test('加载状态可观察，失败后按钮重试不重复创建地图',async()=>{
    let reject,resolve,attempts=0
    const m=await mountMap({active:true},()=>{attempts++;return new Promise((ok,fail)=>{resolve=ok;reject=fail})})
    try{
      assert.equal(m.state.starting,true);assert.equal(m.state.ready,false)
      reject(Error('超时'));await tick();assert.equal(m.state.starting,false);assert.match(m.state.error,/地图暂不可用/)
      const retry=m.state.activate();await tick();assert.equal(m.state.starting,true);assert.equal(m.state.error,'')
      await m.state.activate();assert.equal(attempts,2)
      resolve(m.sdk);await retry;await tick();assert.equal(m.state.starting,false);assert.equal(m.state.ready,true);assert.equal(m.maps.length,1)
    }finally{m.unmount()}
  })
  await t.test('底图完成前不绘制或取景，完成后使用最新地点且只初始化一次',async()=>{
    const m=await mountMap({items,selectedId:1},null,{autoComplete:false})
    try{
      const map=m.maps[0];assert.equal(m.state.starting,true);assert.equal(map.fits,0);assert.equal(m.markers.length,0)
      await m.update({selectedId:2,items:[...items,{id:3,name:'丙',lng:122,lat:32}]})
      assert.equal(map.fits,0);assert.equal(map.center,null)
      map.fire('complete');await tick();assert.equal(m.state.ready,true);assert.equal(map.fits,1);assert.equal(m.markers.length,3);assert.deepEqual(map.center,[121,31])
      map.center=[123,33];map.fire('complete');await tick();assert.equal(map.fits,1);assert.deepEqual(map.center,[123,33])
    }finally{m.unmount()}
  })
  await t.test('底图在隐藏时完成，显示后才取景；卸载取消等待及迟到完成',async()=>{
    const m=await mountMap({items},null,{autoComplete:false})
    try{await m.update({active:false});m.maps[0].fire('complete');await tick();assert.equal(m.maps[0].fits,0);await m.update({active:true});assert.equal(m.maps[0].fits,1)}finally{m.unmount()}
    const late=await mountMap({items},null,{autoComplete:false}),map=late.maps[0],complete=map.events.complete
    late.unmount();complete();await tick();assert.equal(map.destroyed,true);assert.equal(map.fits,0);assert.equal(late.markers.length,0)
  })
})
