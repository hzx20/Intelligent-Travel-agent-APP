export function isUnverifiedPlanPlace(place) {
  return place?.verification_status === 'unverified'
}

export function hasPlanCoordinates(place) {
  return !isUnverifiedPlanPlace(place) && Number.isFinite(place?.lng) && Number.isFinite(place?.lat)
    && place.lng >= -180 && place.lng <= 180 && place.lat >= -90 && place.lat <= 90
}

export function planDayEntries(day) {
  const spots = Array.isArray(day?.spots) ? day.spots : []
  const items = Array.isArray(day?.items) ? day.items : []
  const blocks = []
  for (const item of items) {
    if (!item || typeof item !== 'object' || [item.name, item.poi].some(value => typeof value === 'string' && value.trim())) continue
    if (typeof item.time !== 'string' || !/^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(item.time)) continue
    const duration = Number.isInteger(item.duration_min) && item.duration_min >= 0 && item.duration_min <= 1440 ? item.duration_min : null
    const kind = typeof item.type === 'string' && item.type.trim() ? item.type.trim() : '安排'
    if (kind === '休息' && !duration) continue
    blocks.push({
      name: typeof item.activity === 'string' && item.activity.trim() ? item.activity.trim() : kind,
      time: item.time,
      duration_min: duration,
      timeBlock: kind,
      note: item.note,
      ...(Object.hasOwn(item, 'cost_estimate') ? { cost_estimate: item.cost_estimate, cost_category: item.cost_category } : {}),
    })
  }
  if (!blocks.length) return spots
  return [...spots, ...blocks].sort((a, b) => String(a?.time || '99:99').localeCompare(String(b?.time || '99:99')))
}
