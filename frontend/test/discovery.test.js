import test from 'node:test'
import assert from 'node:assert/strict'
import {createReadResource} from '../src/store/readResource.js'
import {discoveryQuery,discoveryRoute,validPage,validRegions,validFootprints} from '../src/utils/discoveryData.js'
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const resource=read=>createReadResource({read,initial:{items:[]},validate:r=>Array.isArray(r?.items)})
test('发现页读取顺序、原查询与身份隔离',async t=>{
  await t.test('失败保留已读内容，重试不混入当前输入',async()=>{
    const queries=[];let fail=false;const r=resource(async q=>{queries.push(q);if(fail)throw Error('503');return {items:['旧结果']}})
    const original={keyword:'已提交'};await r.load(original);fail=true;await r.load({keyword:'失败查询'});original.keyword='新输入';await r.retry()
    assert.deepEqual(r.state.data.items,['旧结果']);assert.equal(r.state.applied.keyword,'已提交');assert.equal(queries.at(-1).keyword,'失败查询');assert(Object.isFrozen(queries.at(-1)));r.dispose()
  })
  await t.test('旧错误不影响更新后的选择',async()=>{
    const old=deferred();let calls=0;const r=resource(()=>++calls===1?old.promise:Promise.resolve({items:['新结果']}))
    const a=r.load();await r.load();old.reject(Error('旧错误'));await a;assert.equal(r.state.error,'');assert.deepEqual(r.state.data.items,['新结果']);r.dispose()
  })
  await t.test('账号切换后再切回，旧回调仍不能回填；销毁不再读取',async()=>{
    let uid=1,reads=0;const old=deferred(),r=createReadResource({read:()=>{reads++;return old.promise},validate:()=>true,initial:{items:[]},owner:()=>uid})
    const a=r.load();uid=2;r.reset();uid=1;r.reset();old.resolve({items:['旧账号数据']});await a;assert.deepEqual(r.state.data.items,[]);r.dispose();await r.load();assert.equal(reads,1)
  })
  await t.test('坏200不作为成功空结果，不清掉之前数据',async()=>{
    let bad=false;const r=resource(async()=>bad?{items:null}:{items:['可见']});await r.load();bad=true;await r.load();assert.deepEqual(r.state.data.items,['可见']);assert.match(r.state.error,/不完整/);r.dispose()
  })
  await t.test('URL筛选与页数往返，忽略重复和损坏参数',()=>{
    const q=discoveryQuery({page:'3',keyword:' 湖边 ',city:'杭州',sort:'hot'},'guides');assert.deepEqual(discoveryQuery(discoveryRoute(q,'guides'),'guides'),q)
    assert.equal(discoveryQuery({page:['2','3']}).page,1);assert.equal(discoveryQuery({page:'-3'}).page,1);assert.equal(discoveryQuery({is_free:'unknown'}).is_free,'all')
  })
  await t.test('错误页号、重复记录和坏地图坐标不显示',()=>{
    const spot={id:1,name:'湖边',city:'杭州',tags:'风景',is_free:true},p={items:[spot],total:1,page:1,page_size:12}
    assert(validPage(p,{page:1},'spots'));assert(!validPage({...p,page:2},{page:1},'spots'));assert(!validPage({...p,items:[spot,spot],total:2},{page:1},'spots'))
    const f={id:1,trip_id:1,trip_title:'旅行',place_name:'湖边',city:'杭州',location_status:'associated',lng:181,lat:30,photo_ids:[]};assert(!validFootprints({items:[f]}));assert(validFootprints({items:[{...f,lng:120}]}))
    assert(validRegions({cities:[{city:'杭州',count:2,districts:[]}],total:3}));assert(!validRegions({cities:{}}))
  })
})
