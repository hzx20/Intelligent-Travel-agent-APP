import test from 'node:test'
import assert from 'node:assert/strict'
import {newCreationAttempt,readCreationAttempt,saveCreationAttempt,clearCreationAttempt} from '../src/store/tripCreationAttempt.js'

const payload = {title:'成都旅行',destination:'成都',days:2,start_date:null}
async function storageTest(run) {
  const before = globalThis.sessionStorage, data = new Map()
  globalThis.sessionStorage = {getItem:key=>data.get(key)||null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)}
  try { await run(data) } finally { globalThis.sessionStorage = before }
}
test('创建尝试按账号保存，重读保持同一标识及不可变快照', async () => storageTest(() => {
  const source = {...payload}, attempt = newCreationAttempt(source)
  saveCreationAttempt(1,attempt);source.title='后来输入'
  const restored=readCreationAttempt(1)
  assert.equal(restored.request_id,attempt.request_id)
  assert.deepEqual(restored.payload,payload)
  assert.equal(Object.isFrozen(restored.payload),true)
  assert.equal(readCreationAttempt(2),null)
  clearCreationAttempt(1,'another-key')
  assert.equal(readCreationAttempt(1).request_id,attempt.request_id)
  clearCreationAttempt(1,attempt.request_id)
  assert.equal(readCreationAttempt(1),null)
}))
test('损坏或错账号的创建信息拒绝恢复', async () => storageTest(data => {
  const attempt=newCreationAttempt(payload)
  saveCreationAttempt(1,attempt)
  data.set('travel.trip-creation.v1.2',data.get('travel.trip-creation.v1.1'))
  assert.throws(()=>readCreationAttempt(2),/账号不一致/)
  data.set('travel.trip-creation.v1.1','broken')
  assert.throws(()=>readCreationAttempt(1),/先在“我的旅行”核对/)
  assert.throws(()=>newCreationAttempt({...payload,days:31}),/无法恢复/)
}))
test('存储失败显式报错，不假装已保留重试信息', async () => storageTest(() => {
  globalThis.sessionStorage.setItem=()=>{throw Error('quota')}
  assert.throws(()=>saveCreationAttempt(1,newCreationAttempt(payload)),/无法保留这次创建信息/)
  assert.equal(readCreationAttempt(1),null)
}))
