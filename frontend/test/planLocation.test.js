import test from 'node:test'
import assert from 'node:assert/strict'
import { hasPlanCoordinates, isUnverifiedPlanPlace, planDayEntries } from '../src/utils/planLocation.js'

test('待核实地点即使带旧坐标也不能绘制或生成导航链接',()=>{
  const place={lng:120.1,lat:30.2,verification_status:'unverified'}
  assert.equal(isUnverifiedPlanPlace(place),true)
  assert.equal(hasPlanCoordinates(place),false)
})
test('正常历史地点和新匹配地点坐标可用，零值不误判为缺失',()=>{
  assert.equal(hasPlanCoordinates({lng:120.1,lat:30.2}),true)
  assert.equal(hasPlanCoordinates({lng:0,lat:0,verification_status:'verified'}),true)
})
test('缺失、非数或越界坐标不能作为地点位置',()=>{
  for(const place of [null,{}, {lng:'120',lat:30},{lng:120,lat:null},{lng:Infinity,lat:30},{lng:181,lat:30},{lng:120,lat:91}])
    assert.equal(hasPlanCoordinates(place),false)
})

test('午餐未选餐厅也保留时段与未知金额，有餐厅的安排不重复', () => {
  const named = { name: '合成餐厅', type: '餐饮', time: '18:30', lng: 120.1, lat: 30.1 }
  const day = {
    spots: [named],
    items: [
      { activity: '午餐', type: '餐饮', poi: ' ', time: '12:00', duration_min: 45, cost_estimate: null, lng: 120.1, lat: 30.1 },
      { activity: '午休', type: '休息', time: '13:00', duration_min: 60 },
      { activity: '晚餐', type: '餐饮', poi: '合成餐厅', time: '18:30' },
    ],
  }
  const [lunch, rest, dinner] = planDayEntries(day)
  assert.deepEqual([lunch.name, rest.name, dinner.name], ['午餐', '午休', '合成餐厅'])
  assert.equal(lunch.timeBlock, '餐饮')
  assert.equal(lunch.duration_min, 45)
  assert.equal(lunch.cost_estimate, null)
  assert.equal(hasPlanCoordinates(lunch), false)
  assert.equal(hasPlanCoordinates(rest), false)
  assert.equal(dinner, named)
  assert.equal(day.items[0].lng, 120.1)
})

test('原规划保留抵达、自由活动和返程，地点未定时不生成地图标记', () => {
  const spot = { name: '西湖', time: '10:00', lng: 120.14, lat: 30.25 }
  const entries = planDayEntries({ spots: [spot], items: [
    { activity: '自由活动', type: '自由活动', time: '13:00', duration_min: 60 },
    { activity: '返程', type: '交通', time: '15:00', duration_min: 0, cost_estimate: null },
    { activity: '抵达杭州', type: '交通', time: '09:00', lng: 120.17, lat: 30.26 },
    { activity: '游览西湖', poi: '西湖', time: '10:00' },
  ] })
  assert.deepEqual(entries.map(entry => entry.name), ['抵达杭州', '西湖', '自由活动', '返程'])
  assert.deepEqual(entries.map(hasPlanCoordinates), [false, true, false, false])
  assert.equal(entries[3].duration_min, 0)
  assert.equal(entries[3].cost_estimate, null)
})
