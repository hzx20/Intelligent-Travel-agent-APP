import test from 'node:test'
import assert from 'node:assert/strict'
import {createPlanHistory} from '../src/store/planHistory.js'
const REV='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const detail=(id,version=1)=>({id,version,revision:REV,status:'completed',chat:[{role:'ai',text:'历史'+id}],itinerary:{days:[]},intent:{},map_state:{}})
const state=(id,version,status='completed')=>({id,version,revision:REV,status,can_restore:status==='deleted',restore_seconds_remaining:status==='deleted'?8:0,restore_until:status==='deleted'?123:null})
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return {promise,resolve,reject}}

test('历史读取、快照排队和未知操作保护',async t=>{
  await t.test('先读后返回不能覆盖较晚选择，坏编号不采纳',async()=>{
    const a=deferred(),b=deferred();let n=0
    const h=createPlanHistory({owner:()=>1,api:{get:()=>++n===1?a.promise:b.promise}})
    try{const old=h.read(1),latest=h.read(2);b.resolve(detail(2));assert.equal((await latest).id,2);a.resolve(detail(1));assert.equal(await old,null)
      h.reset();assert.equal(await h.read(3),null);assert.match(h.detailError.value,/无法确认/)
    }finally{h.reset()}
  })
  await t.test('两个相邻快照顺序保存，后一份只沿用已确认版本',async()=>{
    const first=deferred(),calls=[]
    const h=createPlanHistory({owner:()=>1,api:{patch:async(path,body)=>{calls.push(structuredClone(body));return calls.length===1?first.promise:state(1,body.version+1)}}})
    try{h.register(detail(1));h.queue(1,{map_state:{center:[1,2],zoom:10}});const saving=h.retry(1)
      h.queue(1,{map_state:{center:[3,4],zoom:11}});assert.equal(calls.length,1)
      first.resolve(state(1,2));await saving
      assert.deepEqual(calls.map(p=>p.version),[1,2]);assert.equal(h.records.get(1).version,3);assert.equal(h.hasPending.value,false)
    }finally{h.reset()}
  })
  await t.test('未知保存保留原版本，核对不提升版本，失败不覆盖排队草稿',async()=>{
    const calls=[];let fail=true
    const h=createPlanHistory({owner:()=>1,api:{patch:async(path,body)=>{calls.push(body);if(fail)throw Error('unknown');return state(1,body.version+1)},get:async()=>state(1,8)}})
    try{h.register(detail(1));h.queue(1,{messages:[{role:'ai',text:'第一次'}]});await h.retry(1);h.queue(1,{messages:[{role:'ai',text:'第二次'}]});await h.checkSnapshot(1)
      assert.equal(h.records.get(1).version,1);assert.equal(h.records.get(1).checked.version,8);assert.equal(h.records.get(1).pending.messages[0].text,'第一次')
      fail=false;await h.retry(1);assert.equal(calls[1].version,1);assert.equal(calls[2].version,2);assert.equal(calls[2].messages[0].text,'第二次')
    }finally{h.reset()}
  })
  await t.test('切换视图前的延时快照仍写原记录，换号后旧回执不回填',async()=>{
    const reply=deferred(),calls=[];let owner=1
    const h=createPlanHistory({owner:()=>owner,api:{patch:async(path,body)=>{calls.push(path);return reply.promise}}})
    try{h.register(detail(1));h.register(detail(2));h.queue(1,{map_state:{center:[1,2],zoom:9}});const writing=h.retry(1)
      owner=2;h.reset();h.register(detail(2));reply.resolve(state(1,2));await writing
      assert.match(calls[0],/plan\/1\/snapshot\/edit$/);assert.equal(h.records.has(1),false);assert.equal(h.records.get(2).version,1)
    }finally{h.reset()}
  })
  await t.test('删除丢响应保留原版本，核对确认才清操作，列表不重复复活',async()=>{
    const calls=[]
    const h=createPlanHistory({owner:()=>1,api:{del:async path=>{calls.push(path);throw Error('lost')},get:async()=>state(1,2,'deleted')}})
    try{h.register(detail(1));h.items.value=[detail(1)];assert.equal(await h.mutate(detail(1),'delete'),null);assert(h.operation.value)
      const original=h.operation.value.version;const ack=await h.checkMutation();assert.equal(ack.status,'deleted');assert.equal(h.operation.value,null);assert.equal(h.items.value.length,0);assert.equal(original,1);assert.equal(calls.length,1)
    }finally{h.reset()}
  })
  await t.test('更新后核对不让旧删除采用新版本重发，坏回执不清操作',async()=>{
    const calls=[]
    const h=createPlanHistory({owner:()=>1,api:{del:async path=>{calls.push(path);return {}},get:async()=>state(1,4)}})
    try{h.register(detail(1));await h.mutate(detail(1),'delete');await h.checkMutation();await h.retryMutation()
      assert.equal(h.operation.value.version,1);assert.equal(h.operation.value.checked.version,4);assert.equal(calls[0],calls[1]);assert(h.operation.value.error)
    }finally{h.reset()}
  })
  await t.test('放弃快照不发送新写入，重新载入才允许新基线',async()=>{
    let writes=0
    const h=createPlanHistory({owner:()=>1,api:{patch:async()=>{writes++;throw Error('lost')},get:async()=>detail(1,3)}})
    try{h.register(detail(1));h.queue(1,{messages:[]});await h.retry(1);h.discardSnapshot(1);h.queue(1,{messages:[{role:'ai',text:'不应发送'}]});assert.equal(writes,1);assert.equal(h.hasPending.value,false)
      const read=await h.read(1,{discard:true});assert.equal(read.version,3)
    }finally{h.reset()}
  })
  await t.test('列表版本不能充当未读取快照的基线，旧任务结果也不能借新版本覆盖',async()=>{
    const h=createPlanHistory({owner:()=>1,api:{patch:async()=>{throw Error('不应写入')}}})
    try{
      h.register({id:1,version:3,revision:REV,status:'completed'});h.queue(1,{messages:[]});assert.equal(h.hasPending.value,false);assert.match(h.detailError.value,/重新打开/)
      h.register(detail(1,3),{replace:true});h.queue(1,{messages:[]},{expected:{version:1,revision:REV}})
      assert.equal(h.hasPending.value,false);assert.match(h.records.get(1).error,/未自动覆盖/)
    }finally{h.reset()}
  })
})
