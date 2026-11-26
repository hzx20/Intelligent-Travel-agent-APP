export function isLocationFreeRest(item) {
  return item?.kind === '休息' && item.lng == null && item.lat == null && !item.amap_id
}

export const routeLegKey = (origin, destination) => `${origin.id}:${destination.id}`
export const hasRouteCoordinates = item => item.lng != null && item.lat != null
export const routePoint = item => [item.lng, item.lat, item.amap_id || '']
export const isRouteMode = value => value === 'walking' || value === 'driving'

export function routePairs(items) {
  const stops = items.filter(item => !isLocationFreeRest(item))
  return stops.slice(1).map((end, index) => ({ start: stops[index], end }))
}

export function summarizeRoutes(total, routes) {
  const measured = routes.filter(route => Number.isSafeInteger(route.distance_m) && route.distance_m >= 0
    && Number.isSafeInteger(route.duration_s) && route.duration_s >= 0)
  return {
    total,
    count: measured.length,
    distance: measured.reduce((sum, route) => sum + route.distance_m, 0),
    seconds: measured.reduce((sum, route) => sum + route.duration_s, 0),
  }
}

export function summarizeAdoptedRoutes(total, routes) {
  const summary = summarizeRoutes(total, routes)
  const walking = summarizeRoutes(total, routes.filter(route => route.mode === 'walking'))
  return { ...summary, walkingDistance: walking.distance, walkingSeconds: walking.seconds, walkingCount: walking.count }
}

export function walkingLimitState(summary, limit) {
  if (!Number.isSafeInteger(limit) || limit < 1) return null
  if (summary?.count && summary.distance > limit) return 'exceeded'
  if (summary?.count && summary.count === summary.total) return 'within_segments'
  return 'pending'
}
