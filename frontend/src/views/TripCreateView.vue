<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../store/auth'
import { guestTrips } from '../store/guestTrips'
import { newCreationAttempt, readCreationAttempt, saveCreationAttempt, clearCreationAttempt } from '../store/tripCreationAttempt'
import { registerAuthChangeGuard } from '../store/authChangeGuard'
import { allowTripDeparture, guardTripUnload } from '../utils/tripDraftGuard'
const { user } = useAuth()
const router = useRouter()
const blank = () => ({ title: '', destination: '', days: 3, start_date: '' })
const form = reactive(blank())
const busy = ref(false), error = ref('')
const pending = ref(null)
const hasDraft = computed(() => Boolean(pending.value || form.title || form.destination || form.start_date || Number(form.days) !== 3))
let generation = 0, leavingAfterCreate = false, unregisterAuthGuard = null
function confirmDeparture() { return leavingAfterCreate || allowTripDeparture(hasDraft.value, busy.value, text => window.confirm(text)) }
function beforeUnload(event) { if (!leavingAfterCreate) guardTripUnload(event, hasDraft.value, busy.value) }
onBeforeRouteLeave(confirmDeparture)
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); unregisterAuthGuard = registerAuthChangeGuard(confirmDeparture) })
onBeforeUnmount(() => { generation++; window.removeEventListener('beforeunload', beforeUnload); unregisterAuthGuard?.() })
watch(() => user.value?.id, id => {
  generation++; busy.value = false; error.value = ''; pending.value = null; leavingAfterCreate = false
  Object.assign(form, blank())
  if (id) {
    try {
      pending.value = readCreationAttempt(id)
      if (pending.value) Object.assign(form, pending.value.payload, {start_date:pending.value.payload.start_date || ''})
    } catch (e) { error.value = e.message }
  }
}, {immediate:true,flush:'sync'})
function abandon() {
  if (busy.value || !pending.value || !window.confirm('这次尝试可能已经创建旅行。放弃只清除当前表单信息，不会删除旅行。请先在“我的旅行”核对，仍要重新填写吗？')) return
  try {
    clearCreationAttempt(user.value.id, pending.value.request_id)
    pending.value = null; error.value = ''; Object.assign(form, blank())
  } catch { error.value = '无法清除这次创建信息，请检查浏览器存储后重试。' }
}
async function submit() {
  if (busy.value) return
  if (!form.title.trim()) { error.value = '请填写旅行名称'; return }
  const run = generation, userId = user.value?.id
  const active = () => run === generation && userId === user.value?.id
  busy.value = true; error.value = ''
  let attempt = null
  try {
    const payload = { ...form, title: form.title.trim(), days: Number(form.days), start_date: form.start_date || null }
    let trip
    if (userId) {
      let capability
      try { capability = await api.get('/api/trips/create-capabilities', {timeout:10000}) }
      catch (e) { if ([404,405,422].includes(e.status)) throw new Error('当前服务器尚不支持可靠创建，请更新服务后重试。'); throw e }
      if (!active()) return
      if (capability?.idempotent_creation !== true) throw new Error('当前服务器尚不支持可靠创建，请更新服务后重试。')
      if (!pending.value) {
        const next = newCreationAttempt(payload)
        saveCreationAttempt(userId, next)
        pending.value = next
      }
      attempt = pending.value
      trip = await api.post('/api/trips/create', {...attempt.payload, request_id:attempt.request_id})
    } else trip = await guestTrips.create(payload)
    if (!active()) return
    leavingAfterCreate = true
    const failed = await router.push(`/trips/${trip.id}`)
    if (!failed && attempt) { try { clearCreationAttempt(userId, attempt.request_id) } catch { /* A retained key safely reopens the same trip. */ } }
    if (failed && active()) { leavingAfterCreate = false; error.value = '旅行已创建，请点击继续打开这次旅行。' }
  } catch (e) {
    if (!active()) return
    leavingAfterCreate = false
    error.value = [404,405].includes(e.status) ? '当前服务器尚不支持可靠创建，请更新服务后继续这次创建。' : e.message
    if (attempt && [410,422].includes(e.status)) {
      try { clearCreationAttempt(userId, attempt.request_id); pending.value = null } catch { /* Keep the original key when storage is unavailable. */ }
    }
  } finally { if (active()) busy.value = false }
}
</script>
<template>
  <main class="create-page"><router-link class="back" to="/">← 返回我的旅行</router-link><div class="head"><p>START A JOURNEY</p><h1>创建一段新旅程</h1><span>先定下大致方向，具体安排可以慢慢添加。</span></div><form class="form" @submit.prevent="submit"><fieldset :disabled="busy"><label>旅行名称 <input v-model="form.title" :readonly="Boolean(pending)" maxlength="100" required placeholder="例如：成都慢游计划"></label><label>目的地 <input v-model="form.destination" :readonly="Boolean(pending)" maxlength="100" placeholder="例如：成都"></label><div class="two"><label>出发日期 <input v-model="form.start_date" :readonly="Boolean(pending)" type="date"><small>还没确定？可以留空。</small></label><label>旅行天数 <input v-model.number="form.days" :readonly="Boolean(pending)" type="number" min="1" max="30" required></label></div></fieldset><p v-if="!user" class="hint">旅行保存在此浏览器，清除浏览器数据会丢失；登录后可主动复制到账号。</p><p v-if="pending" class="hint" role="status">本次创建的信息已在此标签页保留。继续会找回已创建的旅行；尚未创建时完成这次创建。请先确认结果，再修改信息或另建旅行。</p><p v-if="error" class="error" role="alert">{{ error }}</p><button class="submit" :disabled="busy">{{ busy ? '正在创建…' : pending ? '继续这次创建' : '创建旅行 →' }}</button><button v-if="pending" type="button" class="abandon" :disabled="busy" @click="abandon">放弃并重新填写</button><router-link class="ai" to="/plan">想先让 AI 帮忙？去 AI 规划 →</router-link></form></main>
</template>
<style scoped>
.form fieldset{border:0;padding:0;margin:0;min-width:0;display:grid;gap:22px}.form input[readonly]{background:#f6f8f5}.abandon{border:0;background:none;color:#6b8174;font:inherit;font-size:13px;cursor:pointer}
.create-page{width:90%;max-width:700px;margin:35px auto 70px;color:#203b35}.back{color:#688276;font-size:13px}.head{margin:38px 0 25px}.head p{font-size:11px;letter-spacing:.16em;color:#789587;font-weight:bold}.head h1{font-size:30px;margin:10px 0}.head span{color:#78897f}.form{padding:30px;border:1px solid #e2e9e3;border-radius:16px;background:#fff;display:grid;gap:22px}.form label{font-weight:700;display:grid;gap:9px}.form input{width:100%;height:45px;padding:0 13px;border:1px solid #d7e1da;border-radius:8px;font:inherit;color:#203b35;background:white}.form input:focus{outline:2px solid #a5c9b5}.form small{font-weight:normal;color:#8a9990}.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}.hint{color:#6b8174}.error{color:#a33}.submit{border:0;border-radius:9px;background:#1a6e50;color:#fff;font-size:15px;font-weight:700;min-height:48px}.submit:disabled{opacity:.6}.ai{text-align:center;color:#1a6e50;font-size:13px}@media(max-width:500px){.form{padding:22px}.two{grid-template-columns:1fr}}
</style>
