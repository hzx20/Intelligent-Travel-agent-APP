import test from 'node:test'
import assert from 'node:assert/strict'
import { loadTripHomeSources } from '../src/utils/tripHomeSources.js'

test('历史方案失败时仍保留账号旅行', async () => {
  const result = await loadTripHomeSources(async path => {
    if (path.includes('history')) throw new Error('历史暂不可用')
    return {items:[{id:1, title:'杭州'}]}
  })
  assert.equal(result.trips[0].title, '杭州')
  assert.equal(result.tripsError, '')
  assert.equal(result.historyError, '历史暂不可用')
})

test('旅行请求失败时仍显示历史，并明确旅行加载失败', async () => {
  const result = await loadTripHomeSources(async path => {
    if (path === '/api/trips') throw new Error('旅行暂不可用')
    return {items:[{id:9}]}
  })
  assert.equal(result.tripsError, '旅行暂不可用')
  assert.equal(result.history[0].id, 9)
  assert.equal(result.historyError, '')
})
