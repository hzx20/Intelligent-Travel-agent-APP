import test from 'node:test'
import assert from 'node:assert/strict'
import {checkStoredSession} from '../src/store/sessionRecovery.js'

test('恢复账号时区分凭证失效和暂时无法连接', async () => {
  const current = () => true
  assert.deepEqual(await checkStoredSession('', () => {throw Error('should not call')}, current), {status:'anonymous'})
  assert.deepEqual(await checkStoredSession('token', async () => ({id:1}), current), {status:'authenticated',user:{id:1}})
  assert.deepEqual(await checkStoredSession('token', async () => {throw Object.assign(new Error(), {status:401})}, current), {status:'expired'})
  for (const status of [undefined, 403, 500]) {
    assert.deepEqual(await checkStoredSession('token', async () => {throw Object.assign(new Error(), {status})}, current), {status:'unavailable'})
  }
})

test('迟到的恢复响应不会覆盖已经切换的登录状态', async () => {
  let valid = true, finish
  const pending = checkStoredSession('old', () => new Promise(resolve => {finish=resolve}), () => valid)
  valid = false; finish({id:1})
  assert.deepEqual(await pending, {status:'superseded'})
  assert.deepEqual(await checkStoredSession('old', async () => {throw Object.assign(new Error(), {status:401})}, () => false), {status:'superseded'})
})
