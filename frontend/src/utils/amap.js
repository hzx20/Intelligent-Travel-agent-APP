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
const CONFIG_TIMEOUT = 8000
const SCRIPT_TIMEOUT = 12000

async function readConfig() {
  const controller = new AbortController()
  let timer
  try {
    return await Promise.race([
      (async () => {
        const { BASE } = await import('../api')
        controller.signal.throwIfAborted()
        const response = await fetch(BASE + '/api/map/config', { signal: controller.signal })
        if (!response.ok) throw new Error('地图配置获取失败，请稍后重试')
        const cfg = await response.json()
        if (cfg?.enabled === false) throw new Error('MISSING_KEY')
        if (cfg?.enabled !== true || typeof cfg.js_key !== 'string' || !cfg.js_key.trim()
          || typeof cfg.security_code !== 'string') throw new Error('地图配置暂不可用')
        return cfg
      })(),
      new Promise((_, reject) => {
        timer = setTimeout(() => {
          reject(new Error('地图配置加载超时，请检查网络后重试'))
          controller.abort()
        }, CONFIG_TIMEOUT)
      }),
    ])
  } finally { clearTimeout(timer) }
}

function readScript(cfg) {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    let settled = false
    let timer
    const finish = (error, sdk) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      script.onload = script.onerror = null
      if (error) { script.remove(); reject(error) }
      else resolve(sdk)
    }
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(cfg.js_key)}&plugin=AMap.ToolBar,AMap.Scale`
    script.onload = () => {
      const sdk = window.AMap
      const usable = ['Map', 'Marker', 'Polyline', 'ToolBar', 'Scale', 'InfoWindow']
        .every(name => typeof sdk?.[name] === 'function')
      finish(usable ? null : new Error('地图对象未就绪，请重试'), sdk)
    }
    script.onerror = () => finish(new Error('地图脚本加载失败，请检查网络后重试'))
    timer = setTimeout(() => finish(new Error('地图脚本加载超时，请检查网络后重试')), SCRIPT_TIMEOUT)
    try { document.head.appendChild(script) }
    catch { finish(new Error('地图脚本加载失败，请稍后重试')) }
  })
}

export function loadAmap() {
  if (promise) return promise
  const attempt = (async () => {
    const cfg = await readConfig()
    // 安全密钥必须在地图脚本加载【之前】挂到 window 上（高德官方要求）
    window._AMapSecurityConfig = { securityJsCode: cfg.security_code }
    return readScript(cfg)
  })()
  // 加载失败允许下次重试（比如用户刚配好 key 刷新场景）
  promise = attempt
  attempt.catch(() => { if (promise === attempt) promise = null })
  return attempt
}
