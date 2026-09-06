/** 分页页码折叠算法（列表页与首页共用同一视觉规则） */

/**
 * 生成折叠页码序列：始终含首页与尾页、当前页前后各 span 个；
 * 当前页贴边时自动向内多展 span 个，避免"1 2 … 20"过瘦；
 * 两个候选页之间只缺 1 个页码时直接补全（不出省略号），缺 ≥2 个才折叠。
 * 例：pageList(5, 20) => [1, '...', 4, 5, 6, '...', 20]
 */
export function pageList(cur, totalPages, span = 1) {
  const tp = Math.max(0, Math.floor(totalPages) || 0)
  const c = Math.min(Math.max(1, Math.floor(cur) || 1), Math.max(1, tp))
  if (tp === 0) return []
  if (tp <= 2 * span + 5) {
    return Array.from({ length: tp }, (_, i) => i + 1)
  }
  let lo = Math.max(1, c - span)
  let hi = Math.min(tp, c + span)
  if (lo === 1) hi = Math.min(tp - 1, c + span * 2)
  if (hi === tp) lo = Math.max(2, c - span * 2)

  const set = new Set([1, tp])
  for (let n = lo; n <= hi; n++) set.add(n)
  const nums = [...set].sort((a, b) => a - b)

  const out = []
  let prev = 0
  for (const n of nums) {
    const gap = n - prev
    if (gap === 2) out.push(prev + 1) // 只缺 1 页 → 补全
    else if (gap > 2) out.push('...')
    out.push(n)
    prev = n
  }
  return out
}
