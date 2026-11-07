/** 展示层小工具：耗时 / 距离 / 床型建议。
 *  规划页（PlanView）与规划会话 store（store/planSession.js）都要用，
 *  放一处避免两边各写一份、改了一边忘了另一边。 */

/** 分钟 → "1 小时 20 分" */
export function fmtMin(m) {
  const n = Number(m) || 0
  if (!n) return '0 分'
  return n >= 60 ? `${Math.floor(n / 60)} 小时 ${n % 60} 分` : `${n} 分`
}

/** 米 → "1.2 km" / "850 米"（0 和空值直接不显示） */
export function fmtDist(m) {
  const n = Number(m)
  return !n ? '' : n >= 1000 ? (n / 1000).toFixed(1) + ' km' : Math.round(n) + ' 米'
}

/** 按出行人数给"大床 vs 双床"建议；用户没提人数就不生成（产品口径） */
export function partyAdvice(p) {
  const s = String(p || '').trim()
  if (!s) return null
  if (/小孩|儿童|娃|婴儿/.test(s)) return `家庭房，或大床房＋加床（多数酒店可免费加婴儿床，预订前电话确认）`
  if (/3人|三人|四人|朋友|闺蜜|同事/.test(s)) return `双床房（每人一床最省心）`
  if (/情侣|夫妻|蜜月/.test(s)) return `大床房（氛围更好；习惯分床就选双床）`
  return `双床房为主；同行人数若超过 2 人建议双床＋加床`
}
