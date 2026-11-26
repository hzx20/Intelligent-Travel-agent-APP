import test from 'node:test'
import assert from 'node:assert/strict'
import { isLocationFreeRest, summarizeAdoptedRoutes, summarizeRoutes, walkingLimitState } from '../src/utils/tripRouteSummary.js'

test('只跨过没有地点的休息，待定餐厅和已有位置的休息照常保留', () => {
  assert.equal(isLocationFreeRest({ kind: '休息', lng: null, lat: null, amap_id: '' }), true)
  assert.equal(isLocationFreeRest({ kind: '餐饮', lng: null, lat: null }), false)
  assert.equal(isLocationFreeRest({ kind: '休息', lng: 0, lat: 0 }), false)
  assert.equal(isLocationFreeRest({ kind: '休息', amap_id: 'known' }), false)
})

test('只汇总已查且金额有效的距离和秒数，保留总段数', () => {
  const summary = summarizeRoutes(4, [
    { distance_m: 600, duration_s: 480 },
    { distance_m: 0, duration_s: 0 },
    { distance_m: -1, duration_s: 100 },
    { distance_m: 100, duration_s: NaN },
  ])
  assert.deepEqual(summary, { count: 2, total: 4, distance: 600, seconds: 480 })
})

test('部分路段已超步行上限即提醒，低于上限仍等待剩余段', () => {
  assert.equal(walkingLimitState(undefined, 3000), 'pending')
  assert.equal(walkingLimitState({ count: 1, total: 2, distance: 3500 }, 3000), 'exceeded')
  assert.equal(walkingLimitState({ count: 1, total: 2, distance: 1500 }, 3000), 'pending')
  assert.equal(walkingLimitState({ count: 2, total: 2, distance: 3000 }, 3000), 'within_segments')
  assert.equal(walkingLimitState({ count: 2, total: 2, distance: 6000 }, null), null)
})

test('混合交通以全部路段为覆盖范围，驾车不计入步行小计', () => {
  const walking = { mode: 'walking', distance_m: 5841, duration_s: 4673 }
  const driving = { mode: 'driving', distance_m: 11140, duration_s: 1412 }
  const summary = summarizeAdoptedRoutes(3, [walking, driving])
  assert.deepEqual(summary, { total: 3, count: 2, distance: 16981, seconds: 6085, walkingDistance: 5841, walkingSeconds: 4673, walkingCount: 1 })
  assert.equal(walkingLimitState({ ...summary, distance: summary.walkingDistance }, 6000), 'pending')
  assert.equal(walkingLimitState({ ...summary, distance: summary.walkingDistance }, 5000), 'exceeded')
  const complete = summarizeAdoptedRoutes(1, [driving])
  assert.equal(walkingLimitState({ ...complete, distance: complete.walkingDistance }, 5000), 'within_segments')
})
