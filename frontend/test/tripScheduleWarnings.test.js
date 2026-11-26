import test from 'node:test'
import assert from 'node:assert/strict'
import { scheduleWarning } from '../src/utils/tripScheduleWarnings.js'

test('跨休息的交通核对保留连续空档，备选或未知休息时长不做耗时判断', () => {
  const first = { start_time: '09:00', duration_min: 30 }
  const next = { start_time: '11:00' }
  const pause = { start_time: '10:00', duration_min: 30 }
  const route = { status: 'ok', duration_s: 31 * 60 }
  assert.match(scheduleWarning(first, next, route, [pause]), /计入休息后.*还差 1 分钟/)
  assert.equal(scheduleWarning(first, next, { ...route, duration_s: 30 * 60 }, [pause]), '')
  assert.equal(scheduleWarning(first, next, route, [{ ...pause, duration_min: null }]), '')
  assert.equal(scheduleWarning(first, next, { ...route, preview: true }, [pause]), '')
})

test('提示安排重叠、倒序及可能跨日，不改写行程', () => {
  const first = {start_time:'09:00', duration_min:90}, next = {start_time:'10:00'}
  assert.match(scheduleWarning(first, next), /尚未结束.*30 分钟/)
  assert.match(scheduleWarning(first, {start_time:'08:00'}), /顺序或是否跨日/)
  assert.deepEqual(first, {start_time:'09:00', duration_min:90})
})

test('只有有效交通结果才计算衔接，时长不足时提示', () => {
  const first = {start_time:'09:00', duration_min:30}, next = {start_time:'10:00'}
  assert.match(scheduleWarning(first, next, {status:'ok', duration_s:1860}), /差 1 分钟/)
  assert.equal(scheduleWarning(first, next, {status:'ok', duration_s:1800}), '')
  assert.equal(scheduleWarning(first, next, {status:'unavailable', duration_s:9999}), '')
  assert.equal(scheduleWarning({...first, duration_min:null}, next, {status:'ok', duration_s:9999}), '')
  assert.equal(scheduleWarning(first, {start_time:''}, {status:'ok', duration_s:9999}), '')
})
