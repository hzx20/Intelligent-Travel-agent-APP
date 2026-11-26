<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import { guestTrips, isGuestTripId } from '../store/guestTrips'
import { useAuth } from '../store/auth'
import TripEditDialog from './TripEditDialog.vue'

const props = defineProps({ detail: { type: Object, required: true } })
const emit = defineEmits(['dirty'])
const { user } = useAuth()
const open = ref(false), loading = ref(false), saving = ref(false), cloud = ref([]), local = ref([])
const selected = ref(''), error = ref(''), success = ref(null)
const pending = ref(null), checked = ref(null), checking = ref(false)
let generation = 0
const active = (run,uid,sid) => run === generation && uid === user.value?.id && sid === props.detail.spot.id
watch([saving,pending,checking],() => emit('dirty',Boolean(saving.value || pending.value || checking.value)),{immediate:true,flush:'sync'})
watch(() => [user.value?.id, props.detail.spot.id], () => { generation++; open.value = false; success.value = null; loading.value = false; saving.value = false; checking.value = false; pending.value = null; checked.value = null; selected.value = ''; cloud.value = []; local.value = []; error.value = '' },{flush:'sync'})
onBeforeUnmount(() => { generation++; emit('dirty',false) })
async function show() {
  if (saving.value || checking.value) return
  if (pending.value) { open.value = true; return }
  const run = ++generation, uid = user.value?.id, sid = props.detail.spot.id
  open.value = true; loading.value = true; error.value = ''; success.value = null; selected.value = ''; cloud.value = []; local.value = []
  try {
    const results = await Promise.allSettled([guestTrips.list(), uid ? api.get('/api/trips') : Promise.resolve({ items: [] })])
    if (!active(run,uid,sid)) return
    const failures = []
    for (const [index,target] of [[0,local],[1,cloud]]) {
      const result = results[index]
      if (result.status === 'fulfilled' && Array.isArray(result.value?.items)) target.value = result.value.items
      else failures.push(result.status === 'rejected' ? result.reason?.message || '加载失败' : '无法读取旅行列表，请重试。')
    }
    if (failures.length) error.value = failures.join('；')
  } finally { if (active(run,uid,sid)) loading.value = false }
}
function close() {
  if (saving.value || checking.value) return
  if (pending.value && !window.confirm('加入结果可能已经保存。请先核对待安排清单；关闭只清除本页重试信息，不会撤销地点，确定关闭吗？')) return
  generation++; open.value = false; loading.value = false; pending.value = null; checked.value = null
}
function validTrip(trip,kind,id) { return String(trip?.id) === String(id) && Number.isSafeInteger(trip.version) && trip.version > 0 && Array.isArray(trip.places) && (kind === 'local' ? isGuestTripId(String(id)) : Number.isSafeInteger(Number(id)) && Number(id) > 0) }
async function readTrip(kind,id) { return kind === 'local' ? guestTrips.get(id) : api.get(`/api/trips/${id}`) }
async function add() {
  if (!open.value || !selected.value || saving.value || checking.value || loading.value) return
  const run = generation, uid = user.value?.id, spot = props.detail.spot, sid = spot.id
  saving.value = true; error.value = ''
  try {
    let attempt = pending.value
    if (!attempt) {
      const [kind,id] = selected.value.split(':')
      const target = (kind === 'local' ? local.value : kind === 'cloud' ? cloud.value : []).find(t => String(t.id) === id)
      if (!target) throw new Error('请重新选择可用旅行。')
      const trip = await readTrip(kind,id)
      if (!active(run,uid,sid)) return
      if (!validTrip(trip,kind,id)) throw new Error('无法确认旅行及版本，请重新读取后再加入。')
      const hasCoordinates = spot.lng != null && spot.lat != null
      const payload = Object.freeze({name:spot.name.trim(),address:props.detail.address || '',lng:hasCoordinates?spot.lng:null,lat:hasCoordinates?spot.lat:null,
        amap_id:spot.amap_id || '',note:'',source:props.detail.source || '景点库',version:trip.version})
      attempt = Object.freeze({kind,id,title:trip.title,payload}); pending.value = attempt; checked.value = null
    }
    const result = attempt.kind === 'local' ? await guestTrips.addPlace(attempt.id,attempt.payload) : await api.post(`/api/trips/${attempt.id}/places`,attempt.payload)
    if (!active(run,uid,sid)) return
    if (!validTrip(result,attempt.kind,attempt.id) || result.version <= attempt.payload.version || !result.places.some(p => p.name === attempt.payload.name && p.amap_id === attempt.payload.amap_id && p.address === attempt.payload.address)) throw new Error('加入结果暂时无法确认，请核对待安排清单后继续原次操作。')
    pending.value = null; checked.value = null; success.value = {id:attempt.id,title:attempt.title}; open.value = false
  } catch (e) { if (active(run,uid,sid)) error.value = e.status === 409 ? `${e.message}。请核对待安排清单；原次重试不会采用新版本另加一条。` : e.message }
  finally { if (active(run,uid,sid)) saving.value = false }
}
async function check() {
  const attempt = pending.value
  if (!attempt || saving.value || checking.value) return
  const run = generation, uid = user.value?.id, sid = props.detail.spot.id
  checking.value = true; checked.value = null
  try {
    const trip = await readTrip(attempt.kind,attempt.id)
    if (!active(run,uid,sid)) return
    if (!validTrip(trip,attempt.kind,attempt.id)) throw new Error('无法读取最新待安排，请稍后重试。')
    checked.value = trip
  } catch (e) { if (active(run,uid,sid)) error.value = e.message }
  finally { if (active(run,uid,sid)) checking.value = false }
}
</script>
<template><div class="collect"><button class="collect-button" :disabled="saving" @click="show">＋ 加入旅行</button><p v-if="success" class="success" role="status">已加入「{{ success.title }}」的待安排。 <router-link :to="{path:`/trips/${success.id}`,query:{panel:'places'}}">查看旅行 →</router-link></p><TripEditDialog v-if="open" label="加入哪段旅行？" :busy="saving || checking" @close="close"><div class="dialog"><div class="heading"><h2>加入哪段旅行？</h2><button type="button" aria-label="关闭" :disabled="saving || checking" @click="close">×</button></div><p>「{{ detail.spot.name }}」将加入待安排地点，之后可以选择日期。</p><p v-if="loading">正在加载旅行…</p><template v-else><label v-if="cloud.length || local.length">选择旅行<select v-model="selected" :disabled="saving || checking || Boolean(pending)"><option value="" disabled>请选择</option><optgroup v-if="cloud.length" label="账号旅行"><option v-for="trip in cloud" :key="trip.id" :value="`cloud:${trip.id}`">{{ trip.title }} · {{ trip.destination || '目的地待定' }}</option></optgroup><optgroup v-if="local.length" label="本机旅行"><option v-for="trip in local" :key="trip.id" :value="`local:${trip.id}`">{{ trip.title }} · {{ trip.destination || '目的地待定' }}</option></optgroup></select></label><p v-else-if="!error">还没有旅行，可以先创建一段。</p><p v-if="error" class="error" role="alert">{{ error }} <button v-if="!pending" type="button" @click="show">重试列表</button></p><div v-if="pending" class="pending"><p>本次目标和地点已固定，继续使用原版本，不会在核对后悄悄另加一条。重试信息仅在本页保留；离开不撤销已发操作。</p><button type="button" :disabled="saving || checking" @click="check">核对待安排清单</button><p v-if="checked" role="status">已读取最新待安排，原次提交版本保持不变。</p><ul v-if="checked"><li v-for="place in checked.places" :key="place.id">{{ place.name }}<small v-if="place.address"> · {{ place.address }}</small></li></ul><router-link :to="{path:`/trips/${pending.id}`,query:{panel:'places'}}">打开旅行核对 →</router-link></div><div class="actions"><router-link v-if="!saving && !checking" to="/trips/new">创建旅行 →</router-link><button v-if="cloud.length || local.length" :disabled="!selected || saving || checking" @click="add">{{ saving ? '正在加入…' : pending ? '继续原次加入' : '加入待安排' }}</button></div></template></div></TripEditDialog></div></template>
<style scoped>.collect{display:inline-flex;align-items:center;gap:10px;flex-wrap:wrap}.collect-button,.actions button,.pending button{min-height:44px;border:0;border-radius:8px;padding:9px 14px;background:#1a6e50;color:#fff;font-weight:700}.success{font-size:12px;color:#1a6e50}.success a{text-decoration:underline}.dialog{background:#fff;border-radius:14px;padding:22px;color:#203b35}.heading{display:flex;justify-content:space-between;align-items:center}.heading h2{font-size:18px}.heading button{min-width:44px;min-height:44px;border:0;background:none;font-size:24px}.dialog p{font-size:13px;color:#73877a;line-height:1.6;margin:12px 0}.dialog label{display:grid;gap:7px;font-size:13px;font-weight:bold}.dialog select{width:100%;min-height:44px;padding:10px;border:1px solid #d7e3d9;border-radius:8px;background:#fff}.dialog .error{color:#a33}.actions{display:flex;justify-content:space-between;align-items:center;margin-top:18px;gap:10px}.actions a,.pending a{font-size:12px;color:#1a6e50}.dialog button:disabled,.collect-button:disabled{opacity:.5}.pending{font-size:12px;line-height:1.7;border:1px solid #d7e3d9;padding:12px;border-radius:8px}.pending ul{max-height:130px;overflow:auto;padding-left:18px}</style>
