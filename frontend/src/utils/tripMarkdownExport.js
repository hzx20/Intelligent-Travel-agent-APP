/** 导出当前保存的行程为 Markdown（不包含预订记录和账本，口径与 formatTripText 一致）。

与 tripTextExport.js 的差异：Markdown 版本带标题层级（# / ##）、列表与加粗，
导入语雀/Notion/飞书文档后能直接保留结构；纯文本版本适合微信直接粘贴。
日期计算沿用 formatTripText 的 UTC 口径（出发日期 + day_number - 1）。
*/
export function formatTripMarkdown(trip) {
  const lines = [`# ${trip.title || '旅行行程'}`, '']
  lines.push(`- **目的地**：${trip.destination || '待定'}`)
  lines.push(`- **出发日期**：${trip.start_date || '待定'}`)
  lines.push('')

  for (const day of trip.days) {
    let date = ''
    if (trip.start_date) {
      const d = new Date(`${trip.start_date}T00:00:00Z`)
      if (!Number.isNaN(d.getTime())) {
        d.setUTCDate(d.getUTCDate() + day.day_number - 1)
        date = ` · ${d.toISOString().slice(0, 10)}`
      }
    }
    lines.push(`## 第 ${day.day_number} 天${date}${day.theme ? ` · ${day.theme}` : ''}`)
    lines.push('')
    if (!day.items.length) {
      lines.push('暂无安排')
      lines.push('')
      continue
    }
    day.items.forEach((item, index) => {
      const head = `${index + 1}. **${item.name || '未命名安排'}**${item.kind ? `（${item.kind}）` : ''} · ${item.start_time || '时间待定'}`
      lines.push(head)
      const details = []
      if (item.duration_min != null) details.push(`停留：${item.duration_min} 分钟`)
      if (item.address) details.push(`地址：${item.address}`)
      if (item.activity) details.push(`安排：${item.activity}`)
      if (item.note) details.push(`备注：${item.note}`)
      if (item.source) details.push(`来源：${item.source}`)
      // Markdown 列表的续行要缩进两个空格，否则会被解析成独立列表
      details.forEach(detail => lines.push(`   - ${detail}`))
    })
    lines.push('')
  }
  return `${lines.join('\n')}\n`
}

/** 导出文件名：去掉标题里的路径非法字符（Windows 文件名限制）。 */
export function tripMarkdownFilename(trip) {
  const raw = String(trip?.title || '旅行行程')
  return `${raw.replace(/[\\/:*?"<>|]/g, '').slice(0, 50) || '旅行行程'}.md`
}
