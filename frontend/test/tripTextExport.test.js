import test from 'node:test'
import assert from 'node:assert/strict'
import { formatTripText } from '../src/utils/tripTextExport.js'

test('导出全部日期及已保存安排，不包含预订和支出', () => {
  const text = formatTripText({title:'春日旅行', destination:'杭州', start_date:'2028-02-28',
    reservations:[{order_number:'secret-order'}], expenses:[{note:'secret-expense'}], days:[
      {day_number:1, theme:'湖边', items:[{name:'西湖', kind:'景点', start_time:'09:00', duration_min:0, address:'湖边', note:'提前预约', source:'手动'}]},
      {day_number:2, theme:'', items:[]}, {day_number:3, theme:'', items:[]},
    ]})
  assert.match(text, /第 1 天 · 2028-02-28 · 湖边/)
  assert.match(text, /第 2 天 · 2028-02-29\n  暂无安排/)
  assert.match(text, /第 3 天 · 2028-03-01/)
  assert.match(text, /停留：0 分钟/)
  assert.match(text, /备注：提前预约/)
  assert.doesNotMatch(text, /secret-order|secret-expense/)
})

test('无出发日期和时间时保留待定说明', () => {
  const text = formatTripText({title:'旅行', destination:'', start_date:null, days:[
    {day_number:1, theme:'', items:[{name:'自由活动', kind:'', start_time:'', duration_min:null}]},
  ]})
  assert.match(text, /目的地：待定\n出发日期：待定/)
  assert.match(text, /1\. 时间待定 · 自由活动/)
  assert.doesNotMatch(text, /停留：/)
})
