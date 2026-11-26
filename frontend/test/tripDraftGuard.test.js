import test from 'node:test'
import assert from 'node:assert/strict'
import {allowTripDeparture, guardTripUnload} from '../src/utils/tripDraftGuard.js'

test('旅行草稿及正在保存的操作阻止意外离开', () => {
  let called = 0
  const reject = () => { called++; return false }
  assert.equal(allowTripDeparture(false, false, reject), true)
  assert.equal(called, 0)
  assert.equal(allowTripDeparture(true, false, reject), false)
  assert.equal(allowTripDeparture(false, true, reject), false)
  assert.equal(allowTripDeparture(true, false, () => true), true)
  assert.equal(called, 2)
  let prevented = false
  const event = {returnValue: 'original', preventDefault() { prevented = true }}
  guardTripUnload(event, false, false)
  assert.equal(prevented, false)
  assert.equal(event.returnValue, 'original')
  guardTripUnload(event, true, false)
  assert.equal(prevented, true)
  assert.equal(event.returnValue, '')
})

import {allowAuthChange, registerAuthChangeGuard} from '../src/store/authChangeGuard.js'

test('账号变更遵循草稿保护并在页面卸载后移除保护', () => {
  const unregister = registerAuthChangeGuard(() => false)
  try { assert.equal(allowAuthChange(), false) }
  finally { unregister() }
  assert.equal(allowAuthChange(), true)
})
