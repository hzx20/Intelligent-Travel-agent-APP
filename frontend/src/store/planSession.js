/** 规划会话（v1.3：生成任务搬到后端，切页面/刷新/关标签页都不再中断）
 *
 * 为什么要单独抽这一层？
 *   PlanView 是路由级页面，用户点「首页 / 旅游景点 / 旅游攻略」时组件会被销毁。
 *   v1.1 以前对话、行程、需求槽位（collected）、当前记录 id 全存在组件内部的 ref 里，
 *   组件一销毁就全没了 —— 表现就是"生成到一半切走，回来一片空白，还得从头再说一遍"。
 *
 * v1.2 把状态提到了模块级单例（ES 模块只加载一次，页面反复挂载销毁都不影响）。
 * 但那只解决了一半问题：生成请求虽然写在模块里，仍然是"一条 SSE 长连接"。
 * 连接一断，服务端就会取消那个响应生成器——所以切页面/刷新依然会打断生成，
 * 用户只能事后去历史方案里捡已经落库的结果。
 *
 * v1.3 的做法：**生成不属于任何 HTTP 连接**
 *   1. 发一句话 = 向后端"下单"（POST /api/plan/jobs），立刻拿回 job_id；
 *   2. 真正的生成跑在后端后台任务里，前端只是每隔 500ms 去拉一次增量事件；
 *   3. 页面切走、刷新、甚至关标签页，任务都在后端跑到底；
 *   4. 回来时按 job_id 续上（刷新走 sessionStorage，重开标签页走 /jobs/active），
 *      把断线期间的日志和内容一次补齐，然后继续跟进度。
 *
 * 约定：本文件只管"会话与数据"，不碰地图实例和 DOM —— 那些属于页面。
 * 需要让地图动起来时，往 mapCommand 里放一条指令，页面 watch 到就去执行。
 */
import { ref } from 'vue'
import { api, planJob } from '../api'
import { useAuth } from './auth'
import { fmtMin } from '../utils/format'

const { user } = useAuth()

const STORAGE_KEY = 'tp_plan_session_v1'
const JOB_KEY = 'tp_plan_job_v1'   // 只存 job_id：刷新后靠它把后台任务接回来
const STORAGE_VERSION = 1
const MAX_KEEP_MSGS = 60          // 只留最近 60 条，防止快照无限膨胀
const POLL_MS = 500               // 轮询间隔：够快看出"在动"，又不至于把后端打爆

/* ======================= 会话状态 ======================= */
const messages = ref([])          // {role:'user'|'ai', text, streaming?}
const input = ref('')             // 输入框草稿（切走再回来，没发出去的字也还在）
const busy = ref(false)           // 是否正在生成（切页面不会变 false）
const logs = ref([])              // 节点过程日志
const result = ref(null)          // {itinerary, verify_logs, weather, plan_id, saved}
const collected = ref({})         // 已收集的需求槽位（destination/days/party…）
const currentPlanId = ref(null)   // 当前会话对应的后端记录 id（游客为 null）
const sessionId = ref(newSessionId())
const historyView = ref(false)    // 正在回看历史快照
const liveSnapshot = ref(null)    // 回看历史前暂存的"自己的对话"
const activeDay = ref(0)          // 0=全部，其它=第 N 天（分日筛选，切页面也保留）
const showHistory = ref(false)    // 历史面板展开状态
const history = ref([])           // 历史方案列表
const mapView = ref(null)         // 最近一次地图视角 {center:[lng,lat], zoom}
const mapCommand = ref(null)      // 给页面的地图指令 {nonce, fit} | {nonce, center, zoom}
const stayMatches = ref({})       // 住宿名 → 高德真实 POI
const nearbyHotels = ref([])      // 锚点周边真实酒店（补位用）

/* 视图滚动触发器：每有一点点新内容就 +1，页面 watch 它去滚到底部。
 * 用计数器而不是让页面深监听整个 messages 数组，省得每个 token 都全量遍历。 */
const scrollKey = ref(0)

/* ======================= 非响应式内部句柄 ======================= */
let jobId = null          // 当前后端任务的 id（生成中才有值）
let pollTimer = null      // 轮询定时器
let pollSeq = 0           // 已消费到的事件序号：只拉比它新的
let pollFails = 0         // 连续失败次数（用于退避）
let jobMsg = null         // 本次生成写入的 AI 气泡（锁对象引用，不锁下标）
let runSeq = 0            // 第几次生成：让"被放弃的旧任务"的回调失效
let typingTimer = null    // 打字机定时器
let typingTarget = null   // 打字机正在写的那条消息
let persistTimer = null   // 落 sessionStorage 的防抖
let saveTimer = null      // 地图视角写回后端的防抖
let cmdSeq = 0            // 地图指令序号
let matchKey = ''         // 住宿匹配缓存键
let hydrated = false      // 是否已尝试过从 sessionStorage 恢复

function newSessionId() {
  return `s-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

/* ======================= 任务 id 的本地存档 ======================= */
/* 只存 job_id，不存别的：它足够小，也足够让刷新后的页面"接回"后台任务。
 * 用 sessionStorage 而不是 localStorage —— 每个标签页管自己的会话，
 * 两个标签页各开一局时不会互相抢任务。 */
function storeJobId(id) {
  try { id ? sessionStorage.setItem(JOB_KEY, id) : sessionStorage.removeItem(JOB_KEY) } catch { /* 隐私模式 */ }
}
function readJobId() {
  try { return sessionStorage.getItem(JOB_KEY) || '' } catch { return '' }
}

/* ======================= 地图指令 ======================= */
/** 让地图自适应整条行程（新生成/切回实时对话/清空时用） */
function mapFit() {
  mapCommand.value = { nonce: ++cmdSeq, fit: true }
}
/** 让地图跳到指定视角（打开历史快照时用）；坐标不完整就退化成自适应 */
function mapTo(center, zoom) {
  if (Array.isArray(center) && center.length === 2 && zoom) {
    mapCommand.value = { nonce: ++cmdSeq, fit: false, center, zoom }
  } else {
    mapFit()
  }
}
/** 地图拖动/缩放停稳时由页面回传：记住视角，登录用户顺便写回快照 */
function setMapView(v) {
  mapView.value = v || null
  if (!currentPlanId.value || !user.value) return
  clearTimeout(saveTimer)
  saveTimer = setTimeout(() => saveSnapshot({ mapOnly: true }), 1000)
}

/* ======================= 打字机 ======================= */
/** 逐字打出最终解说。
 *  目标消息用"对象引用"锁定，不用下标 —— 万一用户在这期间又发了一句，
 *  下标会指到新气泡上，把内容打进别人的框里（老写法就是这个隐患）。 */
function typeOut(msg, full) {
  stopTyping()
  if (!msg) return
  msg.text = ''
  msg.streaming = true
  typingTarget = msg
  let i = 0
  typingTimer = setInterval(() => {
    i = Math.min(full.length, i + 3)
    msg.text = full.slice(0, i)
    if (i >= full.length) {
      msg.streaming = false
      stopTyping()
      persistSoon()
    }
  }, 15)
}

function stopTyping() {
  if (typingTimer) { clearInterval(typingTimer); typingTimer = null }
  if (typingTarget) { typingTarget.streaming = false; typingTarget = null }
}

/* ======================= 助手回复文案 ======================= */
/** 助手回复不再干巴巴：拼出 理由/方案对比/决策依据/交通/住宿/假设 的完整解说 */
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
  // 出行难度与交通可达性（v1.1 核心评估维度，方便横向比较不同目的地）
  const tf = it.traffic || {}
  if (tf.total_min || tf.cost) {
    lines.push('')
    lines.push(`🚦 全程在途约 ${fmtMin(tf.total_min)}，交通费约 ${tf.cost || 0} 元，整体难度「${tf.difficulty_overall || '—'}」`)
    if (tf.peak_note) lines.push(`　 高峰波动：${tf.peak_note}`)
  }
  ds.forEach((d) => {
    const t = d.timing || {}
    if (!t.total_min && !(d.transit || {}).combo) return
    lines.push(
      `· 第${d.day}天 ${d.theme || ''}：难度 ${(d.difficulty || {}).level || '—'}，` +
      `在途约 ${fmtMin(t.total_min)}（步行 ${t.walk_min || 0}／车程 ${t.ride_min || 0}／候车 ${t.wait_min || 0} 分，换乘 ${t.transfers || 0} 次）`
    )
    if ((d.transit || {}).combo) lines.push(`　 交通：${d.transit.combo}`)
  })
  // 住宿推荐（v1.1）：优先推荐 + 各档对比
  const stays = it.stays || []
  if (stays.length) {
    const pick = it.stay_pick || {}
    lines.push('')
    lines.push(`🏨 住宿：优先推荐「${pick.name || stays[0].name}」${pick.why ? '——' + pick.why : ''}`)
    stays.forEach((s) => {
      lines.push(`· ${s.name}（${s.type || '—'}）：${s.reason || '—'}｜适合 ${s.fit || '通用'}｜适配 ${s.stage || '全程'}`)
    })
    lines.push('　 实时房价/房型/评价请点卡片上的携程按钮查看')
  }
  const asm = it.assumptions || []
  if (asm.length) {
    lines.push('')
    lines.push('📝 我做的假设：')
    asm.forEach((a) => lines.push(`· ${a}`))
  }
  // 注意：这里不能读页面上的 spotTotal（那是"当前显示的结果"），必须按入参行程自己算
  const total = ds.reduce((n, d) => n + (d.spots || []).length, 0)
  if (!total) lines.push('\n（提示：这批地点没有可核实的坐标，地图上无法标点）')
  return lines.join('\n')
}

/* ======================= 快照（写回后端） ======================= */
async function saveSnapshot({ mapOnly = false } = {}) {
  const id = currentPlanId.value
  if (!id || !user.value) return   // 游客不落库，状态由 sessionStorage 兜底
  const body = { map_state: mapView.value }
  if (!mapOnly) {
    // 正在看历史快照时，要存的是"自己的对话"，不能把临时翻开的那条历史盖上去
    const msgs = historyView.value && liveSnapshot.value ? liveSnapshot.value.messages : messages.value
    body.messages = msgs
      .filter((m) => m.text)
      .map((m) => ({ role: m.role, text: m.text }))
  }
  try { await api.patch(`/api/plan/${id}/snapshot`, body) } catch { /* 快照失败不打扰 */ }
}

/* ======================= 住宿推荐 × 高德真实数据 ======================= */
async function fetchStayMatches() {
  const names = (result.value?.itinerary?.stays || []).map((s) => s.name).filter(Boolean).slice(0, 6)
  if (!names.length) return
  const days = result.value?.itinerary?.days || []
  const anchor = days.flatMap((d) => d.spots || []).find((s) => s.lng && s.lat)
  const city = collected.value.destination || collected.value.city || ''
  const key = `${city}|${names.join('|')}|${anchor ? anchor.lng.toFixed(4) + ',' + anchor.lat.toFixed(4) : 'none'}`
  if (key === matchKey) return   // 同一行程不重复匹配
  matchKey = key
  try {
    const r = await api.post('/api/hotels/match', {
      city, lng: anchor?.lng ?? null, lat: anchor?.lat ?? null, names,
    })
    stayMatches.value = r.matches || {}
    nearbyHotels.value = r.nearby || []
  } catch {
    stayMatches.value = {}
    nearbyHotels.value = []
  }
}

/* ======================= 历史列表 ======================= */
async function refreshHistory() {
  if (!user.value) { history.value = []; return }
  try { history.value = (await api.get('/api/plan/history')).items } catch { /* 游客忽略 */ }
}

/* ======================= 后台任务：下单 / 轮询 / 收尾 ======================= */
/** 往对话里加一条空的 AI 气泡，并返回它的**响应式代理**。
 *
 *  这里有个坑必须说清楚：Vue 的 ref 数组里存的是 Proxy，push 进去的原始对象
 *  本身不是响应式的。如果后续对"原始对象"改 text，数据确实变了，但视图收不到通知，
 *  表现就是打字机一直在写、界面一直空着（光标 ▌ 转圈）。
 *  所以 push 完一定要从数组里再取一次——拿到的才是代理。 */
function pushAiMsg() {
  messages.value.push({ role: 'ai', text: '', streaming: true })
  const msg = messages.value[messages.value.length - 1]
  jobMsg = msg
  return msg
}

/** 当前生成中的 AI 气泡。引用一旦锁定就不再换，
 *  这样用户中途去看历史快照，token 仍然写进"自己那份对话"里，切回来能接上。
 *  刷新恢复时会留着一条空的 AI 气泡，这里优先复用它，别再 push 出第二条空的。 */
function ensureJobMsg() {
  if (jobMsg) return jobMsg
  const tail = messages.value[messages.value.length - 1]
  if (tail && tail.role === 'ai' && !tail.text) {
    tail.streaming = true
    jobMsg = tail            // 从数组取出来的一定是代理，可以直接改
    return jobMsg
  }
  return pushAiMsg()
}

/** 日志写到哪：看历史快照的时候就写进"自己的对话"存档，
 *  免得实时过程日志混到历史记录里，切回来又看不到。 */
function logSink() {
  if (historyView.value && liveSnapshot.value) return liveSnapshot.value.logs
  return logs.value
}

function schedulePoll(delay = POLL_MS) {
  clearTimeout(pollTimer)
  pollTimer = setTimeout(pollOnce, delay)
}

/** 拉一次增量。所有状态变更都从这里进来，页面在不在都一样跑。 */
async function pollOnce() {
  const id = jobId
  if (!id) return
  const myRun = runSeq
  try {
    const snap = await planJob.poll(id, pollSeq)
    if (myRun !== runSeq || id !== jobId) return   // 期间开了新会话，旧回调作废
    pollFails = 0
    applySnapshot(snap)
    if (snap.status === 'running') {
      schedulePoll(POLL_MS)
    } else {
      finishJob(snap)
    }
  } catch (e) {
    if (myRun !== runSeq || id !== jobId) return
    if (e.status === 404) {
      // 任务过期或后端重启过：不假装还在生成，老实告诉用户重发一次
      abandonJob('（这次生成的后台任务已失效，请重新发送一次）')
      return
    }
    // 网络抖一下不判死刑：任务在后端活着，退避重试就行。
    // 这也正是轮询比 SSE 省心的地方——SSE 断了得自己处理重连状态，这里重试一次就完事。
    pollFails = Math.min(pollFails + 1, 5)
    schedulePoll(Math.min(5000, POLL_MS * 2 ** pollFails))
  }
}

/** 把一帧增量事件铺到界面上（seq 去重，重复投递也不会写两遍） */
function applySnapshot(snap) {
  const msg = ensureJobMsg()
  for (const ev of snap.events || []) {
    if (!ev || ev.seq <= pollSeq) continue
    pollSeq = ev.seq
    if (ev.type === 'log') {
      logSink().push(`[${ev.data.node}] ${ev.data.message}`)
      scrollKey.value += 1
    } else if (ev.type === 'token') {
      msg.text += ev.data.text || ''
      msg.streaming = true
      scrollKey.value += 1
    }
  }
  if (snap.result) applyResult(snap.result, msg)
}

/** 处理 result 事件：要么是追问，要么是完整行程 */
function applyResult(r, msg) {
  if (r.error) {
    msg.text = `出错了：${r.error}`
    msg.streaming = false
    return
  }
  if (r.cancelled) {
    if (!msg.text) msg.text = '（已取消这次生成）'
    msg.streaming = false
    return
  }
  collected.value = r.collected || collected.value
  if (!r.done || !r.itinerary?.days) {
    // 需求还没收集全：追问文本已经由 token 事件逐字铺好了，这里只收尾
    msg.streaming = false
    if (!msg.text) msg.text = r.done ? '（这次没生成出行程，换个说法再试一次）' : ''
    return
  }
  // 生成完成时用户可能正在翻历史快照：先切回实时对话，
  // 否则结果留在看不见的地方，用户会以为"又没生成出来"
  if (historyView.value) backToLive()
  result.value = r
  currentPlanId.value = r.plan_id || null
  activeDay.value = 0
  mapFit()
  // 断线重连时可能把同一份 result 又推一遍；已经打出相同内容就别再打一次
  const reply = composeReply(r.itinerary)
  if (msg.text === reply) msg.streaming = false
  else typeOut(msg, reply)
  saveSnapshot()
  refreshHistory()
  fetchStayMatches()
  persistSoon()
}

/** 任务结束：收拾状态，把 job_id 从本地存档里摘掉 */
function finishJob(snap) {
  busy.value = false
  // 正在逐字打解说时不要抢它的 streaming 标记，交给 typeOut 自己收尾
  if (jobMsg && typingTarget !== jobMsg) jobMsg.streaming = false
  if (snap && snap.status === 'error' && jobMsg && !jobMsg.text) jobMsg.text = `出错了：${snap.error || '生成失败'}`
  jobMsg = null
  jobId = null
  pollSeq = 0
  storeJobId('')
  clearTimeout(pollTimer)
  pollTimer = null
  persistSoon()
}

/** 异常放弃（任务过期/后端重启）：给一句实话，别留个空气泡一直转圈 */
function abandonJob(tip) {
  if (jobMsg) {
    jobMsg.streaming = false
    if (!jobMsg.text) jobMsg.text = tip
  }
  busy.value = false
  jobMsg = null
  jobId = null
  pollSeq = 0
  storeJobId('')
  clearTimeout(pollTimer)
  pollTimer = null
  persistSoon()
}

/** 主动掐掉当前任务（开新会话时用），不在界面上留痕 */
function cancelJob() {
  const id = jobId
  jobId = null
  pollSeq = 0
  jobMsg = null
  storeJobId('')
  clearTimeout(pollTimer)
  pollTimer = null
  if (id) planJob.cancel(id).catch(() => { /* 取消失败不影响新会话 */ })
}

/* ======================= 生成入口 ======================= */
/** 发一句话开始（或继续）规划。
 *  可以传显式文本；不传就用输入框里的草稿。返回 Promise，方便外部等待。
 *  （只认字符串：模板里 @click="send" 会把事件对象塞进来，这里挡掉） */
async function send(explicitText) {
  const text = String(typeof explicitText === 'string' ? explicitText : input.value ?? '').trim()
  if (!text || busy.value) return
  // 正在看历史快照时发言 = 回到自己的对话再接着聊，避免把历史快照冲花
  if (historyView.value) backToLive()

  runSeq += 1                     // 本次生成的编号：被放弃的旧任务靠它认领"我已经过期"
  const myRun = runSeq
  stopTyping()
  input.value = ''
  messages.value.push({ role: 'user', text })
  const payloadMessages = messages.value.map((m) => ({ role: m.role, text: m.text }))
  const aiMsg = pushAiMsg()      // 注意：必须用返回的代理，原始对象改不动视图
  logs.value = []
  busy.value = true
  historyView.value = false
  scrollKey.value += 1
  persistNow()                    // 先把"用户已发出、正在等结果"这个状态存下来

  try {
    // 这一步只是"下单"，很快就返回；生成在后端跑，和这个页面再无关系
    const r = await planJob.create({
      history: [], user_input: text, prev_collected: collected.value,
      session_id: sessionId.value, messages: payloadMessages,
    })
    if (myRun !== runSeq) {
      // 下单期间用户又开了新会话：这个刚建出来的任务没人要，顺手取消掉
      planJob.cancel(r.job_id).catch(() => { /* 取消失败也无所谓，任务不会写进界面 */ })
      return
    }
    jobId = r.job_id
    pollSeq = 0
    pollFails = 0
    storeJobId(jobId)
    schedulePoll(0)
  } catch (e) {
    aiMsg.streaming = false
    aiMsg.text = `连接失败：${e.message}（确认后端 8000 已启动）`
    busy.value = false
    jobMsg = null
    persistSoon()
  }
}

/* ======================= 清场 ======================= */
/** 清空当前编辑区。
 *  keepLive=true 表示删的是历史条目、实时对话还在 → 保住 liveSnapshot，别一起清掉 */
function clearWorkspace({ keepLive = false } = {}) {
  stopTyping()
  result.value = null
  messages.value = []
  logs.value = []
  collected.value = {}
  currentPlanId.value = null
  if (!keepLive) liveSnapshot.value = null
  historyView.value = false
  activeDay.value = 0
  stayMatches.value = {}
  nearbyHotels.value = []
  matchKey = ''
  mapFit()               // 无行程时页面只会清掉标记与路线
  persistSoon()
}

/** 彻底重开一局：取消在跑的任务、清空会话、换新的 session_id。
 *  首页发令台带着新需求过来时用它（新需求 = 新会话，别和上一趟混在一起） */
function resetSession() {
  runSeq += 1            // 让旧任务的回调全部失效
  cancelJob()            // 顺手让后端别再为旧需求烧模型调用
  busy.value = false
  clearWorkspace({ keepLive: false })
  sessionId.value = newSessionId()
  clearStorage()
}

/** 首页发令台 → /plan?d=&days=&q=&auto=1：先重置会话，再把条件填好 */
function startFresh({ destination = '', days = 0, text = '' } = {}) {
  resetSession()
  if (destination) collected.value.destination = destination
  if (days > 0) collected.value.days = days
  input.value = String(text || '')
}

/* ======================= 历史快照 ======================= */
/** 旧记录（升级前保存、无聊天快照）也能打开：用行程内容补一份对话 */
function stubChat(p) {
  const it = p.itinerary || {}
  const ds = it.days || []
  const city = p.intent?.destination || '目的地'
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
    // 顺序很重要：先把"自己的对话"原样存下来，再切到历史。
    // （老代码先覆盖 collected 再存快照，导致 backToLive 把历史条目的需求还原成实时对话的，
    //   城市张冠李戴 → 住宿匹配按错的城市搜）
    if (!historyView.value && !liveSnapshot.value) {
      liveSnapshot.value = {
        messages: messages.value, result: result.value, logs: logs.value,
        collected: collected.value, currentPlanId: currentPlanId.value,
      }
    }
    collected.value = p.intent || {}
    historyView.value = true
    showHistory.value = false
    messages.value = p.chat?.length ? p.chat : stubChat(p)
    result.value = { itinerary: p.itinerary, verify_logs: p.verify_logs, weather: {}, plan_id: p.id, saved: true }
    currentPlanId.value = p.id
    activeDay.value = 0
    logs.value = []
    stopTyping()
    const ms = p.map_state || {}
    mapTo(ms.center, ms.zoom)
    matchKey = ''
    fetchStayMatches()
    persistSoon()
    scrollKey.value += 1
  } catch (e) { alert(e.message) }
}

/** 从历史快照切回自己的实时对话（生成中切回来也能接着看进度） */
function backToLive() {
  historyView.value = false
  if (liveSnapshot.value) {
    const live = liveSnapshot.value
    messages.value = live.messages
    result.value = live.result
    logs.value = live.logs
    collected.value = live.collected
    currentPlanId.value = live.currentPlanId ?? (live.result?.plan_id || null)
    liveSnapshot.value = null
  }
  activeDay.value = 0
  mapFit()
  persistSoon()
  scrollKey.value += 1
}

/* ======================= 删除 / 撤销 ======================= */
async function deletePlan(h) {
  const idx = history.value.findIndex((x) => x.id === h.id)
  const title = h.title || `${h.city} ${h.days}天`
  // currentPlanId 精确指向"当前打开的方案"：命中才需要清场（避免删别的条目把当前会话冲掉）
  const wasViewing = currentPlanId.value === h.id
  const isLivePlan = wasViewing && !historyView.value
  await api.del(`/api/plan/${h.id}`)
  await refreshHistory()
  if (wasViewing) {
    // 正在看的就是被删的那条 → 清空编辑区，再自动选中相邻记录
    clearWorkspace({ keepLive: !isLivePlan })
    const rest = history.value
    const next = rest[Math.min(idx, rest.length - 1)]
    if (next) await loadPlan(next.id)
  }
  return { id: h.id, title }
}

async function undoDelete(id) {
  await api.post(`/api/plan/${id}/restore`)
  await refreshHistory()
  await loadPlan(id)
}

/* ======================= 本地存档（防刷新 / 接回任务） ======================= */
/** 打包对话给存档：丢掉空消息，但**保留末尾那条正在生成的 AI 气泡**。
 *  不保留的话，刷新回来只剩用户那句话，看不出"其实还在后台跑"。 */
function packMessages(list) {
  const last = list.length - 1
  return list
    .map((m, i) => ({ m, i }))
    .filter(({ m, i }) => m.text || (i === last && m.role === 'ai' && m.streaming))
    .slice(-MAX_KEEP_MSGS)
    .map(({ m }) => ({ role: m.role, text: String(m.text || '').slice(0, 4000) }))
}

/** 组装要落盘的快照。
 *  回看历史时存"自己的实时对话"，别把临时看的那条历史当成用户的项目 */
function snapshotPayload() {
  const live = liveSnapshot.value
  const msgs = live ? live.messages : messages.value
  return {
    v: STORAGE_VERSION,
    ownerId: user.value?.id ?? 0,     // 0 = 游客
    savedAt: Date.now(),
    sessionId: sessionId.value,
    messages: packMessages(msgs),
    result: live ? live.result : result.value,
    collected: live ? live.collected : collected.value,
    currentPlanId: live ? live.currentPlanId : currentPlanId.value,
    mapView: mapView.value,
    activeDay: activeDay.value,
    input: input.value,
  }
}

function persistNow() {
  clearTimeout(persistTimer)
  persistTimer = null
  const payload = snapshotPayload()
  // 空会话不留垃圾存档
  if (!payload.messages.length && !payload.result) { clearStorage(); return }
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(payload))
  } catch {
    // 容量超了（个别行程 JSON 偏大）：退化成只存对话，至少"回来还能接着聊"
    try {
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ ...payload, result: null }))
    } catch { /* 隐私模式/满盘：存不进就算了，内存里的状态照常可用 */ }
  }
}

function persistSoon() {
  clearTimeout(persistTimer)
  persistTimer = setTimeout(persistNow, 500)
}

function clearStorage() {
  try { sessionStorage.removeItem(STORAGE_KEY) } catch { /* 隐私模式下不可用 */ }
}

/** 首屏恢复：只在第一次挂载时执行。
 *  校验归属用户 —— 换账号登录后不能把上一个账号的行程显示出来。 */
function hydrate() {
  if (hydrated) return
  hydrated = true
  let raw = ''
  try { raw = sessionStorage.getItem(STORAGE_KEY) || '' } catch { return }
  let s = null
  if (raw) {
    try { s = JSON.parse(raw) } catch { s = null }
    if (s && s.v === STORAGE_VERSION && (s.ownerId ?? 0) !== (user.value?.id ?? 0)) {
      clearStorage(); s = null
    }
  }
  if (s) {
    if (s.sessionId) sessionId.value = String(s.sessionId)
    if (Array.isArray(s.messages)) {
      messages.value = s.messages
        .filter((m) => m && typeof m === 'object')
        .map((m) => ({ role: m.role === 'ai' ? 'ai' : 'user', text: String(m.text || '') }))
    }
    if (s.result && s.result.itinerary) result.value = s.result
    if (s.collected && typeof s.collected === 'object') collected.value = s.collected
    currentPlanId.value = Number(s.currentPlanId) || null
    if (Array.isArray(s.mapView?.center) && s.mapView.center.length === 2 && s.mapView.zoom) {
      mapView.value = { center: s.mapView.center, zoom: s.mapView.zoom }
    }
    if (typeof s.input === 'string') input.value = s.input
    activeDay.value = result.value ? (Number(s.activeDay) || 0) : 0
  }

  // 上次的生成任务还挂着？把它接回来 —— 这是 v1.3 的核心：
  // 刷新不再等于"中断"，后台任务一直在跑，这里只是重新开始收事件。
  // 注意：判断依据是"本地还有 job_id"，不是"有没有旧结果"——
  // 上一轮生成完的行程本来就会留在存档里，新一单照样可能正在跑。
  const savedJob = readJobId()
  if (savedJob) {
    jobId = savedJob
    pollSeq = 0
    pollFails = 0
    jobMsg = ensureJobMsg()
    jobMsg.streaming = true
    busy.value = true
    schedulePoll(0)
    return
  }

  // 刷新/关页面留下的"半截对话"：任务确实断了（没有 job_id 可接），说句实话
  const tail = messages.value[messages.value.length - 1]
  const TIP = '（这次生成被中断了：再发一句话就能接着来）'
  if (tail && tail.role === 'user') messages.value.push({ role: 'ai', text: TIP })
  else if (tail && tail.role === 'ai' && !tail.text) tail.text = TIP

  if (result.value) fetchStayMatches()
  else tryResumeRemote()
}

/** 本地什么都没了（比如关掉标签页重新打开）时，问后端还有没有我的任务。
 *  登录用户才能用：游客的任务没有归属，找不回来。 */
async function tryResumeRemote() {
  if (busy.value || jobId || messages.value.length || historyView.value) return
  const mark = runSeq   // 记下"发起这次询问时是第几局"
  try {
    const r = await planJob.active()
    const job = r && r.job
    if (!job || job.status !== 'running') return
    // 等接口这段时间里用户可能已经自己发了新需求（runSeq 变了）或界面已经有内容，
    // 这时再把旧任务顶上来就是"抢戏"，必须放弃。
    if (mark !== runSeq || busy.value || jobId || messages.value.length) return
    const req = job.request || {}
    const list = (req.messages || []).map((m) => ({
      role: m.role === 'ai' ? 'ai' : 'user',
      text: String(m.text || ''),
    }))
    messages.value = list.length ? list : [{ role: 'user', text: String(req.user_input || '') }]
    pushAiMsg()
    collected.value = req.prev_collected || {}
    logs.value = []
    busy.value = true
    jobId = job.job_id
    pollSeq = 0
    pollFails = 0
    storeJobId(jobId)
    persistNow()
    schedulePoll(0)
  } catch { /* 游客 / 离线 / 后端没起：正常从空白开始 */ }
}

// 关页面/切后台标签页时立刻落盘（防抖没跑完就走的场景）
if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => { clearTimeout(persistTimer); persistNow() })
}

export function usePlanSession() {
  return {
    // 状态（都是 ref，页面直接读写；不要在页面里再复制一份，否则又会出现"两份状态"）
    messages, input, busy, logs, result, collected, activeDay,
    historyView, showHistory, history, mapView, mapCommand,
    stayMatches, nearbyHotels, scrollKey,
    // 动作
    hydrate, send, startFresh, refreshHistory, loadPlan, backToLive,
    deletePlan, undoDelete, setMapView,
  }
}
