/** Keep independently available account data visible if the other request fails. */
export async function loadTripHomeSources(get) {
  const [trips, history] = await Promise.allSettled([
    Promise.resolve().then(() => get('/api/trips')),
    Promise.resolve().then(() => get('/api/plan/history')),
  ])
  const message = result => result.reason?.name === 'AbortError' ? '请求超时，请重试' : result.reason?.message || '加载失败，请重试'
  return {
    trips: trips.status === 'fulfilled' ? trips.value.items || [] : [],
    history: history.status === 'fulfilled' ? history.value.items || [] : [],
    tripsError: trips.status === 'rejected' ? message(trips) : '',
    historyError: history.status === 'rejected' ? message(history) : '',
  }
}
