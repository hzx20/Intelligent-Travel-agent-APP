import test from 'node:test'
import assert from 'node:assert/strict'
import { shareError } from '../src/utils/shareErrors.js'

test('失效链接要求重新获取，暂时故障允许重试', () => {
  assert.equal(shareError({status:404}).retryable, false)
  assert.match(shareError({status:422}).message, /重新获取/)
  assert.equal(shareError({status:503}).retryable, true)
  assert.match(shareError({name:'AbortError'}, true).message, /可能已经加入/)
})
