import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import vm from 'node:vm'
const source=(await readFile(new URL('../src/utils/mapReady.js',import.meta.url),'utf8')).replace('export function','function')
function harness(){
  const timers=new Map(),events=new Map();let id=0
  const wait=vm.runInNewContext(source+'\nwaitForMapReady',{AbortController,setTimeout(fn,delay){timers.set(++id,{fn,delay});return id},clearTimeout(i){timers.delete(i)}})
  const map={on(name,fn){events.set(name,fn)},off(name,fn){if(events.get(name)===fn)events.delete(name)}}
  return {wait,map,timers,events,expire(){const [key,value]=[...timers][0];assert.equal(value.delay,15000);timers.delete(key);value.fn()}}
}
test('底图完成仅结算一次，清理事件和超时',async()=>{
  const h=harness(),pending=h.wait(h.map),late=h.events.get('complete')
  late();await pending;assert.equal(h.timers.size,0);assert.equal(h.events.size,0);late()
})
test('底图永久未完成时有界失败，迟到事件不再结算',async()=>{
  const h=harness(),pending=h.wait(h.map),late=h.events.get('complete'),failed=assert.rejects(pending,/底图加载超时/)
  h.expire();await failed;assert.equal(h.events.size,0);late();assert.equal(h.timers.size,0)
})
test('卸载中止等待，预先中止不注册事件',async()=>{
  const h=harness(),controller=new AbortController(),pending=h.wait(h.map,controller.signal),failed=assert.rejects(pending,/已取消/)
  controller.abort();await failed;assert.equal(h.events.size,0);assert.equal(h.timers.size,0)
  await assert.rejects(h.wait(h.map,controller.signal),/已取消/);assert.equal(h.events.size,0)
})
test('SDK注册或清理事件失败仍能结算并释放计时器',async()=>{
  const h=harness();h.map.on=()=>{throw Error('SDK failure')};await assert.rejects(h.wait(h.map),/初始化失败/);assert.equal(h.timers.size,0)
  const other=harness();other.map.off=()=>{throw Error('SDK cleanup failure')};const pending=other.wait(other.map);other.events.get('complete')();await pending;assert.equal(other.timers.size,0)
})
