import test from 'node:test'
import assert from 'node:assert/strict'
import {newGuideCreation,readGuideCreation,saveGuideCreation,clearGuideCreation} from '../src/store/guideCreationAttempt.js'
const payload={title:'攻略',content:'正文',city:'杭州',is_draft:true,images:['a'],spot_ids:[1]}
async function storageTest(run){
  const previous=globalThis.sessionStorage,data=new Map()
  globalThis.sessionStorage={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)}
  try{await run(data)}finally{globalThis.sessionStorage=previous}
}
test('攻略创建快照及数组不可变，重载同标识且按账号隔离',async()=>storageTest(()=>{
  const source={...payload,images:['a']},a=newGuideCreation(source);saveGuideCreation(1,a);source.images.push('later')
  const restored=readGuideCreation(1);assert.equal(restored.request_id,a.request_id);assert.deepEqual(restored.payload,payload)
  assert(Object.isFrozen(restored.payload.images));assert.equal(readGuideCreation(2),null)
  clearGuideCreation(1,'other');assert.equal(readGuideCreation(1).request_id,a.request_id)
  clearGuideCreation(1,a.request_id);assert.equal(readGuideCreation(1),null)
}))
test('攻略创建损坏或错账号不发送，显式放弃可清除损坏数据',async()=>storageTest(data=>{
  const a=newGuideCreation(payload);saveGuideCreation(1,a)
  data.set('travel.guide-creation.v1.2',data.get('travel.guide-creation.v1.1'));assert.throws(()=>readGuideCreation(2),/核对云端/)
  data.set('travel.guide-creation.v1.1','broken');assert.throws(()=>readGuideCreation(1),/核对云端/)
  clearGuideCreation(1);assert.equal(readGuideCreation(1),null)
  assert.throws(()=>newGuideCreation({...payload,content:'x'.repeat(20001)}),/无法恢复/)
}))
test('攻略创建存储失败显式报错，不伪造保留成功',async()=>storageTest(()=>{
  sessionStorage.setItem=()=>{throw Error('quota')}
  assert.throws(()=>saveGuideCreation(1,newGuideCreation(payload)),/无法保留/);assert.equal(readGuideCreation(1),null)
}))
