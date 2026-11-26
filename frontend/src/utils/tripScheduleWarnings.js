function minutes(value) {
  if (typeof value !== 'string' || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(value)) return null
  const [hours, mins] = value.split(':').map(Number)
  return hours * 60 + mins
}

/** Times are interpreted within the same displayed day; cross-midnight needs human review. */
export function scheduleWarning(current, next, route, breaks = []) {
  if (!current || !next) return ''
  const start = minutes(current.start_time), nextStart = minutes(next.start_time)
  if (start === null || nextStart === null) return ''
  if (nextStart < start) return '下一站时间早于当前安排，请检查顺序或是否跨日。'
  const duration = current.duration_min
  if (!Number.isInteger(duration) || duration < 0 || duration > 1440) return ''
  const remaining = nextStart - start - duration
  if (remaining < 0) return `下一站开始时当前安排可能尚未结束（相差 ${-remaining} 分钟），请核对。`
  if (route?.status === 'ok' && !route.preview && Number.isFinite(route.duration_s) && route.duration_s >= 0) {
    const travelMinutes = Math.ceil(route.duration_s / 60)
    let cursor = start + duration, longest = 0
    const intervals = breaks.map(item => [minutes(item.start_time), item.duration_min])
    if (intervals.some(([time, length]) => time === null || !Number.isInteger(length) || length < 0 || length > 1440)) return ''
    for (const [time, length] of intervals.filter(([, length]) => length > 0).sort(([left], [right]) => left - right)) {
      if (time >= nextStart) break
      longest = Math.max(longest, time - cursor)
      cursor = Math.max(cursor, time + length)
    }
    longest = Math.max(longest, nextStart - cursor)
    if (travelMinutes > longest) {
      const gap = breaks.length ? '计入休息后，可连续用于交通的时间' : '前往下一站可能'
      return `按已查询交通估计，${gap}还差 ${travelMinutes - longest} 分钟；实际耗时可能变化。`
    }
  }
  return ''
}
