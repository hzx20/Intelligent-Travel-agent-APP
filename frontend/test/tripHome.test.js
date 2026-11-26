import test from 'node:test'
import assert from 'node:assert/strict'
import {createTripHome} from '../src/store/tripHome.js'
import {homeTransferAttempt,saveHomeTransfer,readHomeTransfer} from '../src/store/homeTransferAttempt.js'
const rev='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const plan={id:3,version:1,revision:rev,title:'原历史'}
const trip=(id=7)=>({id,version:1,title:'账号旅行',source_plan_id:3,days:[{items:[]}]})
const snapshot={guest_id:'guest-test',guest_version:2,trip:{id:'guest-test',version:2,title:'本机原件',days:[{items:[]}],reservations:[]}}
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
const tick=()=>new Promise(r=>setImmediate(r))
function setup(api={},guest={},storageOverride){
  let uid=1;const data=new Map(),marked=[]
  const storage=storageOverride||{getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)}
  const home=createTripHome({owner:()=>uid,storage:()=>storage,
    api:{get:async path=>path.endsWith('capabilities')?{recoverable_conversion:true,idempotent_import:true,status_lookup:true,reservation_records:true}:{items:[]},...api},
    guestTrips:{list:async()=>({items:[]}),migrationHistory:async()=>[],prepareMigration:async()=>structuredClone(snapshot),markMigrated:async(...args)=>marked.push(args),...guest}})
  return {home,data,marked,storage,setOwner:id=>uid=id}
}
const receipt=(value=trip())=>({owner_id:1,plan_id:3,plan_revision:rev,state:'converted',trip:value})

test('首页独立列表、原次转换及账号隔离',async t=>{
  await t.test('列表重试503保留已见内容，另一来源不受影响',async()=>{
    let fail=false;const {home:h}=setup({get:async p=>{if(p==='/api/trips'&&fail)throw Error('暂不可用');return {items:p==='/api/trips'?[trip()]:[plan]}}})
    await h.reset();fail=true;await h.load('trips');assert.equal(h.trips.items[0].id,7);assert.equal(h.trips.error,'暂不可用');assert.equal(h.history.items[0].id,3)
    h.dispose()
  })
  await t.test('坏200不是空列表，迟到旧读取不能覆盖新读取',async()=>{
    const old=deferred();let reads=0;const {home:h}=setup({get:p=>p==='/api/trips'?(++reads===1?old.promise:Promise.resolve({items:[trip(8)]})):Promise.resolve({items:[]})})
    const one=h.load('trips');await h.load('trips');old.resolve({items:[trip(7)]});await one;assert.equal(h.trips.items[0].id,8)
    const bad=setup({get:async()=>({items:null})}).home;await bad.load('trips');assert.match(bad.trips.error,/不完整/);assert.equal(bad.trips.loaded,false);h.dispose();bad.dispose()
  })
  await t.test('原次正文冻结，丢响应刷新后只继续同一版本',async()=>{
    const calls=[],x=setup({post:async(p,b)=>{calls.push(b);throw Error('响应丢失')}});await x.home.begin('plan',plan)
    assert.equal(x.home.hasPending.value,true);plan.version=9;assert.equal(x.home.operation.value.body.version,1)
    const y=setup({post:async(p,b)=>{calls.push(b);return receipt()}},{},x.storage);await y.home.reset();await y.home.retry();assert.deepEqual(calls[0],calls[1]);assert.equal(y.home.operation.value.resolved,true);assert.equal(y.data.size,0);plan.version=1;x.home.dispose();y.home.dispose()
  })
  await t.test('状态核对未创建不改变提交版本，不自动POST',async()=>{
    let posts=0;const {home:h}=setup({post:async()=>{posts++;throw Error('未知')},get:async p=>p.endsWith('capabilities')?{recoverable_conversion:true}:{owner_id:1,plan_id:3,plan_revision:rev,state:'not_created'}})
    await h.begin('plan',plan);await h.check();assert.equal(posts,1);assert.equal(h.operation.value.body.version,1);assert.match(h.operation.value.error,/尚未找到/);h.dispose()
  })
  await t.test('错误账号或原件标识回执保留尝试，不误确认',async()=>{
    const {home:h}=setup({post:async()=>({...receipt(),plan_revision:'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'})});await h.begin('plan',plan)
    assert.equal(h.operation.value.trip,null);assert.equal(h.hasPending.value,true);assert.match(h.operation.value.error,/回执/);h.dispose()
  })
  await t.test('换号后的迟到成功不影响新账号；本标签原账号仍保留尝试',async()=>{
    const late=deferred(),x=setup({post:()=>late.promise}),running=x.home.begin('plan',plan);await tick();x.setOwner(2);await x.home.reset();late.resolve(receipt());await running
    assert.equal(x.home.operation.value,null);assert(x.data.has('travel.home-transfer.v1.1'));assert(!x.data.has('travel.home-transfer.v1.2'));x.home.dispose()
  })
  await t.test('本机后续修改不改变原复制快照；标记失败不把已确认复制说成未保存',async()=>{
    const calls=[],x=setup({post:async(p,b)=>{calls.push(b);if(calls.length===1)throw Error('未知');return {owner_id:1,guest_id:'guest-test',guest_version:2,state:'copied',trip:trip()}}},{markMigrated:async()=>{throw Error('本机存储失败')}})
    await x.home.begin('local',{id:'guest-test',title:'原本机'});snapshot.trip.title='后续修改';await x.home.retry();snapshot.trip.title='本机原件'
    assert.equal(calls[1].trip.title,'本机原件');assert.equal(x.home.operation.value.resolved,true);assert.match(x.home.notice.value,/标记未保存/);x.home.dispose()
  })
  await t.test('本标签无法存储或旧能力不发送转换',async()=>{
    let writes=0;const x=setup({post:async()=>{writes++}},{},{getItem:()=>null,setItem(){throw Error('quota')},removeItem(){}})
    await x.home.begin('plan',plan);assert.equal(writes,0);assert.equal(x.home.operation.value,null)
    const y=setup({get:async()=>({}),post:async()=>{writes++}});await y.home.begin('plan',plan);assert.equal(writes,0);assert.match(y.home.operation.value.error,/不支持/);x.home.dispose();y.home.dispose()
  })
  await t.test('快照准备期间重复点击不会重复准备和提交',async()=>{
    let prepares=0,writes=0;const p=deferred(),x=setup({post:async()=>{writes++;throw Error('未知')}},{prepareMigration:()=>{prepares++;return p.promise}})
    const a=x.home.begin('local',{id:'guest-test',title:'本机'});await x.home.begin('local',{id:'guest-test',title:'本机'});p.resolve(snapshot);await a
    assert.equal(prepares,1);assert.equal(writes,1);x.home.dispose()
  })
  await t.test('恢复校验拒绝串号和损坏信息，未提交输入不混入快照',()=>{
    const x=setup(),a=homeTransferAttempt({kind:'local',source_id:'guest-test',label:'本机',body:snapshot});saveHomeTransfer(x.storage,1,a)
    assert.equal(readHomeTransfer(x.storage,2),null);assert(Object.isFrozen(a.body.trip.days));x.storage.setItem('travel.home-transfer.v1.2',x.storage.getItem('travel.home-transfer.v1.1'));assert.throws(()=>readHomeTransfer(x.storage,2),/账号不符/);x.home.dispose()
  })
})
