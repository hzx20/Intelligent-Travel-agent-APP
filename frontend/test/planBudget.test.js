import test from 'node:test'
import assert from 'node:assert/strict'
import { estimatedMoney, planBudgetView } from '../src/utils/planBudget.js'

test('未给出或非法费用保持待确认，明确零元和有效小数可显示',()=>{
  for(const value of [null,undefined,true,-1,'','NaN',Infinity,'未知'])assert.equal(estimatedMoney(value),'待确认')
  assert.equal(estimatedMoney(0),'¥0');assert.equal(estimatedMoney('50.5'),'¥50.5')
})
test('旧方案不假装明细已核对，缺分类不会显示成零元',()=>{
  const b=planBudgetView({budget_summary:{总计:300}})
  assert.equal(b.total,'¥300');assert.ok(b.rows.every(r=>r.amount==='待确认'));assert.ok(b.warnings.length)
})
test('明细核对状态与实时报价分开，不给未知项目清掉提示',()=>{
  const b=planBudgetView({budget_summary:{交通:100,住宿:600,餐饮:300,门票:150,其他:25,总计:1175},
    budget_validation:{summary_checked:true,item_costs_checked:false,warnings:['有1项安排未给出费用。']}})
  assert.equal(b.rows.length,5);assert.equal(b.total,'¥1,175');assert.deepEqual(b.warnings,['有1项安排未给出费用。'])
})
test('费用组成有未知时显示已列小计，补充费用可展开且未知不补零',()=>{
  const b=planBudgetView({budget_summary:{交通:120,住宿:0,餐饮:0,门票:0,其他:0,总计:120},
    budget_validation:{calculation_basis:'items_and_additions_v1',total_is_partial:true,item_costs_checked:false,summary_checked:true,unpriced_categories:['住宿'],warnings:['住宿待确认']},
    additional_costs:[{name:'2晚住宿',category:'住宿',amount:null},{name:'通勤',category:'交通',amount:120}]})
  assert.equal(b.totalLabel,'已列估算小计');assert.equal(b.total,'¥120')
  assert.equal(b.rows.find(r=>r.name==='住宿').amount,'待确认')
  assert.deepEqual(b.additions,[{name:'2晚住宿',category:'住宿',amount:'待确认'},{name:'通勤',category:'交通',amount:'¥120'}])
})

test('未列类别不展示为免费，明确零元费用仍保留',()=>{
  const b=planBudgetView({budget_summary:{交通:0,住宿:0,餐饮:100,门票:0,其他:0,总计:100},
    budget_validation:{calculation_basis:'items_and_additions_v1',summary_checked:true,item_costs_checked:true,unlisted_categories:['住宿','门票','其他']}})
  assert.equal(b.rows.find(r=>r.name==='住宿').amount,'未列费用')
  assert.equal(b.rows.find(r=>r.name==='交通').amount,'¥0')
})
