export const budgetCategories = [
  ['local_transport', '市内交通'],
  ['intercity_transport', '城际往返'],
  ['lodging', '住宿'],
  ['food', '餐饮'],
  ['admission', '门票与活动'],
  ['other', '其他'],
]

export function parseCny(input) {
  if (input === '') return null
  if (!/^\d+(?:\.\d{1,2})?$/.test(input)) throw new Error('请输入非负金额，最多保留两位小数。')
  const [whole, fraction = ''] = input.split('.')
  const amount = Number(whole) * 100 + Number(fraction.padEnd(2, '0'))
  if (!Number.isSafeInteger(amount) || amount > 100000000000) throw new Error('金额超出可记录范围。')
  return amount
}

export const formatCny = minor => minor == null ? '待确认' : `¥${(minor / 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`

export function summarizeBudgetReview(trip) {
  const { reference, checklist, checklist_scope: scope } = trip.budget_review || {}
  const limit = checklist ? checklist.limit_minor : (reference?.limit_minor ?? null)
  const entries = checklist?.entries || {}
  let quoted = 0, remaining = 0, subtotal = 0
  for (const [key] of budgetCategories) {
    const row = entries[key]
    if (row?.status === 'quoted' && Number.isSafeInteger(row.amount_minor) && row.amount_minor >= 0) {
      quoted++
      subtotal += row.amount_minor
    } else if (row?.status !== 'excluded') remaining++
  }
  const stale = Boolean(scope && (scope.start_date !== trip.start_date || scope.destination !== trip.destination || scope.days !== trip.days.length))
  let state = 'pending'
  if (stale) state = 'stale'
  else if (quoted && limit !== null && subtotal > limit) state = 'over'
  else if (!quoted && !remaining) state = 'no_quotes'
  else if (!remaining && limit === null) state = 'no_limit'
  else if (!remaining && quoted) state = 'within'
  return { limit, entries, remaining, quoted, subtotal, state }
}
