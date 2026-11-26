<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'

const props = defineProps({ tripId: { type: Number, required: true } })
const emit = defineEmits(['trip-changed', 'dirty'])
const allowed = ref(false), links = ref([]), members = ref([]), secrets = ref([])
const working = ref(false), error = ref(''), message = ref('')
const pending = ref(null)
let generation = 0, mutationEpoch = 0, syncTimer = null, syncing = false
const active = (run, id) => run === generation && id === props.tripId
watch([() => secrets.value.length, working, () => Boolean(pending.value)], ([count, busy, retry]) => emit('dirty', Boolean(count || busy || retry)), { immediate: true, flush: 'sync' })

async function load() {
  const run = ++generation
  allowed.value = false; secrets.value = []; links.value = []; members.value = []; error.value = ''; message.value = ''; working.value = false
  pending.value = null
  try {
    const data = await api.get(`/api/trips/${props.tripId}/sharing`, { timeout: 10000 })
    if (run !== generation) return
    links.value = data.links; members.value = data.members; allowed.value = true
  } catch (e) {
    if (run !== generation) return
    // Collaborators are deliberately not allowed to manage sharing.
    if (e.status !== 404) { allowed.value = true; error.value = e.message }
  }
}
watch(() => props.tripId, load, { immediate: true })

async function syncSharing() {
  if (!allowed.value || working.value || syncing || document.hidden) return
  const run = generation, epoch = mutationEpoch, id = props.tripId
  syncing = true
  try {
    const data = await api.get(`/api/trips/${id}/sharing`, { timeout: 10000 })
    if (run !== generation || epoch !== mutationEpoch || props.tripId !== id || working.value || !allowed.value) return
    links.value = data.links; members.value = data.members
  } catch { /* Keep the existing list; explicit actions still report errors. */ }
  finally { syncing = false }
}
function onVisibilityChange() { if (!document.hidden) syncSharing() }
onMounted(() => { syncTimer = window.setInterval(syncSharing, 8000); document.addEventListener('visibilitychange', onVisibilityChange) })
onBeforeUnmount(() => { generation++; if (syncTimer) window.clearInterval(syncTimer); document.removeEventListener('visibilitychange', onVisibilityChange) })

async function refresh(id, run) {
  const data = await api.get(`/api/trips/${id}/sharing`, { timeout: 10000 })
  if (active(run, id)) { links.value = data.links; members.value = data.members }
}
async function create(mode) {
  if (working.value) return
  if (pending.value && pending.value.mode !== mode) { error.value = '请先继续或放弃上次链接生成，再创建其他链接。'; return }
  const run = generation, id = props.tripId
  mutationEpoch++
  working.value = true; error.value = ''; message.value = ''
  let activating = Boolean(pending.value)
  try {
    let attempt = pending.value
    if (!attempt) {
      const prepared = await api.post(`/api/trips/${id}/sharing/links/prepare`, { mode, expires_days: 30 }, {timeout: 15000})
      if (!active(run, id)) return
      if (typeof prepared?.preparation !== 'string' || !prepared.preparation || prepared.mode !== mode || !/^[A-Za-z0-9_-]{43}$/.test(prepared.token || '')) throw new Error('无法准备链接')
      attempt = {mode, preparation:prepared.preparation, token:prepared.token}
      pending.value = attempt
    }
    activating = true
    const data = await api.post(`/api/trips/${id}/sharing/links/activate`, {preparation:attempt.preparation}, {timeout:15000})
    if (!active(run, id)) return
    if (!Number.isSafeInteger(data?.id) || data.id <= 0 || data.mode !== attempt.mode || data.token !== attempt.token || typeof data.expires_at !== 'string' || !Number.isFinite(Date.parse(data.expires_at))) {
      throw new Error('链接生成结果暂时无法确认，请继续本次生成以找回同一条链接。')
    }
    pending.value = null
    const page = data.mode === 'view' ? 'share' : 'invite'
    const url = new URL(`${import.meta.env.BASE_URL}${page}#${data.token}`, window.location.origin).href
    if (!secrets.value.some(secret => secret.id === data.id)) secrets.value.push({ ...data, url })
    try { await refresh(id, run) } catch { if (active(run, id)) error.value = '链接已创建，但列表刷新失败。请先复制链接。' }
  } catch (e) {
    if (active(run, id)) error.value = activating ? ([404,405].includes(e.status) ? '当前服务无法继续可靠生成链接，请核对权限或更新服务后重试。' : e.message) : e.code?.startsWith('SESSION_') ? e.message
      : [404,405].includes(e.status) ? '无法准备可靠链接，请刷新确认旅行权限或更新服务。' : '暂时无法准备分享链接，请检查网络后重试。'
  }
  finally { if (active(run, id)) working.value = false }
}
function discardPending() {
  if (working.value || !pending.value || !window.confirm('链接可能已生成。放弃只清除本页重试信息，不会撤销链接；请先核对管理列表，仍要放弃吗？')) return
  pending.value = null; error.value = ''; message.value = '已清除本页重试信息，请在管理列表核对已生成的链接。'
  syncSharing()
}
async function copy(secret) {
  const run = generation, id = props.tripId, url = secret?.url
  if (!url) return
  try {
    if (!navigator.clipboard) throw new Error('clipboard unavailable')
    await navigator.clipboard.writeText(url)
    if (active(run, id)) message.value = '链接已复制。'
  } catch { if (active(run, id)) message.value = '请手动选择并复制链接。' }
}
async function revoke(link) {
  if (!window.confirm('撤销后，这个链接将无法再打开或用于加入。已加入的成员需单独移除。')) return
  const run = generation, id = props.tripId
  mutationEpoch++
  working.value = true; error.value = ''; message.value = ''
  try {
    await api.del(`/api/trips/${id}/sharing/links/${link.id}`)
    if (!active(run, id)) return
    secrets.value = secrets.value.filter(secret => secret.id !== link.id)
    await refresh(id, run); if (active(run, id)) message.value = '链接已撤销。'
  } catch (e) { if (active(run, id)) error.value = e.message }
  finally { if (active(run, id)) working.value = false }
}
async function remove(member) {
  if (!window.confirm(`移除 ${member.nickname || '该成员'} 的编辑权限？仍有效的邀请链接可再次加入。`)) return
  const run = generation, id = props.tripId
  let removed = false
  mutationEpoch++
  working.value = true; error.value = ''; message.value = ''
  try {
    await api.del(`/api/trips/${id}/sharing/members/${member.user_id}`)
    if (!active(run, id)) return
    removed = true
    await refresh(id, run); if (active(run, id)) message.value = '成员已移除。'
  } catch (e) { if (active(run, id)) error.value = e.message }
  finally { if (active(run, id)) { working.value = false; if (removed) emit('trip-changed') } }
}
function date(value) { return new Date(value).toLocaleString() }
const labels = { active: '有效', revoked: '已撤销', expired: '已过期' }
function secretInactive(secret) { return ['revoked', 'expired'].includes(links.value.find(link => link.id === secret.id)?.status) }
function hideSecret(id) { secrets.value = secrets.value.filter(secret => secret.id !== id) }
</script>

<template>
  <section v-if="allowed" class="sharing">
    <p class="eyebrow">TRAVEL TOGETHER</p><h2>分享与同行</h2>
    <p class="description">只读链接展示每天的安排、地址和备注，不展示预订记录；编辑邀请允许加入者查看和修改行程、待安排地点、旅行账本及预订记录（包括订单号）。请只分享给信任的人。</p>
    <div class="actions"><button :disabled="working || Boolean(pending)" @click="create('view')">生成只读链接</button><button :disabled="working || Boolean(pending)" @click="create('edit')">生成编辑邀请</button></div>
    <div v-if="pending" class="pending-link"><p role="status">上次{{ pending.mode === 'view' ? '只读链接' : '编辑邀请' }}的生成结果尚未确认。继续会完成或找回同一条链接，不会另建。重试信息仅在本页保留；刷新或离开后请到管理列表核对。</p><button type="button" :disabled="working" @click="pending && create(pending.mode)">继续生成这条链接</button><button type="button" :disabled="working" @click="discardPending">放弃本次重试</button></div>
    <div v-for="secret in secrets" :key="secret.id" class="secret"><b>{{ secret.mode === 'view' ? '只读链接' : '编辑邀请链接' }}</b><p>有效期至 {{ date(secret.expires_at) }}。地址仅在本页面保留，刷新、离开或收起后无法找回；收起不会撤销链接。</p><p v-if="secretInactive(secret)" class="error" role="status">此链接已撤销或过期，请重新生成。</p><label>分享地址<input :value="secret.url" readonly @focus="$event.target.select()"></label><button :disabled="secretInactive(secret)" @click="copy(secret)">复制链接</button> <button type="button" @click="hideSecret(secret.id)">收起地址</button></div>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="message" class="message" role="status">{{ message }}</p>
    <details v-if="links.length"><summary>管理链接（{{ links.length }}）</summary><div v-for="link in links" :key="link.id" class="row"><span>{{ link.mode === 'view' ? '只读' : '编辑邀请' }} · {{ labels[link.status] }}<small>到期 {{ date(link.expires_at) }}</small></span><button v-if="link.status === 'active'" :disabled="working" @click="revoke(link)">撤销</button></div><p class="hint">撤销邀请链接不会自动移除已加入的成员。</p></details>
    <details v-if="members.length"><summary>同行成员（{{ members.length }}）</summary><div v-for="member in members" :key="member.user_id" class="row"><span>{{ member.nickname || '同行成员' }}<small>可编辑 · 加入于 {{ date(member.joined_at) }}</small></span><button :disabled="working" @click="remove(member)">移除</button></div><p class="hint">移除成员后，其已打开的旧页面无法再提交修改；有效邀请链接可再次加入。</p></details>
  </section>
</template>

<style scoped>
.pending-link{padding:14px;margin-top:14px;border:1px solid #dfd6bd;border-radius:10px;background:#fff9ec;font-size:12px;color:#796536;line-height:1.6}.pending-link button{border:0;border-radius:7px;background:#e7f2ea;color:#1a6e50;padding:8px 10px;margin:8px 8px 0 0;font:inherit;cursor:pointer}.pending-link button:disabled{opacity:.5}
.sharing{padding:24px;border-top:1px solid #e8eee9}.eyebrow{font-size:10px;color:#789487;letter-spacing:.14em;font-weight:bold}.sharing h2{font-size:19px;margin:6px 0}.description,.hint,.secret p{font-size:12px;color:#788c80;line-height:1.6;margin:8px 0 12px}.actions{display:flex;gap:8px;flex-wrap:wrap}.actions button,.secret button{border:0;border-radius:8px;background:#e7f2ea;color:#1a6e50;padding:10px 13px;font-weight:bold}.actions button:disabled,.row button:disabled{opacity:.5}.secret{margin-top:15px;padding:14px;border:1px solid #cfe2d4;border-radius:10px;background:#f7faf6}.secret label{display:grid;gap:6px;font-size:12px;color:#60796a}.secret input{min-width:0;width:100%;border:1px solid #dce7de;border-radius:7px;padding:10px;font:inherit}.secret button{margin-top:9px}.sharing details{border-top:1px solid #edf1ed;margin-top:15px;padding-top:12px}.sharing summary{cursor:pointer;color:#315f46;font-weight:700;font-size:13px}.row{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:10px 0;border-bottom:1px solid #edf1ed;font-size:12px}.row small{display:block;color:#87998d;margin-top:4px}.row button{border:0;background:none;color:#a04c3e}.error,.message{font-size:12px;margin-top:10px;line-height:1.6}.error{color:#a33}.message{color:#1a6e50}@media(max-width:760px){.sharing{padding:20px 16px}}
</style>
