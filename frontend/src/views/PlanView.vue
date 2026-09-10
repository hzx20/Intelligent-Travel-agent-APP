<script setup>
/** 线路规划助手 v1.1：左对话流 + 右动态地图（高德 JS API，可缩放/拖动/打点/路线）。
 *  历史方案 = 快照（聊天内容 + 地图中心/缩放 + 标记路线数据），点击原样还原。 */
import { computed, nextTick, onMounted, ref } from 'vue'
import { api, streamPlan } from '../api'
import { loadAmap } from '../utils/amap'
import { useAuth } from '../store/auth'

const { user } = useAuth()
const messages = ref([])        // {role:'user'|'ai', text, streaming?}
const input = ref('')
const busy = ref(false)
const logs = ref([])
const result = ref(null)        // {itinerary, verify_logs, weather, plan_id, saved}
const history = ref([])
const showHistory = ref(false)
const historyView = ref(false)  // 正在回看历史快照
const chatBox = ref(null)
const sessionId = ref(`s-${Date.now()}`)
const collected = ref({})

// ---- 地图状态 ----
const mapEl = ref(null)
const mapReady = ref(false)
const mapError = ref('')        // 'MISSING_KEY' | 其它错误信息
const activeDay = ref(0)        // 0=全部，其它=第 N 天
let map = null
let overlays = []               // 当前行程的标记+路线
let pinMarkers = []             // 用户手动打的点
let infoWin = null
let currentPlanId = null        // 当前快照归属的规划记录
let liveSnapshot = null         // 回看历史前暂存当前对话
let saveTimer = null
const DAY_COLORS = ['#1a6e50', '#c0392b', '#2471a3', '#7d3c98', '#b7950b', '#ca6f1e']

const days = computed(() => result.value?.itinerary?.days || [])
const visibleDays = computed(() =>
  days.value.filter((d) => !activeDay.value || d.day === activeDay.value)
)
const spotTotal = computed(() => days.value.reduce((n, d) => n + (d.spots || []).length, 0))

function colorOf(day) {
  return DAY_COLORS[(day - 1) % DAY_COLORS.length]
}
function scrollBottom() {
  nextTick(() => { if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight })
}
function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}

/** 助手回复不再干巴巴：拼出 理由/方案对比/决策依据/假设 的完整解说 */
function composeReply(it) {
  if (!it) return ''
  const lines = []
  lines.push(`✅ 已生成「${it.title || '行程'}」——${it.summary || ''}`)
  const ds = it.days || []
  if (ds.some((d) => d.reason)) {
    lines.push('')
    lines.push('📌 每天这样安排的理由：')
    ds.forEach((d) => { if (d.reason) lines.push(`· 第${d.day}天 ${d.theme || ''}：${d.reason}`) })
  }
  const alts = it.alternatives || []
  if (alts.length) {
    lines.push('')
    lines.push('⚖️ 方案对比：')
    alts.forEach((a) =>
      lines.push(`· ${a.option}（优点：${a.pros || '—'}；不足：${a.cons || '—'}）${a.chosen ? ' ← 已选' : ''}`))
  }
  const basis = it.decision_basis || []
  if (basis.length) {
    lines.push('')
    lines.push('🧭 决策依据：')
    basis.forEach((b) => lines.push(`· ${b}`))
  }
  const asm = it.assumptions || []
  if (asm.length) {
    lines.push('')
    lines.push('📝 我做的假设：')
    asm.forEach((a) => lines.push(`· ${a}`))
  }
  if (!spotTotal.value) lines.push('\n（提示：这批地点没有可核实的坐标，地图上无法标点）')
  return lines.join('\n')
}

/** 逐字打出最终解说（打字机效果） */
function typeOut(full) {
  const msg = messages.value[messages.value.length - 1]
  msg.text = ''
  let i = 0
  const timer = setInterval(() => {
    i = Math.min(full.length, i + 3)
    msg.text = full.slice(0, i)
    scrollBottom()
    if (i >= full.length) { clearInterval(timer); msg.streaming = false }
  }, 15)
}

// ================= 地图 =================
async function initMap() {
  try {
    const AMap = await loadAmap()
    map = new AMap.Map(mapEl.value, { zoom: 11, center: [120.155, 30.274], viewMode: '2D' })
    map.addControl(new AMap.ToolBar({ position: 'RB' }))
    map.addControl(new AMap.Scale())
    map.on('click', onMapClick)
    map.on('moveend', scheduleMapSave)
    map.on('zoomend', scheduleMapSave)
    mapReady.value = true
  } catch (e) {
    mapError.value = e.message === 'MISSING_KEY'
      ? 'MISSING_KEY'
      : (e.message || '地图初始化失败')
  }
}

/** 在地图上画当前行程（按 activeDay 过滤）：编号标记 + 分日颜色路线 */
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

function setDay(d) {
  activeDay.value = d
  drawPlan(false)
}

// ================= 快照 =================
function mapState() {
  if (!map) return null
  const c = map.getCenter()
  return { center: [Number(c.lng.toFixed(6)), Number(c.lat.toFixed(6))], zoom: Number(map.getZoom().toFixed(1)) }
}

/** 生成完成/对话更新 → 存聊天+地图；地图拖动停稳 → 只存地图（防抖 1 秒） */
function scheduleMapSave() {
  if (!currentPlanId || !user.value) return
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => saveSnapshot({ map_only: true }), 1000)
}

async function saveSnapshot({ map_only = false } = {}) {
  if (!currentPlanId || !user.value) return
  const body = { map_state: mapState() }
  if (!map_only) {
    body.messages = messages.value
      .filter((m) => m.text)
      .map((m) => ({ role: m.role, text: m.text }))
  }
  try { await api.patch(`/api/plan/${currentPlanId}/snapshot`, body) } catch { /* 快照失败不打扰 */ }
}

// ================= 对话 =================
async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  // 正在看历史快照时发言 = 回到自己的对话再接着聊，避免把历史快照冲花
  if (historyView.value) backToLive()
  input.value = ''
  messages.value.push({ role: 'user', text })
  const payloadMessages = messages.value.map((m) => ({ role: m.role, text: m.text }))
  busy.value = true
  logs.value = []
  historyView.value = false
  scrollBottom()
  let aiText = ''
  let gotPlan = false
  messages.value.push({ role: 'ai', text: '', streaming: true })
  try {
    await streamPlan(
      { history: [], user_input: text, prev_collected: collected.value, session_id: sessionId.value, messages: payloadMessages },
      {
        onLog: (l) => { logs.value.push(`[${l.node}] ${l.message}`); scrollBottom() },
        onToken: (t) => { aiText += t; messages.value[messages.value.length - 1].text = aiText; scrollBottom() },
        onResult: (r) => {
          if (r.error) { messages.value[messages.value.length - 1].text = `出错了：${r.error}`; return }
          collected.value = r.collected || collected.value
          if (r.done && r.itinerary?.days) {
            gotPlan = true
            result.value = r
            currentPlanId = r.plan_id || null
            drawPlan(true)
            typeOut(composeReply(r.itinerary))
            saveSnapshot()
            loadHistory()
          }
        },
      },
    )
    if (!gotPlan) messages.value[messages.value.length - 1].streaming = false
  } catch (e) {
    messages.value[messages.value.length - 1].text = `连接失败：${e.message}（确认后端 8000 已启动）`
    messages.value[messages.value.length - 1].streaming = false
  } finally {
    busy.value = false
    scrollBottom()
  }
}

// ================= 历史 =================
async function loadHistory() {
  if (!user.value) return
  try { history.value = (await api.get('/api/plan/history')).items } catch { /* 游客忽略 */ }
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

/** 清空当前编辑区（删除的是正在看的那条时用）。
 *  keepLive=true 表示删的是历史条目、实时对话还在 → 保住 liveSnapshot，别一起清掉 */
function clearWorkspace({ keepLive = false } = {}) {
  result.value = null
  messages.value = []
  logs.value = []
  collected.value = {}
  currentPlanId = null
  if (!keepLive) liveSnapshot = null
  historyView.value = false
  activeDay.value = 0
  drawPlan(true)   // 无行程时只清标记与路线
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
  const idx = history.value.findIndex((x) => x.id === h.id)
  const title = h.title || `${h.city} ${h.days}天`
  // currentPlanId 精确指向"当前打开的方案"：命中才需要清场（避免删别的条目把当前会话冲掉）
  const wasViewing = currentPlanId === h.id
  const isLivePlan = wasViewing && !historyView.value
  pendingDelete.value = null
  try {
    await api.del(`/api/plan/${h.id}`)
    await loadHistory()
    if (wasViewing) {
      // 正在看的就是被删的那条 → 清空编辑区，再自动选中相邻记录
      clearWorkspace({ keepLive: !isLivePlan })
      const rest = history.value
      const next = rest[Math.min(idx, rest.length - 1)]
      if (next) await loadPlan(next.id)
    }
    showUndoToast(title, h.id)
  } catch (e) {
    alert(`删除失败：${e.message}`)
  }
}

/** 撤销：8 秒窗口内把记录恢复回来并重新打开 */
async function undoDelete() {
  if (!toast.value?.planId) return
  const id = toast.value.planId
  clearToast()
  try {
    await api.post(`/api/plan/${id}/restore`)
    await loadHistory()
    await loadPlan(id)
  } catch (e) {
    alert(`撤销失败：${e.message}`)
  }
}

/** 旧记录（升级前保存、无聊天快照）也能打开：用行程内容补一份对话 */
function stubChat(p) {
  const it = p.itinerary || {}
  const ds = it.days || []
  const city = p.intent?.destination || '目的地'
  // 注意：这里不能用 spotTotal（它反映的是当前结果，此刻还没切到这条历史）
  const hasSpots = ds.some((d) => (d.spots || []).length > 0)
  const old = hasSpots ? '' : '（升级前的旧方案：当时还没存聊天与地图视角，行程可能为空）'
  return [
    { role: 'user', text: `去${city}玩 ${ds.length} 天` },
    { role: 'ai', text: (composeReply(it) || '（旧方案无内容）') + (old ? `\n\n${old}` : '') },
  ]
}

async function loadPlan(id) {
  try {
    const p = await api.get(`/api/plan/${id}`)
    // 只在第一次离开实时对话时暂存（切历史条目之间、删除后重建都不覆盖，避免把实时对话弄丢）
    if (!historyView.value && !liveSnapshot) {
      liveSnapshot = { messages: messages.value, result: result.value, logs: logs.value, collected: collected.value }
    }
    historyView.value = true
    showHistory.value = false
    messages.value = p.chat?.length ? p.chat : stubChat(p)
    result.value = { itinerary: p.itinerary, verify_logs: p.verify_logs, weather: {}, plan_id: p.id, saved: true }
    currentPlanId = p.id
    activeDay.value = 0
    logs.value = []
    const ms = p.map_state || {}
    if (map && Array.isArray(ms.center) && ms.center.length === 2 && ms.zoom) {
      map.setZoomAndCenter(ms.zoom, ms.center)
      drawPlan(false)
    } else {
      drawPlan(true)
    }
    scrollBottom()
  } catch (e) { alert(e.message) }
}

function backToLive() {
  historyView.value = false
  if (liveSnapshot) {
    messages.value = liveSnapshot.messages
    result.value = liveSnapshot.result
    logs.value = liveSnapshot.logs
    collected.value = liveSnapshot.collected
    liveSnapshot = null
  }
  currentPlanId = result.value?.plan_id || null
  activeDay.value = 0
  drawPlan(true)
}

onMounted(() => { loadHistory(); initMap() })
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
            <div class="hist-time">{{ h.created_at }}</div>
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
        <div class="chat-input">
          <input v-model="input" :disabled="busy" maxlength="500"
                 placeholder="例如：去成都玩 3 天，想吃火锅看熊猫"
                 @keyup.enter="send" />
          <button class="btn-send" :disabled="busy || !input.trim()" @click="send">
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
          <button class="undo" @click="undoDelete">撤销（{{ toast.seconds }}s）</button>
        </div>

        <!-- 行程卡 + 核实报告 -->
        <div v-if="result" class="bottom-panel">
          <div class="panel-scroll">
            <p v-if="!visibleDays.length" class="empty-p">这个方案没有可展示的地点。</p>
            <div v-for="d in visibleDays" :key="d.day" class="day-group">
              <div class="day-title">
                <span class="dot big" :style="{ background: colorOf(d.day) }"></span>
                第{{ d.day }}天 · {{ d.theme || '' }}
                <span class="day-count">{{ (d.spots || []).length }} 个点</span>
              </div>
              <div v-if="d.reason" class="day-reason">💡 {{ d.reason }}</div>
              <div v-for="(s, i) in d.spots" :key="i" class="spot-row">
                <span class="spot-idx" :style="{ background: colorOf(d.day) }">{{ i + 1 }}</span>
                <div class="spot-info">
                  <b>{{ s.name }}<span v-if="s.replacedNote" class="v-badge">已替换</span></b>
                  <small>{{ s.time ? s.time + ' · ' : '' }}{{ s.address || s.district || '' }}</small>
                </div>
                <a v-if="s.lng && s.lat" class="mini-map" target="_blank" rel="noopener"
                   :href="`https://uri.amap.com/marker?position=${s.lng},${s.lat}&name=${encodeURIComponent(s.name)}&src=travel-planner&coordinate=gaode`">📍 高德</a>
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
        </div>
      </section>
    </div>
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
.hist-time { font-size: 11px; color: #999; margin-top: 2px; }
.back-live { padding: 7px 16px; font-size: 12px; background: var(--green-soft); color: var(--green); border-bottom: 1px solid var(--line); }
.back-live .lk { cursor: pointer; font-weight: 700; margin-left: 8px; }
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
.toast { position: absolute; top: 56px; left: 50%; transform: translateX(-50%); background: rgba(33,43,38,.94); color: #fff; border-radius: 10px; padding: 8px 14px; font-size: 12.5px; display: flex; align-items: center; gap: 12px; z-index: 30; box-shadow: 0 4px 14px rgba(0,0,0,.25); }
.toast .undo { background: transparent; border: 1px solid rgba(255,255,255,.6); color: #fff; border-radius: 6px; padding: 3px 10px; font-size: 12px; cursor: pointer; }
.toast .undo:hover { background: rgba(255,255,255,.16); }

/* 行程卡浮层 */
.bottom-panel { position: absolute; left: 10px; right: 10px; bottom: 10px; background: rgba(255,255,255,.97); border: 1px solid var(--line); border-radius: 12px; z-index: 5; max-height: 46%; display: flex; }
.panel-scroll { overflow-y: auto; padding: 12px 16px; width: 100%; }
.empty-p { color: var(--text-sub); font-size: 12.5px; }
.day-group { margin-bottom: 10px; }
.day-title { font-weight: 700; font-size: 13px; display: flex; align-items: center; gap: 7px; }
.day-count { font-weight: 400; font-size: 11.5px; color: var(--text-sub); }
.day-reason { font-size: 12px; color: #55605a; background: var(--green-soft); border-radius: 8px; padding: 6px 10px; margin: 6px 0 2px; line-height: 1.6; }
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
