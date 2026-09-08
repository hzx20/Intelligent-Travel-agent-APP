<script setup>
/** 线路规划助手：左对话流（打字机+节点过程）+ 右行程面板（按天/核实徽章/天气/地图链接/历史切换） */
import { computed, nextTick, onMounted, ref } from 'vue'
import { api, streamPlan } from '../api'
import { useAuth } from '../store/auth'

const { user, openLogin } = useAuth()
const messages = ref([])        // {role:'user'|'ai', text, logs?}
const input = ref('')
const busy = ref(false)
const logs = ref([])            // 节点过程日志
const result = ref(null)        // 最终结果 {itinerary, verify_logs, weather, plan_id, saved}
const history = ref([])         // 登录用户的历史方案
const activeLog = ref(null)     // 查看的历史（覆盖 result 展示）
const chatBox = ref(null)
const sessionId = ref(`s-${Date.now()}`)
const collected = ref({})

const dayCount = computed(() => (result.value?.itinerary?.days || []).length)

function scrollBottom() {
  nextTick(() => { if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight })
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  input.value = ''
  messages.value.push({ role: 'user', text })
  busy.value = true
  logs.value = []
  scrollBottom()
  try {
    let aiText = ''
    messages.value.push({ role: 'ai', text: '', streaming: true })
    await streamPlan(
      { history: [], user_input: text, prev_collected: collected.value, session_id: sessionId.value },
      {
        onLog: (l) => { logs.value.push(`[${l.node}] ${l.message}`); scrollBottom() },
        onToken: (t) => { aiText += t; messages.value[messages.value.length - 1].text = aiText; scrollBottom() },
        onResult: (r) => {
          if (r.error) { messages.value[messages.value.length - 1].text = `出错了：${r.error}`; return }
          collected.value = r.collected || collected.value
          if (r.done && r.itinerary?.days) {
            result.value = r
            messages.value[messages.value.length - 1].text = `行程已生成（${(r.itinerary.days || []).length} 天），右侧查看详情，已核实地点可一键打开高德导航。`
          }
        },
      },
    )
    messages.value[messages.value.length - 1].streaming = false
    await loadHistory()
  } catch (e) {
    messages.value[messages.value.length - 1].text = `连接失败：${e.message}（确认后端 8000 已启动）`
  } finally {
    busy.value = false
    scrollBottom()
  }
}

function amapNav(spots) {
  // 高德 URI 导航：按游览顺序串联真实坐标
  const pts = spots.filter(s => s.lng && s.lat)
  if (!pts.length) return ''
  const to = pts.map((s, i) => `${s.lng},${s.lat},${encodeURIComponent(s.name)}:${i + 1}`).join('|')
  const from = `${pts[0].lng},${pts[0].lat},${encodeURIComponent(pts[0].name)}`
  return `https://uri.amap.com/navigation?from=${from}&to=${to}&via=&mode=car&policy=&src=travel-planner&coordinate=gaode&callnative=0`
}

function markerLink(s) {
  if (!s.lng || !s.lat) return ''
  return `https://uri.amap.com/marker?position=${s.lng},${s.lat}&name=${encodeURIComponent(s.name)}&src=travel-planner&coordinate=gaode`
}

/** 静态地图：把坐标拼给后端 /api/map/static（key 只在后端，前端拿到的就是一张图） */
function mapPoints(spots) {
  return (spots || []).filter(s => s.lng && s.lat).map(s => `${s.lng},${s.lat}`).join(';')
}
function mapUrl(pts, size = '640*300', zoom = 12) {
  return pts ? `/api/map/static?points=${pts}&size=${size}&zoom=${zoom}` : ''
}
const allSpots = computed(() =>
  (result.value?.itinerary?.days || []).flatMap(d => d.spots || []).filter(s => s.lng && s.lat)
)
const allPoints = computed(() => mapPoints(allSpots.value))

async function loadHistory() {
  if (!user.value) return
  try { history.value = (await api.get('/api/plan/history')).items } catch { /* 游客忽略 */ }
}

async function loadPlan(id) {
  try {
    const p = await api.get(`/api/plan/${id}`)
    activeLog.value = p
    result.value = {
      itinerary: p.itinerary, verify_logs: p.verify_logs,
      weather: {}, plan_id: p.id, saved: true, fromHistory: true,
    }
  } catch (e) { alert(e.message) }
}

onMounted(loadHistory)
</script>

<template>
  <div class="wrap plan-wrap">
    <div class="split">
      <!-- 左：对话流 -->
      <section class="chat">
        <div class="chat-head">🧭 线路规划助手
          <small>告诉我目的地/天数/偏好，我生成的每个地点都会先经高德核实</small>
        </div>
        <div class="chat-box" ref="chatBox">
          <div v-if="!messages.length" class="empty">
            试试输入：<b>"去杭州玩 2 天，喜欢安静的地方"</b>
          </div>
          <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
            <div class="bubble">{{ m.text }}<span v-if="m.streaming" class="cursor">▌</span></div>
          </div>
          <!-- 节点过程日志 -->
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

      <!-- 右：行程面板 -->
      <section class="panel-side">
        <!-- 历史方案 -->
        <div class="hist" v-if="user && history.length">
          历史方案：
          <select @change="loadPlan($event.target.value)">
            <option value="">— 选择查看 —</option>
            <option v-for="h in history" :key="h.id" :value="h.id">
              {{ h.city }} {{ h.days }}天 · {{ h.created_at }}
            </option>
          </select>
          <span v-if="activeLog" class="lk" @click="activeLog = null; result = null">返回对话</span>
        </div>

        <p v-if="!result" class="empty">还没有行程。<br />在左边说一句话，生成的路线会出现在这里（每个地点先过高德核实，查无此地的会被自动替换）。</p>
        <template v-else>
          <!-- 天气条 -->
          <div class="weather" v-if="result.weather?.ok">
            🌤 {{ result.weather.city }}：
            <span v-for="c in result.weather.casts" :key="c.date" class="w-item">
              {{ c.date.slice(5) }} {{ c.day }} {{ c.temp_low }}~{{ c.temp_high }}℃
            </span>
          </div>

          <!-- 行程路线总览（静态地图：后端代理，key 不外泄） -->
          <div v-if="allPoints" class="map-card">
            <div class="map-title">
              🗺️ 行程路线总览
              <small>按顺序连线 · 点击图片可开高德导航</small>
            </div>
            <a v-if="amapNav(allSpots)" :href="amapNav(allSpots)" target="_blank" rel="noopener">
              <img class="map-img" :src="mapUrl(allPoints, '640*300', 11)" alt="行程路线图"
                   @error="$event.target.closest('.map-card').style.display = 'none'" />
            </a>
            <img v-else class="map-img" :src="mapUrl(allPoints, '640*300', 11)" alt="行程路线图"
                 @error="$event.target.closest('.map-card').style.display = 'none'" />
          </div>

          <!-- 按天行程 -->
          <div v-for="day in result.itinerary.days" :key="day.day" class="day-card">
            <div class="day-head">第 {{ day.day }} 天
              <a v-if="amapNav(day.spots)" class="nav-link" :href="amapNav(day.spots)" target="_blank" rel="noopener">🧭 高德导航全程 →</a>
            </div>
            <img v-if="mapPoints(day.spots)" class="day-map" :src="mapUrl(mapPoints(day.spots), '260*130', 13)"
                 alt="当日路线" @error="$event.target.style.display = 'none'" />
            <div v-for="(s, i) in day.spots" :key="i" class="spot-row">
              <span class="spot-idx">{{ i + 1 }}</span>
              <div class="spot-info">
                <b>
                  {{ s.name }}
                  <span v-if="s.replacedNote" class="v-badge replace">已替换</span>
                </b>
                <small>{{ s.address || s.district || '' }}</small>
              </div>
              <a v-if="markerLink(s)" class="mini-map" :href="markerLink(s)" target="_blank" rel="noopener">📍 高德</a>
            </div>
          </div>

          <!-- 核实日志 -->
          <details class="vlog" open>
            <summary>地图核实报告（{{ (result.verify_logs || []).length }} 条）</summary>
            <div v-for="(l, i) in result.verify_logs" :key="i" class="v-line">
              <b>{{ l.spot }}</b> — {{ l.detail }}
            </div>
          </details>
          <p class="save-tip" v-if="result.saved">✅ 已存入历史方案（登录后随时回来切换对比）</p>
          <p class="save-tip" v-else-if="!user">💡 登录后规划会自动保存为历史方案</p>
        </template>
      </section>
    </div>
  </div>
</template>

<style scoped>
.plan-wrap { max-width: 1200px; }
.split { display: flex; gap: 14px; align-items: flex-start; }
.chat { flex: 1; background: #fff; border: 1px solid var(--line); border-radius: 12px; display: flex; flex-direction: column; height: 72vh; min-width: 0; }
.chat-head { padding: 12px 16px; font-weight: 700; border-bottom: 1px solid var(--line); }
.chat-head small { display: block; font-weight: 400; color: var(--text-sub); margin-top: 2px; }
.chat-box { flex: 1; overflow-y: auto; padding: 14px 16px; }
.msg { display: flex; margin-bottom: 10px; }
.msg.user { justify-content: flex-end; }
.bubble {
  max-width: 78%; padding: 9px 13px; border-radius: 12px; font-size: 13.5px;
  line-height: 1.7; white-space: pre-wrap; word-break: break-word;
}
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

.panel-side { flex: 1; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; min-width: 0; max-height: 72vh; overflow-y: auto; }
.hist { font-size: 12.5px; color: #666; margin-bottom: 10px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.hist select { border: 1px solid var(--line); border-radius: 8px; padding: 5px 8px; font-size: 12.5px; max-width: 60%; }
.lk { color: var(--green); cursor: pointer; }
.weather { background: var(--green-soft); border: 1px solid var(--green-border); border-radius: 10px; padding: 8px 12px; font-size: 12.5px; margin-bottom: 12px; }
.w-item { margin-right: 10px; }
.map-card { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; margin-bottom: 12px; background: #fff; }
.map-title { font-weight: 700; font-size: 13.5px; margin-bottom: 8px; }
.map-title small { font-weight: 400; font-size: 11.5px; color: var(--text-sub); margin-left: 6px; }
.map-img { width: 100%; border-radius: 8px; display: block; border: 1px solid var(--line); }
.day-map { width: 100%; border-radius: 8px; margin-bottom: 8px; border: 1px solid var(--line); }
.day-card { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; margin-bottom: 10px; }
.day-head { font-weight: 700; font-size: 13.5px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
.nav-link { font-size: 12px; color: var(--green); }
.spot-row { display: flex; gap: 10px; align-items: center; padding: 6px 0; border-bottom: 1px dashed var(--line); }
.spot-row:last-child { border: none; }
.spot-idx {
  width: 20px; height: 20px; border-radius: 50%; background: var(--green); color: #fff;
  display: flex; align-items: center; justify-content: center; font-size: 11px; flex-shrink: 0;
}
.spot-info { flex: 1; min-width: 0; }
.spot-info b { font-size: 13px; display: block; }
.spot-info small { color: var(--text-sub); font-size: 11.5px; }
.v-badge { font-size: 10px; color: #c0392b; border: 1px solid #e5b4b4; border-radius: 8px; padding: 0 5px; margin-left: 4px; }
.mini-map { font-size: 11.5px; color: var(--green); white-space: nowrap; }
.vlog { margin-top: 12px; font-size: 12.5px; }
.vlog summary { cursor: pointer; color: #666; }
.v-line { padding: 4px 0; border-bottom: 1px dashed var(--line); color: #555; }
.save-tip { font-size: 12px; color: var(--green); margin-top: 10px; }
@media (max-width: 640px) {
  .split { flex-direction: column; }
  .chat, .panel-side { width: 100%; height: auto; max-height: none; }
  .chat-box { height: 45vh; }
}
</style>
