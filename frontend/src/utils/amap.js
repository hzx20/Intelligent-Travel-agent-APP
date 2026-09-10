/**
 * 高德 JS 地图加载器（v1.1 动态地图）。
 *
 * 为什么不把 <script> 直接写进 index.html？
 *  1. key 存在后端 .env，由 /api/map/config 下发，源码里不出现任何钥匙；
 *  2. 未配置 key 时页面不能白屏，要能优雅降级成提示；
 *  3. 整个应用只该加载一次地图脚本，重复加载会报错。
 *
 * 用法：const AMap = await loadAmap()  // 失败时抛错，调用方 catch 后显示引导
 */
let promise = null

export function loadAmap() {
  if (promise) return promise
  promise = (async () => {
    const cfg = await fetch('/api/map/config').then((r) => r.json())
    if (!cfg.enabled) {
      throw new Error('MISSING_KEY')
    }
    // 安全密钥必须在地图脚本加载【之前】挂到 window 上（高德官方要求）
    window._AMapSecurityConfig = { securityJsCode: cfg.security_code }
    await new Promise((resolve, reject) => {
      const s = document.createElement('script')
      s.src = `https://webapi.amap.com/maps?v=2.0&key=${cfg.js_key}&plugin=AMap.ToolBar,AMap.Scale`
      s.onload = resolve
      s.onerror = () => reject(new Error('地图脚本加载失败（检查网络）'))
      document.head.appendChild(s)
    })
    if (!window.AMap) throw new Error('地图对象未就绪')
    return window.AMap
  })()
  // 加载失败允许下次重试（比如用户刚配好 key 刷新场景）
  promise.catch(() => { promise = null })
  return promise
}
