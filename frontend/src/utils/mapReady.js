// Map construction precedes the SDK's initial tile/layout completion.
// Wait once before fitting overlays; later user pans must remain untouched.
export function waitForMapReady(map, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new Error('地图初始化已取消')); return }
    let settled = false
    const finish = error => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      signal?.removeEventListener('abort', abort)
      try { map.off('complete', complete) } catch { /* Still settle after an SDK cleanup failure. */ }
      if (error) reject(error)
      else resolve()
    }
    const complete = () => finish()
    const abort = () => finish(new Error('地图初始化已取消'))
    const timer = setTimeout(() => finish(new Error('地图底图加载超时，请重试')), 15000)
    signal?.addEventListener('abort', abort, { once: true })
    try { map.on('complete', complete) }
    catch { finish(new Error('地图初始化失败，请重试')) }
  })
}
