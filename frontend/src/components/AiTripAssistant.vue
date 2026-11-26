<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import { useAuth } from '../store/auth'
import { guestTrips } from '../store/guestTrips'
const props = defineProps({ trip: { type: Object, required: true }, busy: Boolean, local: Boolean })
const emit = defineEmits(['updated', 'dirty'])
const { user } = useAuth()
const request = ref(''), proposal = ref(null), loading = ref(false), applying = ref(false), error = ref(''), message = ref('')
let generation = 0, timer = null
const labels = { name: '名称', kind: '类型', start_time: '开始时间', duration_min: '停留分钟', note: '备注', activity: '活动说明', address: '地址', lng: '经度', lat: '纬度', source: '来源' }
function value(v) { return v === null || v === '' || v === undefined ? '未填写' : String(v) }
function marker(id) { return `trip-ai-job:${user.value?.id}:${id}` }
function setMarker(id, pending) { try { pending ? sessionStorage.setItem(marker(id), '1') : sessionStorage.removeItem(marker(id)) } catch { /* Storage may be unavailable. */ } }
function hasMarker(id) { try { return sessionStorage.getItem(marker(id)) === '1' } catch { return false } }
function stopPolling() { if (timer) clearTimeout(timer); timer = null }
function valid(run, id, version) { return run === generation && props.trip.id === id && props.trip.version === version }
async function checkJob(run, id, version, waitingForStale = false) {
  if (!valid(run, id, version)) return
  try {
    const { job } = await api.get(`/api/trips/${id}/ai-jobs/latest`, { timeout: 10000 })
    if (!valid(run, id, version)) return
    if (!job) { setMarker(id, false); loading.value = false; error.value = waitingForStale ? '旧版本的生成任务已结束，请根据当前行程重新生成建议。' : '未找到生成任务，请重新生成。'; message.value = ''; return }
    if (job.status === 'stale_running') {
      message.value = '行程已更新，旧版本建议正在后台结束；完成后可重新生成。'
      timer = setTimeout(() => checkJob(run, id, version, true), 2500)
    } else if (job.status === 'running') {
      message.value = '建议正在后台生成，离开或刷新页面后可继续查看。'
      timer = setTimeout(() => checkJob(run, id, version), 2500)
    } else {
      setMarker(id, false); loading.value = false
      if (job.status === 'done') { proposal.value = job.proposal; message.value = '建议已生成，请核对后再决定是否应用。' }
      else { error.value = job.error || '生成失败，请重试。'; message.value = '' }
    }
  } catch (e) {
    if (!valid(run, id, version)) return
    if (e.status === 401 || e.status === 404) { loading.value = false; error.value = e.message; return }
    message.value = '暂时无法获取进度，正在重试；后台任务可能仍在运行。'
      timer = setTimeout(() => checkJob(run, id, version, waitingForStale), 5000)
  }
}
async function recover(id) {
  const run = ++generation, version = props.trip.version
  stopPolling()
  try {
    const { job } = await api.get(`/api/trips/${id}/ai-jobs/latest`, { timeout: 10000 })
    if (!valid(run, id, version) || request.value.trim() || proposal.value || loading.value) return
    if (job?.status === 'stale_running') {
      loading.value = true; message.value = '行程已更新，旧版本建议正在后台结束；完成后可重新生成。'
      checkJob(run, id, version, true)
      return
    }
    if (job?.status === 'running') {
      request.value = job.request; loading.value = true
      message.value = '已接回后台生成任务。'
      checkJob(run, id, version)
      return
    }
    if (job) setMarker(id, false)
    if (job?.status === 'error') error.value = job.error
    if (!job && hasMarker(id)) { setMarker(id, false); error.value = '未找到上次的生成任务，服务可能已重启，请重新生成。' }
    const result = await api.get(`/api/trips/${id}/ai-proposals/latest`, { timeout: 10000 })
    if (!valid(run, id, version) || request.value.trim() || proposal.value || loading.value) return
    if (result.proposal) {
      proposal.value = result.proposal
      message.value = '已找回这份旅行最近一次未应用的建议，请核对后再决定是否应用。同行编辑者也可看到这份预览。'
    }
  } catch { /* Recovery failure does not prevent starting a new job. */ }
}
watch(() => [props.trip.id, props.trip.version], ([id, version], [oldId]) => {
  const wasLoading = loading.value
  generation++; stopPolling(); loading.value = false
  if (id !== oldId) { proposal.value = null; request.value = ''; error.value = ''; message.value = '' }
  else if (wasLoading && !props.local) {
    loading.value = true; error.value = ''; message.value = '行程已更新，正在确认旧版本任务状态。'
    checkJob(generation, id, version, true)
  } else if (wasLoading) { message.value = ''; error.value = '行程已更新，原建议无法使用，请重新生成。' }
})
watch(() => props.trip.id, id => { if (!props.local) recover(id) }, { immediate: true })
watch([request, proposal, loading, applying], () => emit('dirty', Boolean(request.value.trim() || proposal.value || loading.value || applying.value)), { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { generation++; stopPolling() })
async function generate() {
  if (!request.value.trim() || loading.value || applying.value || props.busy) return
  const run = ++generation, id = props.trip.id, version = props.trip.version
  stopPolling(); loading.value = true; error.value = ''; message.value = ''; proposal.value = null
  try {
    if (props.local) {
      const itemKeys = ['id','name','kind','start_time','duration_min','address','lng','lat','amap_id','note','activity','source']
      const trip = {version, title:props.trip.title, destination:props.trip.destination, start_date:props.trip.start_date,
        days:props.trip.days.map(day => ({id:day.id, day_number:day.day_number, theme:day.theme,
          items:day.items.map(item => Object.fromEntries(itemKeys.map(key => [key,item[key]]))) }))}
      const result = await api.post('/api/trips/guest-ai-preview', {trip, request:request.value.trim()}, {timeout:130000})
      if (valid(run, id, version)) { proposal.value = result; loading.value = false; message.value = '建议已生成，请核对后决定是否应用；刷新或离开后无法找回此预览。' }
    } else {
      await api.post(`/api/trips/${id}/ai-jobs`, { version, request: request.value.trim() }, { timeout: 10000 })
      if (valid(run, id, version)) { setMarker(id, true); checkJob(run, id, version) }
    }
  } catch (e) {
    if (valid(run, id, version)) { loading.value = false; error.value = e.status ? e.message : (props.local ? '建议生成失败或超时，请稍后重试。' : '暂时无法确认任务是否已提交，可用相同需求重试或重新打开行程查看。') }
  }
}
async function apply() {
  if (!proposal.value || proposal.value.base_version !== props.trip.version || applying.value || props.busy) return
  applying.value = true; error.value = ''
  const run = generation, id = props.trip.id
  try {
    const updated = props.local ? await guestTrips.applyAi(id, {version:proposal.value.base_version, operations:proposal.value.operations})
      : await api.post(`/api/trips/${id}/ai-proposals/${proposal.value.id}/apply`, { version: proposal.value.base_version })
    if (run === generation && props.trip.id === id) { proposal.value = null; message.value = '建议已应用，可以继续手动调整。'; emit('updated', updated) }
  } catch (e) { if (run === generation) error.value = e.message }
  finally { applying.value = false }
}
function closePreview() {
  proposal.value = null
  message.value = props.local ? '预览已关闭，可以重新生成。' : '预览已关闭；重新打开行程时，当前版本的建议仍可找回。'
}
</script>
<template>
  <section class="assistant"><p class="eyebrow">YOUR TRAVEL ASSISTANT</p><h2>让 AI 帮你调整行程</h2><p class="intro">从空白行程开始规划，或告诉助手想如何调整已有安排。{{ local ? "本机建议仅在当前页面保留，刷新或离开后需要重新生成。" : "" }}</p><form @submit.prevent="generate"><textarea v-model="request" :disabled="loading || applying || busy" maxlength="500" rows="3" placeholder="例如：为这次旅行安排轻松的两天；或把第二天排得更从容" aria-label="告诉 AI 你的规划需求" required></textarea><div class="form-bottom"><small>{{ local ? "旅行名称、目的地、出发日期、各天安排的地点、坐标、时间、备注及规划需求会发送给 AI 服务；预订记录不会发送。可能产生服务费用。" : "行程内容和你的要求会发送给已配置的 AI 服务。" }}</small><button :disabled="loading || applying || busy">{{ loading ? '正在生成建议…' : '生成修改建议' }}</button></div></form><p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="message" class="success" role="status">{{ message }}</p>
    <div v-if="proposal" class="proposal"><p v-if="proposal.base_version !== trip.version" class="error">行程已更新，此预览已过期，请重新生成。</p><div class="proposal-title"><h3>修改预览</h3><span>尚未应用</span></div><p v-if="!proposal.changes.length" class="intro">AI 暂时没有提出可应用的修改，可以补充更具体的要求后重试。</p><template v-else><p class="intro">请核对以下全部修改，确认后才会写入行程。新增地点尚未核实。</p><ol><li v-for="(change, index) in proposal.changes" :key="index"><template v-if="change.action === 'add'"><b>新增到第 {{ change.day }} 天 · 第 {{ change.position }} 项</b><dl><template v-for="(v, k) in change.item" :key="k"><dt>{{ labels[k] || k }}</dt><dd>{{ value(v) }}</dd></template></dl></template><template v-else-if="change.action === 'remove'"><b>删除第 {{ change.day }} 天第 {{ change.position }} 项：{{ change.item.name }}</b><dl><template v-for="(v, k) in change.item" :key="k"><template v-if="k !== 'name'"><dt>{{ labels[k] || k }}</dt><dd>{{ value(v) }}</dd></template></template></dl></template><template v-else-if="change.action === 'move'"><b>移动：{{ change.name }}</b><p>第 {{ change.from_day }} 天第 {{ change.from_position }} 项 → 第 {{ change.day }} 天第 {{ change.position }} 项</p></template><template v-else><b>修改第 {{ change.day }} 天：{{ change.name }}</b><dl><template v-for="(v, k) in change.after" :key="k"><dt>{{ labels[k] || k }}</dt><dd>{{ value(change.before[k]) }} → {{ value(v) }}</dd></template></dl></template></li></ol><button class="apply" :disabled="loading || applying || busy || proposal.base_version !== trip.version" @click="apply">{{ applying ? '正在应用…' : '确认应用这些修改' }}</button></template><button class="close-preview" :disabled="applying" @click="closePreview">关闭预览</button></div>
  </section>
</template>
<style scoped>.assistant{padding:24px;border-top:1px solid #e8eee9}.eyebrow{font-size:10px;color:#789487;letter-spacing:.14em;font-weight:bold}.assistant h2{font-size:19px;margin:6px 0}.intro{font-size:12px;line-height:1.6;color:#788c80;margin:8px 0 12px}.assistant textarea{width:100%;resize:vertical;min-height:76px;border:1px solid #dbe5de;border-radius:8px;padding:11px;font:inherit;line-height:1.5}.form-bottom{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-top:9px}.form-bottom small{color:#8b9c91;line-height:1.5}.form-bottom button,.apply{border:0;border-radius:8px;background:#1a6e50;color:#fff;padding:10px 14px;font-weight:bold;white-space:nowrap}.form-bottom button:disabled,.apply:disabled{opacity:.5}.proposal{background:#f5f8f4;border-radius:10px;padding:15px;margin-top:18px}.proposal-title{display:flex;justify-content:space-between;align-items:center}.proposal-title h3{font-size:15px}.proposal-title span{font-size:11px;color:#8c9e92}.proposal ol{padding-left:20px}.proposal li{padding:9px 0;border-top:1px solid #e3ebe4;font-size:12px;line-height:1.6}.proposal li p{margin-top:5px;color:#617a69}.proposal dl{display:grid;grid-template-columns:70px 1fr;gap:4px;margin-top:6px}.proposal dt{color:#8a9b8e}.proposal dd{white-space:pre-wrap;overflow-wrap:anywhere}.apply{margin-top:12px}.close-preview{margin:12px 0 0 10px;border:0;background:none;color:#547365;padding:10px}.error,.success{font-size:12px;margin-top:10px;line-height:1.6}.error{color:#a33}.success{color:#1a6e50}@media(max-width:760px){.assistant{padding:20px 16px}.form-bottom{align-items:start;flex-direction:column}}
</style>
