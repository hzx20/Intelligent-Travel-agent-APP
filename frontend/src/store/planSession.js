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
import { ref, watch, computed } from 'vue'
import { api, planJob } from '../api'
import { useAuth } from './auth'
import { fmtMin } from '../utils/format'
import { hasPlanCoordinates } from '../utils/planLocation'
import { registerAuthChangeGuard } from './authChangeGuard'
import { newPlanCreation, cancelPlanCreation, readPlanCreation, savePlanCreation, clearPlanCreation } from './planCreationAttempt'
import { createPlanHistory, validPlanMeta } from './planHistory'

const { user } = useAuth()

const STORAGE_KEY = 'tp_plan_session_v2.'
const JOB_KEY = 'tp_plan_job_v2.'
let scopeOwner = user.value?.id ?? 0
const STORAGE_VERSION = 1
const MAX_KEEP_MSGS = 60          // 只留最近 60 条，防止快照无限膨胀
const POLL_MS = 500               // 轮询间隔：够快看出"在动"，又不至于把后端打爆

/* ======================= 会话状态 ======================= */
const messages = ref([])          // {role:'user'|'ai', text, streaming?}
const input = ref('')             // 输入框草稿（切走再回来，没发出去的字也还在）
const reconnecting = ref(false)
const busy = ref(false)           // 是否正在生成（切页面不会变 false）
const stopping = ref(false),stopError=ref('')
const pendingCreation=ref(null),creationUncertain=ref(false),creationRecoveryError=ref(''),creationStatus=ref('')
const logs = ref([])              // 节点过程日志
const result = ref(null)          // {itinerary, verify_logs, weather, plan_id, saved}
const collected = ref({})         // 已收集的需求槽位（destination/days/party…）
const currentPlanId = ref(null)   // 当前会话对应的后端记录 id（游客为 null）
const sessionId = ref(newSessionId())
const historyView = ref(false)    // 正在回看历史快照
const liveSnapshot = ref(null)    // 回看历史前暂存的"自己的对话"
const activeDay = ref(0)          // 0=全部，其它=第 N 天（分日筛选，切页面也保留）
const showHistory = ref(false)    // 历史面板展开状态
const historyCtl=createPlanHistory({api,owner:()=>user.value?.id??0})
const history=historyCtl.items,historyLoading=historyCtl.listLoading,historyError=historyCtl.listError
const historyDetailLoading=historyCtl.detailLoading,historyDetailError=historyCtl.detailError,historyOperation=historyCtl.operation
const snapshotState=computed(()=>historyCtl.records.get(currentPlanId.value)||null)
const historyNotice=ref('')
const mapView = ref(null)         // 最近一次地图视角 {center:[lng,lat], zoom}
const mapCommand = ref(null)      // 给页面的地图指令 {nonce, fit} | {nonce, center, zoom}
const stayMatches = ref({})       // 住宿名 → 高德真实 POI
const nearbyHotels = ref([])      // 锚点周边真实酒店（补位用）
/* 住宿匹配是否还在请求中。
 * 没有这个标志，页面只能靠"列表为空"判断，于是空结果也显示成"正在匹配…"，
 * 永远转不完 —— 用户以为一直在加载，其实是没匹配到。 */
const stayLoading = ref(false)
/* 本次生成的起点时刻（毫秒），0 表示没在跑。
 * 单独存这里而不是让页面自己算：用户中途切页面再回来，计时不会从 0 重新开始。 */
const jobStartedAt = ref(0)

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
let typingFull=''
let persistTimer = null   // 落 sessionStorage 的防抖
let saveTimer = null      // 地图视角写回后端的防抖
let cmdSeq = 0            // 地图指令序号
let matchKey = ''         // 住宿匹配缓存键
let hydrated = false      // 是否已尝试过从 sessionStorage 恢复
let resultApplied = false // 本次任务是否已经消费过 result（事件流与顶层字段会各来一次）

function newSessionId() {
  return `s-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

/* ======================= 任务 id 的本地存档 ======================= */
/* 只存 job_id，不存别的：它足够小，也足够让刷新后的页面"接回"后台任务。
 * 用 sessionStorage 而不是 localStorage —— 每个标签页管自己的会话，
 * 两个标签页各开一局时不会互相抢任务。 */
function storeJobId(id) {
  try { id ? sessionStorage.setItem(JOB_KEY+scopeOwner,JSON.stringify({ownerId:scopeOwner,id})) : sessionStorage.removeItem(JOB_KEY+scopeOwner) } catch { /* 隐私模式 */ }
}
function readJobId() {
  try { const value=JSON.parse(sessionStorage.getItem(JOB_KEY+scopeOwner)||'null');return value?.ownerId===scopeOwner && typeof value.id==='string' ? value.id : '' } catch { return '' }
}

function migrateLegacyStorage() {
  try {
    const raw=sessionStorage.getItem('tp_plan_session_v1'),s=raw?JSON.parse(raw):null
    if(s?.v===1 && Number.isSafeInteger(s.ownerId) && s.ownerId>=0){
      if(!sessionStorage.getItem(STORAGE_KEY+s.ownerId))sessionStorage.setItem(STORAGE_KEY+s.ownerId,raw)
      const id=sessionStorage.getItem('tp_plan_job_v1')
      if(id && !sessionStorage.getItem(JOB_KEY+s.ownerId))sessionStorage.setItem(JOB_KEY+s.ownerId,JSON.stringify({ownerId:s.ownerId,id}))
      sessionStorage.removeItem('tp_plan_session_v1');sessionStorage.removeItem('tp_plan_job_v1')
    }
  } catch { /* Never attach an unbound legacy job to another account. */ }
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
  saveSnapshot({mapOnly:true})
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
  typingFull=full
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
  if (typingTarget) { if(typingFull)typingTarget.text=typingFull;typingTarget.streaming = false; typingTarget = null }
  typingFull=''
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
function saveSnapshot({ mapOnly = false, reply=null, msg=null, base=null } = {}) {
  const live=!mapOnly&&historyView.value&&liveSnapshot.value?liveSnapshot.value:null
  const id = live?live.currentPlanId:currentPlanId.value
  if (!id || !user.value) return   // 游客不落库，状态由 sessionStorage 兜底
  const body = {}
  if(mapOnly){if(!mapView.value)return;body.map_state=mapView.value}
  if (!mapOnly) {
    // 正在看历史快照时，要存的是"自己的对话"，不能把临时翻开的那条历史盖上去
    const msgs = live?live.messages:messages.value
    body.messages = msgs
      .map((m) => ({ role: m.role, text: m===msg&&reply!==null?reply:m.text })).filter(m=>m.text).slice(-60)
  }
  historyCtl.queue(id,body,{delay:mapOnly?1000:0,expected:base})
}

/* ======================= 住宿推荐 × 高德真实数据 ======================= */
async function fetchStayMatches() {
  const mark=runSeq
  const names = (result.value?.itinerary?.stays || []).map((s) => s.name).filter(Boolean).slice(0, 6)
  if (!names.length) {
    // 模型这次没给住宿建议：别把"没结果"伪装成"还在加载"
    stayMatches.value = {}
    nearbyHotels.value = []
    stayLoading.value = false
    return
  }
  const days = result.value?.itinerary?.days || []
  const anchor = days.flatMap((d) => d.spots || []).find(hasPlanCoordinates)
  const city = collected.value.destination || collected.value.city || ''
  const key = `${city}|${names.join('|')}|${anchor ? anchor.lng.toFixed(4) + ',' + anchor.lat.toFixed(4) : 'none'}`
  if (key === matchKey) return   // 同一行程不重复匹配
  matchKey = key
  stayLoading.value = true
  // 兜底：即使请求被浏览器挂住（接口内部要连高德），8 秒后也把 loading 关掉，
  // 让界面落到"没有可用住宿卡片"的实话，而不是无限转圈。
  const bail = setTimeout(() => { if(mark===runSeq)stayLoading.value = false }, 8000)
  try {
    const r = await api.post('/api/hotels/match', {
      city, lng: anchor?.lng ?? null, lat: anchor?.lat ?? null, names,
    }, { timeout: 7000 })
    if(mark!==runSeq)return
    stayMatches.value = r.matches || {}
    nearbyHotels.value = r.nearby || []
  } catch {
    if(mark!==runSeq)return
    stayMatches.value = {}
    nearbyHotels.value = []
  } finally {
    clearTimeout(bail)
    if(mark===runSeq)stayLoading.value = false
  }
}

/* ======================= 历史列表 ======================= */
async function refreshHistory() {
  await historyCtl.refresh()
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
    if(snap?.job_id!==id || !['running','done','error','cancelled'].includes(snap.status) || !Array.isArray(snap.events) || !Number.isSafeInteger(snap.seq) || snap.seq<pollSeq)throw Error('暂时无法确认生成进度，请稍后重连。')
    pollFails = 0
    reconnecting.value = false
    applySnapshot(snap)
    if (snap.status === 'running') {
      schedulePoll(POLL_MS)
    } else {
      finishJob(snap)
    }
  } catch (e) {
    if (myRun !== runSeq || id !== jobId) return
    if (e.status === 404) {
      abandonJob('（后台任务不存在或已过期，请先查看历史方案，再决定是否重新生成）')
      return
    }
    // 网络抖一下不判死刑：任务在后端活着，退避重试就行。
    // 这也正是轮询比 SSE 省心的地方——SSE 断了得自己处理重连状态，这里重试一次就完事。
    reconnecting.value = true
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
    if(ev.type==='reset'){
      msg.text='';msg.streaming=true;resultApplied=false
      logSink().push(ev.data?.message || '正在恢复本次生成。')
    } else if (ev.type === 'log') {
      logSink().push(`[${ev.data.node}] ${ev.data.message}`)
      scrollKey.value += 1
    } else if (ev.type === 'token') {
      msg.text += ev.data.text || ''
      msg.streaming = true
      scrollKey.value += 1
    } else if (ev.type === 'result' && ev.data) {
      /* 失败与取消只在这里出现：后端异常收尾时走的是 emit("result", {error})
       * 而顶层 result 字段保持 null。以前只认顶层字段，导致模型报错后
       * 界面上那条 AI 气泡一直停在"正在整理需求…"，用户等了五分钟不知道发生了什么。 */
      applyResult(ev.data, msg)
    }
  }
  if(!resultApplied && typeof snap.text==='string')msg.text=snap.text
  if (snap.result) applyResult(snap.result, msg)
}

/** 处理 result 事件：要么是追问，要么是完整行程 */
function applyResult(r, msg) {
  // 同一份 result 可能从事件流和顶层字段各来一次（成功时两者都有），只认第一次
  if (resultApplied) return
  resultApplied = true
  if (r.error) {
    msg.text = `出错了：${r.error}`
    msg.streaming = false
    return
  }
  if (r.cancelled) {
    msg.text = (msg.text ? msg.text+'\n' : '')+'（已取消这次生成）'
    msg.streaming = false
    return
  }
  collected.value = r.collected || collected.value
  if (!r.done || !r.itinerary?.days) {
    // 需求还没收集全：追问文本已经由 token 事件逐字铺好了，这里只收尾
    msg.streaming = false
    if(r.question)msg.text=r.question
    else if (!msg.text) msg.text = r.done ? '（这次没生成出行程，换个说法再试一次）' : ''
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
  let autoSnapshotBase=null
  if(r.saved&&r.plan_id){
    const meta={id:r.plan_id,version:r.plan_version,revision:r.plan_revision,status:'completed'}
    if(validPlanMeta(meta)){historyCtl.register({...meta,intent:r.collected||{},itinerary:r.itinerary,chat:pendingCreation.value?.payload.messages||[],map_state:{}});autoSnapshotBase=meta}
    else historyDetailError.value='此任务没有快照版本，自动保存已暂停；请从历史重新打开。'
  }
  if (msg.text === reply) msg.streaming = false
  else typeOut(msg, reply)
  if(autoSnapshotBase)saveSnapshot({reply,msg,base:autoSnapshotBase})
  refreshHistory()
  fetchStayMatches()
  persistSoon()
}

/** 任务结束：收拾状态，把 job_id 从本地存档里摘掉 */
function finishJob(snap) {
  releaseCreation()
  busy.value = false
  reconnecting.value = false
  jobStartedAt.value = 0
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
  if(pendingCreation.value){creationUncertain.value=true;creationStatus.value=tip}
  if (jobMsg) {
    jobMsg.streaming = false
    if (!jobMsg.text) jobMsg.text = tip
  }
  busy.value = false
  reconnecting.value = false
  jobStartedAt.value = 0
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
  jobStartedAt.value = 0
  storeJobId('')
  clearTimeout(pollTimer)
  pollTimer = null
  if (id) planJob.cancel(id).catch(() => { /* 取消失败不影响新会话 */ })
}

async function stopGeneration(){
  if(stopping.value)return
  if(pendingCreation.value){await stopCreation();return}
  if(!busy.value)return
  if(!jobId){stopError.value='正在提交任务，请稍后再停止；请勿重复发送。';return}
  const id=jobId,mark=runSeq
  stopping.value=true;stopError.value=''
  try{
    await planJob.cancel(id)
    if(mark===runSeq && id===jobId)schedulePoll(0)
  }catch(e){
    if(mark===runSeq && id===jobId)stopError.value=`暂时无法确认是否停止：${e.message} 仍会核对后台进度。`
  }finally{if(mark===runSeq)stopping.value=false}
}

/* ======================= 生成入口 ======================= */
/** 发一句话开始（或继续）规划。
 *  可以传显式文本；不传就用输入框里的草稿。返回 Promise，方便外部等待。
 *  （只认字符串：模板里 @click="send" 会把事件对象塞进来，这里挡掉） */
function releaseCreation(){
  if(!pendingCreation.value)return
  try{clearPlanCreation(scopeOwner,pendingCreation.value.request_id);pendingCreation.value=null;creationUncertain.value=false;creationRecoveryError.value=''}
  catch{creationUncertain.value=true;creationRecoveryError.value='无法清除本次提交标识，请先检查浏览器存储。'}
}
function creationBody(attempt){return {...attempt.payload,request_id:attempt.request_id,guest_key:attempt.guest_key}}
function creationActive(mark,owner,attempt){return mark===runSeq && owner===scopeOwner && pendingCreation.value?.request_id===attempt.request_id && pendingCreation.value.mode===attempt.mode}
async function confirmCreation(attempt,mark,owner,verifiedCapability=null){
  const capability=verifiedCapability||await planJob.capabilities()
  if(!creationActive(mark,owner,attempt))return
  if(capability?.reliable_creation!==true||capability?.scoped_guests!==true)throw Error('当前服务不支持可靠提交，请更新服务后继续；不会使用旧创建接口。')
  const r=attempt.mode==='cancel'?await planJob.cancelCreation(creationBody(attempt)):await planJob.create(creationBody(attempt))
  if(!creationActive(mark,owner,attempt))return
  const validJob=typeof r?.job_id==='string'&&/^[0-9a-f]{32}$/i.test(r.job_id)
  const emptyCancel=attempt.mode==='cancel'&&r?.job_id===null&&['cancelled','expired'].includes(r.status)
  if(r?.request_id!==attempt.request_id||r?.owner_id!==owner||!['running','done','error','cancelled','expired'].includes(r?.status)||
     !(validJob||emptyCancel)||attempt.mode==='cancel'&&r.status==='running')throw Error('提交回执无法确认，请继续同次提交，避免另建任务。')
  if(historyView.value)backToLive()
  if(emptyCancel){
    releaseCreation()
    const tail=messages.value.at(-1),msg=tail?.role==='ai'?tail:pushAiMsg();msg.text=r.status==='expired'?'（本次提交已过期，请核对历史；不会重新生成）':'（已取消这次生成）';msg.streaming=false
    busy.value=false;jobStartedAt.value=0;jobMsg=null;creationStatus.value=''
    persistNow();return
  }
  jobId=r.job_id;pollSeq=0;pollFails=0;resultApplied=false
  creationUncertain.value=false;creationStatus.value='';creationRecoveryError.value=''
  const tail=messages.value.at(-1);jobMsg=tail?.role==='ai'?tail:pushAiMsg();jobMsg.text='';jobMsg.streaming=true
  busy.value=true;storeJobId(jobId);persistNow();schedulePoll(0)
}
function creationFailed(e,mark,owner,attempt){
  if(mark!==runSeq||owner!==scopeOwner)return
  busy.value=false;stopping.value=false
  creationUncertain.value=Boolean(pendingCreation.value)
  creationStatus.value=[404,405,428].includes(e.status)?'当前服务无法可靠确认本次提交；不会退回旧创建接口，请核对服务状态。':e.message
  if(attempt){const list=historyView.value&&liveSnapshot.value?liveSnapshot.value.messages:messages.value,tail=list.at(-1);if(tail?.role==='ai'){tail.streaming=false;tail.text=attempt.mode==='cancel'?'停止结果待确认，请继续核对本次提交。':'提交结果待确认，请继续同次提交。'}}
  jobStartedAt.value=0;persistSoon()
}
async function resumeCreation(){
  if(busy.value||stopping.value||creationRecoveryError.value||!pendingCreation.value)return
  const attempt=pendingCreation.value,owner=scopeOwner,mark=++runSeq
  busy.value=true;creationStatus.value='';stopping.value=attempt.mode==='cancel'
  try{await confirmCreation(attempt,mark,owner)}catch(e){creationFailed(e,mark,owner,attempt)}
  finally{if(mark===runSeq)stopping.value=false}
}
async function stopCreation(){
  if(stopping.value||!pendingCreation.value||creationRecoveryError.value)return
  const owner=scopeOwner,knownId=jobId
  let attempt
  try{attempt=cancelPlanCreation(owner,pendingCreation.value);savePlanCreation(owner,attempt)}
  catch(e){creationStatus.value=e.message;return}
  const mark=++runSeq;pendingCreation.value=attempt;creationUncertain.value=true;creationStatus.value=''
  clearTimeout(pollTimer);pollTimer=null;jobId=null;if(!knownId)storeJobId('')
  busy.value=true;stopping.value=true
  try{await confirmCreation(attempt,mark,owner)}catch(e){
    creationFailed(e,mark,owner,attempt)
    if(knownId && mark===runSeq && owner===scopeOwner){jobId=knownId;busy.value=true;creationUncertain.value=false;stopError.value='停止结果待确认，仍在核对后台进度。';schedulePoll(0)}
  }
  finally{if(mark===runSeq)stopping.value=false}
}
function discardCreation(){
  if(busy.value||stopping.value||!window.confirm('这次提交可能已在后台生成。放弃只清除本标签重试信息，不会停止任务或删除历史，确定放弃吗？'))return
  try{clearPlanCreation(scopeOwner);pendingCreation.value=null;creationUncertain.value=false;creationRecoveryError.value='';creationStatus.value='已放弃本标签恢复，请先核对历史再发新需求。'}
  catch{creationStatus.value='无法清除本次提交，请检查浏览器存储后重试。'}
}

async function send(explicitText){
  const text=String(typeof explicitText==='string'?explicitText:input.value??'').trim()
  if(!text||busy.value||pendingCreation.value||creationRecoveryError.value)return
  if(historyView.value)backToLive()
  const owner=scopeOwner,mark=++runSeq
  historyCtl.invalidateReads()
  busy.value=true;creationStatus.value='';stopError.value='';stopping.value=false
  let attempt
  try{
    const capability=await planJob.capabilities()
    if(mark!==runSeq||owner!==scopeOwner)return
    if(capability?.reliable_creation!==true||capability?.scoped_guests!==true)throw Error('当前服务不支持可靠提交，请更新服务后再发送；不会使用旧创建接口。')
    stopTyping()
    const payloadMessages=[...messages.value.map(m=>({role:m.role,text:m.text})),{role:'user',text}]
    attempt=newPlanCreation(owner,{history:[],user_input:text,prev_collected:collected.value,session_id:sessionId.value,messages:payloadMessages})
    savePlanCreation(owner,attempt);pendingCreation.value=attempt;creationUncertain.value=true
    stopTyping();resultApplied=false;input.value=''
    messages.value.push({role:'user',text});pushAiMsg();logs.value=[]
    jobStartedAt.value=Date.now();historyView.value=false;scrollKey.value++;persistNow()
    await confirmCreation(attempt,mark,owner,capability)
  }catch(e){creationFailed(e,mark,owner,attempt)}
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
  if(pendingCreation.value||creationRecoveryError.value){creationStatus.value='请先确认或停止上次提交，再开始新需求。';return false}
  reconnecting.value = false
  runSeq += 1            // 让旧任务的回调全部失效
  cancelJob()            // 顺手让后端别再为旧需求烧模型调用
  busy.value = false
  stopping.value=false;stopError.value=''
  clearWorkspace({ keepLive: false })
  sessionId.value = newSessionId()
  clearStorage()
  return true
}

/** 首页发令台 → /plan?d=&days=&q=&auto=1：先重置会话，再把条件填好 */
function startFresh({ destination = '', days = 0, text = '' } = {}) {
  if(resetSession()===false)return false
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
  return loadHistory(id)
}
async function loadHistory(id,{discard=false}={}){
    const p=await historyCtl.read(id,{discard})
    if(!p)return
    // 顺序很重要：先把"自己的对话"原样存下来，再切到历史。
    // （老代码先覆盖 collected 再存快照，导致 backToLive 把历史条目的需求还原成实时对话的，
    //   城市张冠李戴 → 住宿匹配按错的城市搜）
    if (!historyView.value && !liveSnapshot.value) {
      liveSnapshot.value = {
        messages: messages.value, result: result.value, logs: logs.value,
        collected: collected.value, currentPlanId: currentPlanId.value,
        mapView:mapView.value,
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
    mapView.value=Array.isArray(ms.center)&&ms.center.length===2?ms:null
    mapTo(ms.center, ms.zoom)
    matchKey = ''
    fetchStayMatches()
    persistSoon()
    scrollKey.value += 1
}

/** 从历史快照切回自己的实时对话（生成中切回来也能接着看进度） */
function backToLive() {
  historyCtl.invalidateReads()
  historyView.value = false
  if (liveSnapshot.value) {
    const live = liveSnapshot.value
    messages.value = live.messages
    result.value = live.result
    logs.value = live.logs
    collected.value = live.collected
    currentPlanId.value = live.currentPlanId ?? (live.result?.plan_id || null)
    mapView.value=live.mapView||null
    liveSnapshot.value = null
  }
  activeDay.value = 0
  mapTo(mapView.value?.center,mapView.value?.zoom)
  persistSoon()
  scrollKey.value += 1
}

/* ======================= 删除 / 撤销 ======================= */
function settleHistoryMutation(ack){
  if(!ack)return null
  const id=ack.id
  if(ack.kind==='delete'){
    if(historyView.value&&currentPlanId.value===id)backToLive()
    if(!historyView.value&&currentPlanId.value===id){
      if(busy.value){currentPlanId.value=null;result.value=null;mapView.value=null;mapFit()}
      else{clearWorkspace();mapView.value=null}
    }
    historyNotice.value=ack.can_restore?'删除已确认，可在剩余期限内撤销。':'删除已确认，撤销期限已结束。'
  }else{historyNotice.value='历史方案已恢复可见，可从列表重新打开。'}
  refreshHistory()
  return ack
}
async function deletePlan(h){
  const activeId=historyView.value&&liveSnapshot.value?liveSnapshot.value.currentPlanId:currentPlanId.value
  if(busy.value&&activeId===h.id){historyDetailError.value='当前对话正在生成，请完成或停止后再删除。';return null}
  return settleHistoryMutation(await historyCtl.mutate(h,'delete'))
}
async function undoDelete(row){
  if(typeof row!=='object'){historyDetailError.value='缺少撤销版本，请先核对服务器状态。';return null}
  return settleHistoryMutation(await historyCtl.mutate(row,'restore'))
}
async function retryHistoryOperation(){return settleHistoryMutation(await historyCtl.retryMutation())}
async function checkHistoryOperation(){return settleHistoryMutation(await historyCtl.checkMutation())}
function endHistoryOperation(){
  if(historyOperation.value?.busy)return
  if(!window.confirm('结束只清除本页核对信息，不会撤销已发出的操作。请先核对服务器状态，确定结束吗？'))return
  historyCtl.endMutation();refreshHistory()
}
async function reloadSnapshot(){
  const e=snapshotState.value
  if(!e||e.saving)return
  if((e.pending||e.queued)&&!window.confirm('重新载入会替换本页聊天和地图，并放弃本页快照草稿；已发送的保存不会被撤销。确定载入吗？'))return
  await loadHistory(e.id,{discard:true})
}
function retrySnapshot(){if(currentPlanId.value)return historyCtl.retry(currentPlanId.value)}
function checkSnapshot(){if(currentPlanId.value)return historyCtl.checkSnapshot(currentPlanId.value)}
function discardSnapshot(){
  if(!snapshotState.value||snapshotState.value.saving)return
  if(window.confirm('放弃只清除本页快照尝试，不会撤销已发送的保存；聊天和地图仍保留显示，重新载入前不会再自动保存。确定放弃吗？'))historyCtl.discardSnapshot(currentPlanId.value)
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
  const meta=historyCtl.records.get(live?live.currentPlanId:currentPlanId.value)
  return {
    v: STORAGE_VERSION,
    ownerId: scopeOwner,     // Explicit scope also during identity transitions.
    savedAt: Date.now(),
    sessionId: sessionId.value,
    messages: packMessages(msgs.map(m=>m===typingTarget&&typingFull?{...m,text:typingFull,streaming:false}:m)),
    result: live ? live.result : result.value,
    collected: live ? live.collected : collected.value,
    currentPlanId: live ? live.currentPlanId : currentPlanId.value,
    planMeta:meta?.detail?{id:meta.id,version:meta.version,revision:meta.revision,status:meta.status,chat:meta.chat||[],map_state:meta.map||{},unconfirmed:Boolean(meta.pending||meta.queued||meta.saving)}:null,
    mapView: mapView.value,
    activeDay: activeDay.value,
    input: input.value,
    jobStartedAt: jobStartedAt.value,   // 刷新后接着算"已用时"，不要从 0 重来
  }
}

function persistNow() {
  clearTimeout(persistTimer)
  persistTimer = null
  const payload = snapshotPayload()
  // 空会话不留垃圾存档
  if (!payload.messages.length && !payload.result) { clearStorage(); return }
  try {
    sessionStorage.setItem(STORAGE_KEY+scopeOwner, JSON.stringify(payload))
  } catch {
    // 容量超了（个别行程 JSON 偏大）：退化成只存对话，至少"回来还能接着聊"
    try {
      sessionStorage.setItem(STORAGE_KEY+scopeOwner, JSON.stringify({ ...payload, result: null }))
    } catch { /* 隐私模式/满盘：存不进就算了，内存里的状态照常可用 */ }
  }
}

function persistSoon() {
  clearTimeout(persistTimer)
  persistTimer = setTimeout(persistNow, 500)
}

function clearStorage() {
  try { sessionStorage.removeItem(STORAGE_KEY+scopeOwner) } catch { /* 隐私模式下不可用 */ }
}

/** 首屏恢复：只在第一次挂载时执行。
 *  校验归属用户 —— 换账号登录后不能把上一个账号的行程显示出来。 */
function hydrate() {
  if (hydrated) return
  hydrated = true
  resultApplied=false
  migrateLegacyStorage()
  try{pendingCreation.value=readPlanCreation(scopeOwner)}catch(e){creationRecoveryError.value=e.message;creationUncertain.value=true}
  let raw = ''
  try { raw = sessionStorage.getItem(STORAGE_KEY+scopeOwner) || '' } catch { return }
  let s = null
  if (raw) {
    try { s = JSON.parse(raw) } catch { s = null }
    if (s && (s.v !== STORAGE_VERSION || s.ownerId !== scopeOwner)) {
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
    if(validPlanMeta(s.planMeta,currentPlanId.value)&&result.value?.itinerary){
      const entry=historyCtl.register({...s.planMeta,itinerary:result.value.itinerary,intent:collected.value})
      if(s.planMeta.unconfirmed)entry.error='刷新前的快照尚未确认，未自动重发；请先核对或重新载入。'
    }
    if (Array.isArray(s.mapView?.center) && s.mapView.center.length === 2 && s.mapView.zoom) {
      mapView.value = { center: s.mapView.center, zoom: s.mapView.zoom }
    }
    if (typeof s.input === 'string') input.value = s.input
    if (Number(s.jobStartedAt) > 0) jobStartedAt.value = Number(s.jobStartedAt)
    activeDay.value = result.value ? (Number(s.activeDay) || 0) : 0
  }

  // 上次的生成任务还挂着？把它接回来 —— 这是 v1.3 的核心：
  // 刷新不再等于"中断"，后台任务一直在跑，这里只是重新开始收事件。
  // 注意：判断依据是"本地还有 job_id"，不是"有没有旧结果"——
  // 上一轮生成完的行程本来就会留在存档里，新一单照样可能正在跑。
  const savedJob = readJobId()
  if(creationRecoveryError.value)return
  if (savedJob) {
    if(pendingCreation.value?.mode==='cancel')stopError.value='上次停止结果尚待确认，正在核对后台进度。'
    jobId = savedJob
    pollSeq = 0
    pollFails = 0
    const tail=messages.value.at(-1)
    jobMsg = tail?.role==='ai' ? tail : pushAiMsg()
    jobMsg.streaming = true
    busy.value = true
    if (!jobStartedAt.value) jobStartedAt.value = Date.now()
    schedulePoll(0)
    return
  }
  if(pendingCreation.value){
    const attempt=pendingCreation.value
    sessionId.value=attempt.payload.session_id;collected.value=attempt.payload.prev_collected
    messages.value=attempt.payload.messages.map(m=>({role:m.role==='ai'?'ai':'user',text:m.text}))
    pushAiMsg();messages.value.at(-1).streaming=false
    messages.value.at(-1).text=attempt.mode==='cancel'?'停止结果待确认，请继续核对本次提交。':'提交结果待确认，请继续同次提交。'
    creationUncertain.value=true;creationStatus.value='本次提交的信息已在本标签保留，继续会使用同一标识和原内容。'
    busy.value=false;return
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
  if (busy.value || jobId || messages.value.length || historyView.value || pendingCreation.value || creationRecoveryError.value) return
  const mark = runSeq   // 记下"发起这次询问时是第几局"
  const uid=user.value?.id
  try {
    const r = await planJob.active()
    const job = r && r.job
    if (!job || job.status !== 'running') return
    // 等接口这段时间里用户可能已经自己发了新需求（runSeq 变了）或界面已经有内容，
    // 这时再把旧任务顶上来就是"抢戏"，必须放弃。
    if (mark !== runSeq || uid!==user.value?.id || busy.value || jobId || messages.value.length) return
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
    jobStartedAt.value = Date.now()
    jobId = job.job_id
    pollSeq = 0
    pollFails = 0
    storeJobId(jobId)
    persistNow()
    schedulePoll(0)
  } catch { /* 游客 / 离线 / 后端没起：正常从空白开始 */ }
}

// This module survives route changes. Identity changes detach the viewer;
// they must never cancel an old account's job using a new account's token.
registerAuthChangeGuard(()=>historyCtl.hasPending.value?window.confirm('历史方案有未确认保存或操作。切换账号会结束本页核对状态，不会撤销已发请求，确定切换吗？'):!(input.value.trim()||busy.value||pendingCreation.value||creationRecoveryError.value)||window.confirm('当前规划还有未发送输入或正在生成的任务。切换账号会保留本标签原账号会话，后台任务可能继续，确定切换吗？'))
watch(()=>user.value?.id??0,uid=>{
  persistNow()
  ++runSeq;clearTimeout(pollTimer);clearTimeout(saveTimer);clearTimeout(persistTimer);stopTyping()
  jobId=null;jobMsg=null;pollSeq=0;pollFails=0;resultApplied=false
  busy.value=false;reconnecting.value=false;jobStartedAt.value=0
  stopping.value=false;stopError.value=''
  pendingCreation.value=null;creationUncertain.value=false;creationRecoveryError.value='';creationStatus.value=''
  historyCtl.reset();historyNotice.value=''
  clearWorkspace();clearTimeout(persistTimer)
  input.value='';history.value=[];showHistory.value=false;stayLoading.value=false;mapView.value=null
  sessionId.value=newSessionId();scopeOwner=uid;hydrated=false
  hydrate();refreshHistory()
},{flush:'sync'})

// 关页面/切后台标签页时立刻落盘（防抖没跑完就走的场景）
if (typeof window !== 'undefined') {
  window.addEventListener('pagehide', () => { clearTimeout(persistTimer); persistNow() })
  window.addEventListener('beforeunload',event=>{if(historyCtl.hasPending.value){event.preventDefault();event.returnValue=''}})
}

export function usePlanSession() {
  return {
    // 状态（都是 ref，页面直接读写；不要在页面里再复制一份，否则又会出现"两份状态"）
    messages, input, busy, stopping, stopError, pendingCreation, creationUncertain, creationRecoveryError, creationStatus, reconnecting, logs, result, collected, activeDay,
    historyView, showHistory, history, historyLoading, historyError, historyDetailLoading, historyDetailError, historyOperation, historyNotice, snapshotState, mapView, mapCommand,
    stayMatches, nearbyHotels, scrollKey, jobStartedAt, stayLoading,
    // 动作
    hydrate, send, resumeCreation, stopCreation, discardCreation, stopGeneration, startFresh, refreshHistory, loadPlan, backToLive,
    deletePlan, undoDelete, setMapView,
    retryHistoryOperation,checkHistoryOperation,endHistoryOperation,reloadSnapshot,retrySnapshot,checkSnapshot,discardSnapshot,
  }
}
