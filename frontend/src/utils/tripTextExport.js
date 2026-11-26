/** Format the currently displayed saved itinerary, without bookings or expenses. */
export function formatTripText(trip) {
  const lines = [trip.title, `目的地：${trip.destination || '待定'}`, `出发日期：${trip.start_date || '待定'}`, '']
  for (const day of trip.days) {
    let date = ''
    if (trip.start_date) {
      const d = new Date(`${trip.start_date}T00:00:00Z`)
      if (!Number.isNaN(d.getTime())) {
        d.setUTCDate(d.getUTCDate() + day.day_number - 1)
        date = ` · ${d.toISOString().slice(0, 10)}`
      }
    }
    lines.push(`第 ${day.day_number} 天${date}${day.theme ? ` · ${day.theme}` : ''}`)
    if (!day.items.length) lines.push('  暂无安排')
    day.items.forEach((item, index) => {
      lines.push(`  ${index + 1}. ${item.start_time || '时间待定'} · ${item.name}${item.kind ? `（${item.kind}）` : ''}`)
      if (item.duration_min != null) lines.push(`     停留：${item.duration_min} 分钟`)
      if (item.address) lines.push(`     地址：${item.address}`)
      if (item.activity) lines.push(`     安排：${item.activity}`)
      if (item.note) lines.push(`     备注：${item.note}`)
      if (item.source) lines.push(`     来源：${item.source}`)
    })
    lines.push('')
  }
  return `${lines.join('\n')}\n`
}
