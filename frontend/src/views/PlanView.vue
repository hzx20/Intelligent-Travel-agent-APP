<script setup>
/** 线路规划助手 v1.2：左对话流 + 右动态地图（高德 JS API，可缩放/拖动/打点/路线）。
 *  历史方案 = 快照（聊天内容 + 地图中心/缩放 + 标记路线数据），点击原样还原。
 *
 *  v1.2 起「会话状态」搬到了 store/planSession.js —— 对话、行程、需求槽位、
 *  正在跑的生成请求都在那一层，用户切到别的模块再回来不会丢。
 *  本文件只负责两件事：把状态显示出来，以及维护地图（地图实例属于页面，不属于会话）。 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { loadAmap } from '../utils/amap'
import { usePlanSession } from '../store/planSession'
import { useAuth } from '../store/auth'
import { fmtDist, fmtMin, partyAdvice } from '../utils/format'
import RegionPicker from '../components/regionpicker.vue'
import DateRangePicker from '../components/daterangepicker.vue'

const route = useRoute()
const router = useRouter()
// 登录态：模板里要判断"显示历史面板 / 显示登录提示"，与 store 里的 user 是同一个 ref
const { user } = useAuth()
const {
  messages, input, busy, logs, result, collected, history, historyView, activeDay,
  showHistory, mapView, mapCommand, stayMatches, nearbyHotels, scrollKey,
  hydrate, send, startFresh, refreshHistory, loadPlan, backToLive, deletePlan,
  undoDelete, setMapView,
} = usePlanSession()

// ---- 地图状态（页面级：组件销毁时地图跟着销毁，会话状态不受影响）----
const mapEl = ref(null)
const mapReady = ref(false)
const mapError = ref('')        // 'MISSING_KEY' | 其它错误信息
const chatBox = ref(null)
let map = null
let overlays = []               // 当前行程的标记+路线
let pinMarkers = []             // 用户手动打的点
let infoWin = null
const DAY_COLORS = ['#1a6e50', '#c0392b', '#2471a3', '#7d3c98', '#b7950b', '#ca6f1e']

const days = computed(() => result.value?.itinerary?.days || [])
const visibleDays = computed(() =>
  days.value.filter((d) => !activeDay.value || d.day === activeDay.value)
)
/** 目的地选择框的回显：以会话里记着的城市为准（切页面回来不会"上面有字、下面是空的"） */
const pickerCity = computed(() => collected.value.destination || '')

/** 住宿展示板：AI 推荐里匹配到真实 POI 的 + 周边真实酒店补位（凑满 6 家，全部带实拍图） */
const stayBoard = computed(() => {
  const stays = result.value?.itinerary?.stays || []
  const out = []
  for (const s of stays) {
    const real = stayMatches.value[s.name]
    if (real) out.push({ kind: 'stay', stay: s, real })
  }
  for (const n of nearbyHotels.value) {
    if (out.length >= 6) break
    out.push({ kind: 'nearby', real: n })
  }
  return out
})

function colorOf(day) {
  return DAY_COLORS[(day - 1) % DAY_COLORS.length]
}
/** 难度配色：轻松绿 / 适中黄 / 较费体力红 */
const DIFF_COLORS = { 轻松: '#1a6e50', 适中: '#b7950b', 较费体力: '#c0392b' }
function diffColor(level) {
  return DIFF_COLORS[level] || '#7a8a80'
}
function scrollBottom() {
  nextTick(() => { if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight })
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}

// ================= 地图 =================
async function initMap() {
  try {
    const AMap = await loadAmap()
    // 地图脚本是异步加载的：等待期间用户可能已经切走，此时容器没了就别再建实例
    if (!mapEl.value || map) return
    map = new AMap.Map(mapEl.value, { zoom: 11, center: [120.155, 30.274], viewMode: '2D' })
    map.addControl(new AMap.ToolBar({ position: 'RB' }))
    map.addControl(new AMap.Scale())
    map.on('click', onMapClick)
    map.on('moveend', onMapMoved)
    map.on('zoomend', onMapMoved)
    mapReady.value = true
    // 可能是"切页面回来"：优先还原上次看到的视角，没有才自适应整条行程
    syncMap({ restore: true })
  } catch (e) {
    mapError.value = e.message === 'MISSING_KEY'
      ? 'MISSING_KEY'
      : (e.message || '地图初始化失败')
  }
}

/** 把当前行程画到地图上（按 activeDay 过滤）：编号标记 + 分日颜色路线 */
function drawPlan(fit = true) {
  if (!map) return
  if (overlays.length) { map.remove(overlays); overlays = [] }
  let seq = 0
  const pts = []
  visibleDays.value.forEach((d) => {
    const color = colorOf(d.day)
    const dayPts = []
    ;(d.spots || []).forEach((s) => {
      if (!s.lng || !s.lat) return
      seq += 1
      dayPts.push([s.lng, s.lat])
      const m = new AMap.Marker({
        position: [s.lng, s.lat],
        content: `<div class="amap-pin" style="--pc:${color}">${seq}</div>`,
        anchor: 'bottom-center',
        title: s.name,
      })
      m.on('click', () => openInfo(s))
      map.add(m)
      overlays.push(m)
    })
    pts.push(...dayPts)
    if (dayPts.length > 1) {
      const line = new AMap.Polyline({
        path: dayPts, strokeColor: color, strokeWeight: 4,
        strokeOpacity: 0.9, showDir: true, lineJoin: 'round',
      })
      map.add(line)
      overlays.push(line)
    }
  })
  if (fit && pts.length) map.setFitView(overlays, false, [50, 50, 50, 50])
}

/** 会话层发出的地图指令（新生成→自适应；打开历史→跳到当时视角） */
function syncMap({ restore = false } = {}) {
  if (!map) return
  const cmd = mapCommand.value
  // 刚挂载时优先还原"上次看到的样子"，指令只在之后到达时才执行
  if (restore && mapView.value?.center?.length === 2 && mapView.value.zoom) {
    map.setZoomAndCenter(mapView.value.zoom, mapView.value.center)
    drawPlan(false)
    return
  }
  if (cmd && !cmd.fit && Array.isArray(cmd.center)) {
    map.setZoomAndCenter(cmd.zoom || 11, cmd.center)
    drawPlan(false)
    return
  }
  drawPlan(true)
}

function openInfo(s) {
  if (!map || !s.lng || !s.lat) return
  const nav = `https://uri.amap.com/marker?position=${s.lng},${s.lat}&name=${encodeURIComponent(s.name)}&src=travel-planner&coordinate=gaode`
  infoWin = infoWin || new AMap.InfoWindow({ anchor: 'bottom-center' })
  infoWin.setContent(
    `<div class="amap-iw"><b>${esc(s.name)}</b>` +
    `<div class="amap-iw-sub">${esc(s.time ? s.time + ' · ' : '')}${esc(s.address || s.district || '')}</div>` +
    (s.note ? `<div class="amap-iw-note">💡 ${esc(s.note)}</div>` : '') +
    `<a class="amap-iw-link" target="_blank" rel="noopener" href="${nav}">📍 高德导航</a></div>`
  )
  infoWin.open(map, [s.lng, s.lat])
}

/** 打点：点地图任意处放一个参考标记，点标记本身可删，也可一键清空 */
function onMapClick(e) {
  if (!map) return
  const m = new AMap.Marker({
    position: e.lnglat,
    content: '<div class="amap-pin pin-user">📍</div>',
    anchor: 'bottom-center',
  })
  m.on('click', () => { map.remove(m); pinMarkers = pinMarkers.filter((x) => x !== m) })
  map.add(m)
  pinMarkers.push(m)
}

function clearPins() {
  if (map && pinMarkers.length) map.remove(pinMarkers)
  pinMarkers = []
}

/** 分日筛选：状态存在会话里，切页面回来还是"只看第 2 天" */
function setDay(d) {
  activeDay.value = d
}

/** 地图拖动/缩放停稳：记住视角（下次进来还原），登录用户顺便写回历史快照 */
function onMapMoved() {
  if (!map) return
  const c = map.getCenter()
  setMapView({
    center: [Number(c.lng.toFixed(6)), Number(c.lat.toFixed(6))],
    zoom: Number(map.getZoom().toFixed(1)),
  })
}

// ================= 快捷条件模块 =================
/** 目的地选中/清除：destination 必须是纯城市名（后端地图核实按 Spot.city 精确匹配），
 *  区县作为倾向词进输入文本，由需求澄清模块自然吸收 */
function onRegionPick({ city, district }) {
  if (!city) {
    delete collected.value.destination
    return
  }
  collected.value.destination = city
  if (!input.value.trim()) {
    input.value = district ? `去${city}${district}玩` : `去${city}玩`
  }
}

/** 日期范围选中/清除：dates 用中文区间、days 给出天数，澄清时可少问一轮 */
function onDateRangePick(r) {
  if (!r) {
    delete collected.value.dates
    delete collected.value.days
    return
  }
  collected.value.dates = r.dates
  collected.value.days = r.days
}

// ================= 首页发令台带过来的需求 =================
/** ?d=城市&days=天数&q=原话&auto=1：
 *  填好输入框和条件，auto=1 就直接开跑——不能让用户把刚说过的话再打一遍。
 *  新需求 = 新会话：先把上一趟的会话清掉，否则会带着旧城市去澄清，问出驴唇不对马嘴的问题。 */
function applyPrefill() {
  const q = route.query
  const text = String(q?.q || '')
  if (!text) return
  const city = String(q.d || '')
  const days = Number(q.days || 0)
  if (String(q.auto) === '1') {
    // 先把 query 抹掉再发：刷新页面不该重复跑一次、也不该重复计一次调用
    router.replace({ path: '/plan' })
    startFresh({ destination: city, days, text })
    setTimeout(() => send(), 350)
  } else {
    input.value = text
    if (city) collected.value.destination = city
    if (days > 0) collected.value.days = days
  }
}

// ================= 删除历史方案（含二次确认与撤销） =================
const UNDO_SECONDS = 8
const pendingDelete = ref(null)   // 等待二次确认的条目 id
const toast = ref(null)           // {text, planId, seconds}
let toastTimer = null
let undoCountdown = null

function clearToast() {
  clearTimeout(toastTimer); clearInterval(undoCountdown)
  toastTimer = null; undoCountdown = null
  toast.value = null
}

function showUndoToast(title, planId) {
  clearToast()
  toast.value = { text: `已删除「${title}」`, planId, seconds: UNDO_SECONDS }
  undoCountdown = setInterval(() => {
    if (!toast.value) return
    toast.value.seconds -= 1
    if (toast.value.seconds <= 0) clearToast()
  }, 1000)
  toastTimer = setTimeout(clearToast, UNDO_SECONDS * 1000 + 200)
}

async function confirmDelete(h, ev) {
  ev?.stopPropagation?.()
  pendingDelete.value = null
  try {
    const { id, title } = await deletePlan(h)
    showUndoToast(title, id)
  } catch (e) {
    alert(`删除失败：${e.message}`)
  }
}

/** 撤销：8 秒窗口内把记录恢复回来并重新打开 */
async function undoDeleteClick() {
  const id = toast.value?.planId
  if (!id) return
  clearToast()
  try {
    await undoDelete(id)
  } catch (e) {
    alert(`撤销失败：${e.message}`)
  }
}

// ================= 生命周期 =================
watch(scrollKey, scrollBottom)                      // 新内容进来就滚到底
watch(activeDay, () => drawPlan(false))             // 分日筛选
watch(mapCommand, () => syncMap())                  // 会话层要求换视角

onMounted(() => {
  hydrate()            // 先把上次的会话接回来（刷新/关标签页也能续上）
  applyPrefill()       // 再看有没有首页带过来的新需求
  refreshHistory()
  scrollBottom()
  initMap()
})

// 组件销毁时把地图实例一并回收：不 destroy 的话每次进页面都会多留一份监听与画布
onBeforeUnmount(() => {
  if (map) {
    try { map.destroy() } catch { /* 高德内部异常不影响页面卸载 */ }
    map = null
  }
  overlays = []
  pinMarkers = []
  infoWin = null
  mapReady.value = false
})
</script>


<template>
  <div class="wrap plan-wrap">
    <div class="split">
      <!-- 左：对话流 -->
      <section class="chat">
        <div class="chat-head">
          🧭 线路规划助手
          <small>告诉我目的地/天数/偏好，生成的每个地点先经高德核实，再实时画到右边地图</small>
          <button v-if="user" class="hist-toggle" @click="showHistory = !showHistory">
            🕘 历史方案（{{ history.length }}）
          </button>
        </div>

        <!-- 历史快照列表：时间/标题/摘要，点击完整还原 -->
        <div v-if="showHistory" class="hist-list">
          <p v-if="!history.length" class="hist-empty">还没有保存过的方案。生成一次行程就会自动存进来。</p>
          <div v-for="h in history" :key="h.id" class="hist-item" @click="pendingDelete === h.id ? null : loadPlan(h.id)">
            <div class="hist-title">
              {{ h.title || `${h.city} ${h.days}天` }}
              <span class="hist-meta">{{ h.city }} · {{ h.days }}天 · {{ h.spots }}个地点</span>
              <!-- 删除入口：悬停出现，点击不触发进入会话 -->
              <button v-if="pendingDelete !== h.id" class="hist-del" title="删除这条方案"
                      @click.stop="pendingDelete = h.id">🗑</button>
            </div>
            <div class="hist-summary">{{ h.summary || '（无摘要）' }}</div>
            <div class="hist-time">
              {{ h.created_at }}
              <span v-if="h.difficulty" class="hist-diff" :style="{ background: diffColor(h.difficulty) }">
                {{ h.difficulty }}
              </span>
              <span v-if="h.travel_min" class="hist-travel">⏱ 在途 {{ fmtMin(h.travel_min) }}</span>
              <span v-if="h.traffic_cost" class="hist-travel">🎫 交通 {{ h.traffic_cost }} 元</span>
              <span v-if="h.stay_range" class="hist-travel">🏨 {{ h.stay_range }}</span>
            </div>
            <!-- 二次确认：就地展开，不用系统弹窗挡住视线 -->
            <div v-if="pendingDelete === h.id" class="hist-confirm" @click.stop>
              确定删除「{{ h.title || '这条方案' }}」？删除后 8 秒内可撤销。
              <div class="confirm-btns">
                <button class="mini" @click="pendingDelete = null">取消</button>
                <button class="mini danger" @click="confirmDelete(h, $event)">删除</button>
              </div>
            </div>
          </div>
        </div>
        <div v-if="historyView" class="back-live">
          正在查看历史快照
          <span class="lk" @click="backToLive">← 返回当前对话</span>
        </div>

        <!-- 生成中提示：既解释"为什么回来还在转"，也告诉用户切页面不会中断 -->
        <div v-if="busy" class="run-hint">
          ⏳ AI 正在生成路线，切换页面不会中断，回到这里可以接着看
        </div>

        <div class="chat-box" ref="chatBox">
          <div v-if="!messages.length" class="empty">
            试试输入：<b>"去杭州玩 2 天，喜欢安静的地方"</b><br />
            <small>生成的行程会实时标到右侧地图，右侧地图也能随手打点参考</small>
          </div>
          <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
            <div class="bubble">{{ m.text }}<span v-if="m.streaming" class="cursor">▌</span></div>
          </div>
          <div v-if="logs.length" class="proc">
            <div v-for="(l, i) in logs" :key="i" class="proc-line">▸ {{ l }}</div>
          </div>
        </div>
        <!-- 快捷条件：目的地联想 + 你想去多久（选中后并入需求，澄清少问一轮） -->
        <div class="chat-addons" :class="{ locked: busy }">
          <RegionPicker :initial="pickerCity" @select="onRegionPick" />
          <DateRangePicker @select="onDateRangePick" @clear="onDateRangePick(null)" />
        </div>
        <div class="chat-input">
          <input v-model="input" :disabled="busy" maxlength="500"
                 placeholder="输入目的地或告诉我你的想法"
                 @keyup.enter="send()" />
          <button class="btn-send" :disabled="busy || !input.trim()" @click="send()">
            {{ busy ? '规划中…' : '发送' }}
          </button>
        </div>
      </section>

      <!-- 右：动态地图主区域 -->
      <section class="map-side">
        <div ref="mapEl" class="map-box"></div>

        <!-- 地图不可用时的降级引导（行程列表仍可看） -->
        <div v-if="mapError" class="map-fallback">
          <template v-if="mapError === 'MISSING_KEY'">
            🗺️ 动态地图还没配置钥匙：在项目根目录 <b>.env</b> 里补上
            <code>AMAP_JS_KEY=你的key</code> 和 <code>AMAP_JS_SECURITY_CODE=你的安全密钥</code>
            （高德「Web端(JS API)」类型），保存后重启后端即可点亮。下面的行程列表不受影响。
          </template>
          <template v-else>⚠️ 地图加载失败：{{ mapError }}（刷新页面重试）</template>
        </div>

        <!-- 天气 -->
        <div v-if="result?.weather?.ok" class="weather-chip">
          🌤 {{ result.weather.city }}
          <span v-for="c in result.weather.casts.slice(0, 3)" :key="c.date">
            {{ c.date.slice(5) }} {{ c.day }} {{ c.temp_low }}~{{ c.temp_high }}℃
          </span>
        </div>

        <!-- 工具条：分日筛选 + 清除打点 -->
        <div class="map-toolbar" v-if="days.length">
          <button class="chip" :class="{ on: activeDay === 0 }" @click="setDay(0)">全部</button>
          <button v-for="d in days" :key="d.day" class="chip" :class="{ on: activeDay === d.day }" @click="setDay(d.day)">
            <span class="dot" :style="{ background: colorOf(d.day) }"></span>第{{ d.day }}天 {{ d.theme }}
          </button>
          <button class="chip pin-chip" @click="clearPins">🧹 清除打点</button>
        </div>

        <div v-if="!result && mapReady" class="map-empty-hint">
          在左边说一句话，生成的路线会实时画在这张地图上<br />
          <small>滚轮缩放 · 按住拖动 · 点地图任意处打参考点（点标记可删）</small>
        </div>

        <!-- 删除后的轻量提示（含撤销入口） -->
        <div v-if="toast" class="toast">
          <span>{{ toast.text }}</span>
          <button class="undo" @click="undoDeleteClick">撤销（{{ toast.seconds }}s）</button>
        </div>

      </section>
    </div>

    <!-- 信息区：地图下方独立展示（随页面滚动完整可见，不截断不折叠） -->
    <section v-if="result" class="info-below">
      <div class="info-body">
            <p v-if="!visibleDays.length" class="empty-p">这个方案没有可展示的地点。</p>
            <div v-for="d in visibleDays" :key="d.day" class="day-group">
              <div class="day-title">
                <span class="dot big" :style="{ background: colorOf(d.day) }"></span>
                第{{ d.day }}天 · {{ d.theme || '' }}
                <span class="day-count">{{ (d.spots || []).length }} 个点</span>
                <span class="diff-badge" :style="{ background: diffColor(d.difficulty?.level) }">
                  {{ d.difficulty?.level || '难度待评估' }}
                </span>
              </div>
              <div v-if="d.difficulty?.basis?.length" class="diff-basis">
                <span class="basis-tag" v-for="(b, bi) in d.difficulty.basis" :key="bi">{{ b }}</span>
              </div>
              <div class="transit-row" v-if="d.transit">
                <span class="mode-chip" v-for="(m, mi) in (d.transit.modes || [])" :key="mi">{{ m }}</span>
                <span class="combo">{{ d.transit.combo }}</span>
                <div v-if="(d.transit.stations || []).length" class="sub">🚉 {{ d.transit.stations.join(' · ') }}</div>
                <div v-if="d.transit.parking" class="sub">🅿️ {{ d.transit.parking }}</div>
              </div>
              <div class="timing-row" v-if="d.timing">
                ⏱ 在途 {{ fmtMin(d.timing.total_min) }}
                <span>步行 {{ d.timing.walk_min }}′</span>
                <span>车程 {{ d.timing.ride_min }}′</span>
                <span>候车 {{ d.timing.wait_min }}′</span>
                <span>换乘 {{ d.timing.transfers }} 次</span>
                <span class="peak">高峰 +{{ d.timing.peak_buffer_min }}′</span>
              </div>
              <div v-if="d.reason" class="day-reason">💡 {{ d.reason }}</div>
              <div v-for="(s, i) in d.spots" :key="i" class="spot-row">
                <span class="spot-idx" :style="{ background: colorOf(d.day) }">{{ i + 1 }}</span>
                <div class="spot-info">
                  <b>{{ s.name }}<span v-if="s.replacedNote" class="v-badge">已替换</span></b>
                  <small>{{ s.time ? s.time + ' · ' : '' }}{{ s.address || s.district || '' }}</small>
                </div>
                <span v-if="s.access?.mode" class="access-chip" :title="s.access.note || ''">
                  {{ s.access.mode }}<template v-if="s.access.min"> {{ s.access.min }}′</template>
                </span>
                <a v-if="s.lng && s.lat" class="mini-map" target="_blank" rel="noopener"
                   :href="`https://uri.amap.com/marker?position=${s.lng},${s.lat}&name=${encodeURIComponent(s.name)}&src=travel-planner&coordinate=gaode`">📍 高德</a>
              </div>
            </div>
            <!-- 住宿推荐：AI 推荐 × 高德真实数据合一（每家都带实拍图与真距） -->
            <div v-if="result" class="stay-block">
              <div class="stay-head">🏨 住宿推荐
                <span class="src-note">名称/实拍图/评分/距离均来自高德真实数据 · 实时房价与房型点携程按钮查看</span>
              </div>
              <div v-if="partyAdvice(collected.party)" class="party-tip">
                👥 你说「{{ collected.party }}」→ 建议订：{{ partyAdvice(collected.party) }}
              </div>
              <p v-if="!stayBoard.length" class="empty-p">正在匹配景点周边的真实酒店…</p>
              <div v-for="b in stayBoard" :key="b.real.name" class="stay-card">
                <img v-if="b.real.photo" class="stay-img" :src="b.real.photo" alt="酒店实拍"
                     @error="$event.target.style.display = 'none'" />
                <div v-else class="stay-img stay-img-empty">🏨</div>
                <div class="stay-main">
                  <div class="stay-top">
                    <b>{{ b.real.name }}</b>
                    <span v-if="b.kind === 'stay'" class="stay-type">{{ b.stay.type }}</span>
                    <span v-if="b.kind === 'nearby'" class="stay-type">附近备选</span>
                  </div>
                  <div class="stay-price-line">
                    <span v-if="b.real.rating">⭐ {{ b.real.rating }}</span>
                    <span v-if="b.real.distance_m"> · 距核心景点 {{ fmtDist(b.real.distance_m) }}</span>
                  </div>
                  <div class="hotel-sub" v-if="b.real.address">📍 {{ b.real.address }}</div>
                  <template v-if="b.kind === 'stay'">
                    <div class="stay-near" v-if="b.stay.near">🧭 {{ b.stay.near }}</div>
                    <div class="stay-pc" v-if="(b.stay.pros || []).length || (b.stay.cons || []).length">
                      <span v-for="(p, pi) in b.stay.pros" :key="'p' + pi" class="pro">＋ {{ p }}</span>
                      <span v-for="(c, ci) in b.stay.cons" :key="'c' + ci" class="con">－ {{ c }}</span>
                    </div>
                    <div class="stay-reason" v-if="b.stay.reason">{{ b.stay.reason }}</div>
                    <div class="stay-meta">👤 适合：{{ b.stay.fit }} · 📅 适配：{{ b.stay.stage }}<span v-if="b.stay.distance"> · ✈️ 机场 {{ b.stay.distance.airport_min || '—' }} 分 · 🚄 车站 {{ b.stay.distance.station_min || '—' }} 分</span></div>
                  </template>
                  <div class="hotel-links">
                    <a class="lk-chip big" :href="b.real.links.ctrip" target="_blank" rel="noopener">🔍 携程看实时房价·房型·评价</a>
                    <a class="lk-chip dim" :href="b.real.links.qunar" target="_blank" rel="noopener">去哪儿</a>
                    <a class="lk-chip dim" :href="b.real.links.tongcheng" target="_blank" rel="noopener">同程</a>
                  </div>
                </div>
              </div>
              <div v-if="result.itinerary.stay_pick?.why" class="stay-why">
                💡 最终建议：{{ result.itinerary.stay_pick.why }}
              </div>
            </div>
            <details class="vlog">
              <summary>地图核实报告（{{ (result.verify_logs || []).length }} 条）</summary>
              <div v-for="(l, i) in result.verify_logs" :key="i" class="v-line">
                <b>{{ l.spot }}</b> — {{ l.detail }}
              </div>
            </details>
            <p class="save-tip" v-if="result.saved">✅ 快照已保存（聊天内容 + 地图视角，历史方案可原样还原）</p>
            <p class="save-tip" v-else-if="!user">💡 登录后规划会自动保存为历史快照</p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.plan-wrap { max-width: 1280px; }
.split { display: flex; gap: 14px; align-items: stretch; height: calc(100vh - 96px); min-height: 520px; }

/* 左：聊天 */
.chat { width: 400px; flex-shrink: 0; background: #fff; border: 1px solid var(--line); border-radius: 12px; display: flex; flex-direction: column; min-height: 0; }
.chat-head { padding: 12px 16px; font-weight: 700; border-bottom: 1px solid var(--line); position: relative; }
.chat-head small { display: block; font-weight: 400; color: var(--text-sub); margin-top: 2px; font-size: 11.5px; }
.hist-toggle { position: absolute; right: 12px; top: 12px; border: 1px solid var(--line); background: #fff; border-radius: 8px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
.hist-toggle:hover { border-color: var(--green); color: var(--green); }
.hist-list { max-height: 260px; overflow-y: auto; border-bottom: 1px solid var(--line); background: #fafbf9; }
.hist-empty { padding: 14px 16px; font-size: 12.5px; color: var(--text-sub); }
.hist-item { padding: 10px 16px; border-bottom: 1px dashed var(--line); cursor: pointer; position: relative; }
.hist-item:hover { background: var(--green-soft); }
.hist-del { position: absolute; right: 12px; top: 8px; border: none; background: transparent; font-size: 13px; cursor: pointer; opacity: 0; transition: opacity .15s; padding: 2px 4px; border-radius: 6px; }
.hist-item:hover .hist-del { opacity: .7; }
.hist-del:hover { opacity: 1; background: #fde9e9; }
/* 触屏没有 hover：删除入口常驻显示 */
@media (hover: none) { .hist-del { opacity: .6; } }
.hist-confirm { margin-top: 8px; background: #fff8e6; border: 1px solid #e8d9a0; border-radius: 8px; padding: 8px 10px; font-size: 12px; color: #7a6520; cursor: default; line-height: 1.6; }
.confirm-btns { display: flex; gap: 8px; justify-content: flex-end; margin-top: 6px; }
.mini { border: 1px solid var(--line); background: #fff; border-radius: 6px; padding: 3px 12px; font-size: 12px; cursor: pointer; }
.mini.danger { background: #c0392b; border-color: #c0392b; color: #fff; }
.hist-title { font-size: 13px; font-weight: 700; }
.hist-meta { font-weight: 400; font-size: 11.5px; color: var(--text-sub); margin-left: 6px; }
.hist-summary { font-size: 12px; color: #555; margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hist-time { font-size: 11px; color: #999; margin-top: 2px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.hist-diff { color: #fff; border-radius: 6px; padding: 1px 6px; font-size: 10.5px; }
.hist-travel { color: #6a7d6a; }
.back-live { padding: 7px 16px; font-size: 12px; background: var(--green-soft); color: var(--green); border-bottom: 1px solid var(--line); }
.back-live .lk { cursor: pointer; font-weight: 700; margin-left: 8px; }
.run-hint { padding: 7px 16px; font-size: 12px; background: #fff8e6; color: #7a6520; border-bottom: 1px solid #e8d9a0; }
.chat-box { flex: 1; overflow-y: auto; padding: 14px 16px; min-height: 0; }
.msg { display: flex; margin-bottom: 10px; }
.msg.user { justify-content: flex-end; }
.bubble { max-width: 88%; padding: 9px 13px; border-radius: 12px; font-size: 13px; line-height: 1.7; white-space: pre-wrap; word-break: break-word; }
.msg.user .bubble { background: var(--green); color: #fff; border-bottom-right-radius: 4px; }
.msg.ai .bubble { background: #f4f6f3; border: 1px solid var(--line); border-bottom-left-radius: 4px; }
.cursor { animation: blink 1s infinite; color: var(--green); }
@keyframes blink { 50% { opacity: 0; } }
.proc { margin: 8px 0; padding: 8px 12px; background: var(--green-soft); border-radius: 8px; }
.proc-line { font-size: 12px; color: var(--green); line-height: 1.9; }
/* 快捷条件行：目的地联想 + 你想去多久 */
.chat-addons { display: flex; gap: 8px; align-items: flex-end; padding: 10px 14px 0; flex-wrap: wrap; }
.chat-addons.locked { opacity: .55; pointer-events: none; }
.chat-input { display: flex; gap: 8px; padding: 12px 14px; border-top: 1px solid var(--line); }
.chat-input input { flex: 1; border: 1.5px solid #ccc; border-radius: 10px; padding: 10px 12px; font-size: 13.5px; }
.chat-input input:focus { outline: none; border-color: var(--green); }
.btn-send { background: var(--green); color: #fff; border: none; border-radius: 10px; padding: 10px 20px; }
.btn-send:disabled { opacity: .5; }
.empty { text-align: center; color: var(--text-sub); font-size: 13px; margin-top: 40px; line-height: 2; }

/* 右：地图 */
.map-side { flex: 1; position: relative; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; min-width: 0; background: #eef2ee; }
.map-box { position: absolute; inset: 0; }
.map-fallback { position: absolute; top: 10px; left: 50%; transform: translateX(-50%); max-width: 86%; background: #fff8e6; border: 1px solid #e8d9a0; color: #7a6520; border-radius: 10px; padding: 8px 14px; font-size: 12px; line-height: 1.7; z-index: 5; }
.map-fallback code { background: #f3ecd2; padding: 0 4px; border-radius: 4px; }
.weather-chip { position: absolute; top: 10px; left: 10px; background: rgba(255,255,255,.94); border: 1px solid var(--line); border-radius: 10px; padding: 6px 12px; font-size: 12px; z-index: 5; }
.weather-chip span { margin-right: 8px; }
.map-toolbar { position: absolute; top: 10px; right: 10px; display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; max-width: 70%; z-index: 5; }
.chip { background: rgba(255,255,255,.94); border: 1px solid var(--line); border-radius: 999px; padding: 4px 12px; font-size: 12px; cursor: pointer; display: inline-flex; align-items: center; gap: 5px; }
.chip.on { background: var(--green); border-color: var(--green); color: #fff; }
.pin-chip:hover { color: var(--green); }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.dot.big { width: 11px; height: 11px; }
.map-empty-hint { position: absolute; top: 46%; left: 50%; transform: translate(-50%,-50%); text-align: center; color: #6a7d6a; font-size: 14px; line-height: 2; pointer-events: none; background: rgba(255,255,255,.8); padding: 14px 22px; border-radius: 12px; z-index: 4; }
.map-empty-hint small { font-size: 11.5px; color: #8a9a8a; }
/* 信息区：地图下方独立展示（自然文档流，完整可见不截断） */
.info-below { margin-top: 14px; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px 18px; }
.toast { position: absolute; top: 56px; left: 50%; transform: translateX(-50%); background: rgba(33,43,38,.94); color: #fff; border-radius: 10px; padding: 8px 14px; font-size: 12.5px; display: flex; align-items: center; gap: 12px; z-index: 30; box-shadow: 0 4px 14px rgba(0,0,0,.25); }
.toast .undo { background: transparent; border: 1px solid rgba(255,255,255,.6); color: #fff; border-radius: 6px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
.toast .undo:hover { background: rgba(255,255,255,.16); }

.empty-p { color: var(--text-sub); font-size: 12.5px; }
.day-group { margin-bottom: 10px; }
.day-title { font-weight: 700; font-size: 13px; display: flex; align-items: center; gap: 7px; }
.day-count { font-weight: 400; font-size: 11.5px; color: var(--text-sub); }
.day-reason { font-size: 12px; color: #55605a; background: var(--green-soft); border-radius: 8px; padding: 6px 10px; margin: 6px 0 2px; line-height: 1.6; }
/* 出行难度 / 交通可达性 / 在途耗时 */
.diff-badge { margin-left: auto; color: #fff; border-radius: 999px; padding: 1px 10px; font-size: 11px; font-weight: 600; }
.diff-basis { display: flex; flex-wrap: wrap; gap: 5px; margin: 6px 0 2px; }
.basis-tag { background: #f2f5f2; border: 1px solid var(--line); border-radius: 6px; padding: 1px 8px; font-size: 11px; color: #5c6a60; }
.transit-row { margin: 6px 0 2px; font-size: 11.5px; color: #4d5a52; }
.mode-chip { display: inline-block; background: var(--green-soft); border: 1px solid var(--green-border, #cfe0d3); color: var(--green); border-radius: 6px; padding: 1px 8px; font-size: 11px; margin-right: 5px; }
.transit-row .combo { color: #55605a; }
.transit-row .sub { color: #7a8a80; margin-top: 2px; }
.timing-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 6px 0 4px; font-size: 11.5px; color: #4d5a52; background: #f7f9f7; border: 1px solid var(--line); border-radius: 8px; padding: 5px 10px; }
.timing-row .peak { margin-left: auto; color: #b7950b; }
.access-chip { font-size: 10.5px; color: #6a7d6a; border: 1px solid var(--line); border-radius: 6px; padding: 1px 6px; white-space: nowrap; }
/* 住宿推荐卡片 */
.stay-block { margin: 12px 0 6px; }
.stay-head { font-weight: 700; font-size: 13px; margin-bottom: 6px; }
.stay-card { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; margin-bottom: 8px; display: flex; gap: 12px; align-items: flex-start; }
.stay-img { width: 132px; height: 99px; border-radius: 8px; object-fit: cover; flex-shrink: 0; background: #f0f3f0; }
.stay-img-empty { display: flex; align-items: center; justify-content: center; font-size: 30px; color: #b9c6bc; }
.stay-main { flex: 1; min-width: 0; }
.stay-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.stay-top b { font-size: 13.5px; }
.stay-type { font-size: 11px; color: var(--text-sub); border: 1px solid var(--line); border-radius: 6px; padding: 0 6px; }
.stay-price-line { font-size: 12.5px; color: var(--green); font-weight: 700; margin: 3px 0; }
.stay-price-line .rate { font-weight: 600; }
.stay-dist, .stay-meta { font-size: 11.5px; color: #7a8a80; margin-top: 5px; }
.stay-pc { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 5px; }
.stay-pc .pro { font-size: 11.5px; color: #1a6e50; background: var(--green-soft); border-radius: 6px; padding: 1px 7px; }
.stay-pc .con { font-size: 11.5px; color: #a94438; background: #fbeeec; border-radius: 6px; padding: 1px 7px; }
.stay-reason { font-size: 12px; color: #4d5a52; margin-top: 6px; line-height: 1.6; }
.stay-why { font-size: 12.5px; font-weight: 600; color: var(--green); background: var(--green-soft); border-radius: 8px; padding: 7px 10px; margin-top: 4px; line-height: 1.6; }
/* 住宿推荐补充样式（来源标注 / 床型建议 / 距离承诺） */
.src-note { font-weight: 400; font-size: 11px; color: var(--text-sub); margin-left: 6px; }
.party-tip { font-size: 12px; color: #55605a; background: var(--green-soft); border-radius: 8px; padding: 6px 10px; margin: 6px 0; line-height: 1.6; }
.stay-near { font-size: 11.5px; color: var(--green); margin-top: 4px; }
.hotel-sub { font-size: 11px; color: var(--text-sub); margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hotel-links { display: flex; gap: 6px; margin-top: 6px; }
.lk-chip { font-size: 11px; color: #fff; background: var(--green); border-radius: 6px; padding: 2px 8px; text-decoration: none; white-space: nowrap; }
.lk-chip.big { font-size: 12px; font-weight: 600; padding: 4px 12px; }
.lk-chip.dim { background: #fff; color: var(--green); border: 1px solid var(--green); }
.no-real-tip { color: #b7950b; }
.spot-row { display: flex; gap: 10px; align-items: center; padding: 5px 0; border-bottom: 1px dashed var(--line); }
.spot-row:last-child { border: none; }
.spot-idx { width: 20px; height: 20px; border-radius: 50%; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 11px; flex-shrink: 0; }
.spot-info { flex: 1; min-width: 0; }
.spot-info b { font-size: 12.5px; display: block; }
.spot-info small { color: var(--text-sub); font-size: 11.5px; }
.v-badge { font-size: 10px; color: #c0392b; border: 1px solid #e5b4b4; border-radius: 8px; padding: 0 5px; margin-left: 4px; }
.mini-map { font-size: 11.5px; color: var(--green); white-space: nowrap; }
.vlog { margin-top: 10px; font-size: 12px; }
.vlog summary { cursor: pointer; color: #666; }
.v-line { padding: 4px 0; border-bottom: 1px dashed var(--line); color: #555; }
.save-tip { font-size: 11.5px; color: var(--green); margin-top: 8px; }

@media (max-width: 900px) {
  .split { flex-direction: column; height: auto; }
  .chat { width: 100%; height: 56vh; }
  .map-side { height: 60vh; }
}
</style>

<style>
/* 地图覆盖物样式：高德把标记 DOM 插进地图容器，拿不到 scoped 属性，必须全局 */
.amap-pin {
  width: 22px; height: 22px; border-radius: 50%;
  background: var(--pc, #1a6e50); color: #fff;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
  border: 2px solid #fff; box-shadow: 0 1px 5px rgba(0,0,0,.4);
}
.amap-pin.pin-user { background: #2471a3; font-size: 12px; }
.amap-iw { font-size: 12.5px; line-height: 1.6; max-width: 230px; }
.amap-iw-sub { color: #666; font-size: 11.5px; margin: 2px 0 4px; }
.amap-iw-note { color: #888; font-size: 11.5px; margin-bottom: 4px; }
.amap-iw-link { color: #1a6e50; font-weight: 600; text-decoration: none; }
</style>
