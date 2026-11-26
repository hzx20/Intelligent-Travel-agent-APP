import test from 'node:test'
import assert from 'node:assert/strict'
import {newPlanCreation,cancelPlanCreation,readPlanCreation,savePlanCreation,clearPlanCreation} from '../src/store/planCreationAttempt.js'
const payload=()=>({history:[],user_input:'生成需求',prev_collected:{days:2},session_id:'session-test',messages:[{role:'user',text:'生成需求'}]})
async function storage(run){const previous=globalThis.sessionStorage,data=new Map();globalThis.sessionStorage={getItem:k=>data.get(k)||null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};try{await run(data)}finally{globalThis.sessionStorage=previous}}
test('规划快照深冻结，取消保留同UUID和游客凭证，账号隔离',async()=>storage(()=>{
  const original=payload(),guest=newPlanCreation(0,original);original.prev_collected.days=3
  assert.equal(guest.payload.prev_collected.days,2);assert(Object.isFrozen(guest.payload.messages[0]));assert(guest.guest_key)
  const cancelled=cancelPlanCreation(0,guest);assert.equal(cancelled.request_id,guest.request_id);assert.equal(cancelled.guest_key,guest.guest_key);assert.equal(cancelled.mode,'cancel')
  savePlanCreation(0,cancelled);assert.equal(readPlanCreation(0).mode,'cancel');assert.equal(readPlanCreation(1),null)
  const account=newPlanCreation(1,payload());assert.equal(account.guest_key,null);savePlanCreation(1,account)
  clearPlanCreation(1,'wrong');assert(readPlanCreation(1));clearPlanCreation(1,account.request_id);assert.equal(readPlanCreation(1),null)
}))
test('损坏或错账号的规划快照不恢复，明确放弃才能清除',async()=>storage(data=>{
  savePlanCreation(1,newPlanCreation(1,payload()));data.set('travel.plan-creation.v1.2',data.get('travel.plan-creation.v1.1'))
  assert.throws(()=>readPlanCreation(2),/无法恢复/);data.set('travel.plan-creation.v1.1','broken');assert.throws(()=>readPlanCreation(1),/无法恢复/)
  clearPlanCreation(1);assert.equal(readPlanCreation(1),null)
}))
test('无法暂存规划提交时显式失败，超大和非法内容在POST前拒绝',async()=>storage(()=>{
  sessionStorage.setItem=()=>{throw Error('quota')};assert.throws(()=>savePlanCreation(1,newPlanCreation(1,payload())),/无法保留/)
  assert.throws(()=>newPlanCreation(1,{...payload(),user_input:''}),/无法恢复/)
  assert.throws(()=>newPlanCreation(1,{...payload(),prev_collected:{large:'x'.repeat(260*1024)}}),/无法恢复/)
}))
