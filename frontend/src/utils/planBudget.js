const CATEGORIES = ['交通', '住宿', '餐饮', '门票', '其他']

export function estimatedMoney(value) {
  if (typeof value === 'boolean' || value == null || !['number', 'string'].includes(typeof value)) return '待确认'
  if (typeof value === 'string' && !/^[+]?\d+(?:\.\d*)?(?:e[+-]?\d+)?$/i.test(value.trim())) return '待确认'
  const amount = Number(value)
  return Number.isFinite(amount) && amount >= 0 ? `¥${amount.toLocaleString('zh-CN', {maximumFractionDigits: 2})}` : '待确认'
}

export function planBudgetView(itinerary) {
  const summary = itinerary?.budget_summary
  const usable = summary && typeof summary === 'object' && !Array.isArray(summary)
  const validation = itinerary?.budget_validation
  const uncertain = Array.isArray(validation?.unpriced_categories) ? validation.unpriced_categories : []
  const unlisted = validation?.calculation_basis === 'items_and_additions_v1' && Array.isArray(validation?.unlisted_categories) ? validation.unlisted_categories : []
  const rows = CATEGORIES.filter(name => name !== '其他' || (usable && Object.hasOwn(summary, name)))
    .map(name => {
      const amount = estimatedMoney(usable ? summary[name] : null)
      return {name, amount: unlisted.includes(name) ? '未列费用' : uncertain.includes(name) ? (amount === '¥0' || amount === '待确认' ? '待确认' : `已列 ${amount} · 另有待确认`) : amount}
    })
  const warnings = Array.isArray(validation?.warnings)
    ? validation.warnings.filter(x => typeof x === 'string').slice(0, 5).map(x => x.slice(0, 250)) : []
  if (validation?.summary_checked !== true || validation?.item_costs_checked !== true) {
    if (!warnings.length) warnings.push('费用明细尚待补充或核对，请确认后再决定。')
  }
  const partial = validation?.calculation_basis === 'items_and_additions_v1' && validation?.total_is_partial === true
  const additions = Array.isArray(itinerary?.additional_costs) ? itinerary.additional_costs
    .filter(row => row && typeof row.name === 'string' && CATEGORIES.includes(row.category))
    .slice(0, 100).map(row => ({name: row.name.slice(0, 160), category: row.category, amount: estimatedMoney(row.amount)})) : []
  return {rows, total: estimatedMoney(usable ? summary['总计'] : null), warnings,
    totalLabel: partial ? '已列估算小计' : '合计', additions}
}
