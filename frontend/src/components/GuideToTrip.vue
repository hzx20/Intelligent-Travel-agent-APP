<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../store/auth'
import { guestTrips, isGuestTripId } from '../store/guestTrips'
import TripEditDialog from './TripEditDialog.vue'
const props = defineProps({ guideId: { type: Number, required: true }, guideIsDraft: Boolean, disabled: Boolean })
const { user } = useAuth()
const router = useRouter()
const open = ref(false), loading = ref(false), trips = ref([]), selected = ref(''), error = ref('')
let generation = 0
watch(() => [user.value?.id, props.guideId, props.guideIsDraft, props.disabled], () => { generation++; open.value = false; loading.value = false; trips.value = []; selected.value = ''; error.value = '' }, {flush:'sync'})
onBeforeUnmount(() => { generation++ })
async function show() {
  if (props.disabled) return
  const run = ++generation, uid = user.value?.id
  open.value = true; loading.value = true; error.value = ''; trips.value = []; selected.value = ''
  const results = await Promise.allSettled([
    props.guideIsDraft ? Promise.resolve({items:[]}) : guestTrips.list(),
    uid ? api.get('/api/trips', {timeout:10000}) : Promise.resolve({items:[]})
  ])
  if (run !== generation || uid !== user.value?.id) return
  const [local, cloud] = results
  if (local.status === 'fulfilled') trips.value.push(...(local.value.items || []).map(t => ({...t, local:true})))
  if (cloud.status === 'fulfilled') trips.value.push(...(cloud.value.items || []).map(t => ({...t, local:false})))
  if (results.some(result => result.status === 'rejected')) error.value = '部分旅行列表暂时无法读取，可重试。'
  loading.value = false
}
function close() { generation++; open.value = false; loading.value = false }
function proceed() {
  if (props.disabled || loading.value || !open.value) return
  const trip = trips.value.find(t => String(t.id) === selected.value)
  if (!trip || (trip.local ? !isGuestTripId(String(trip.id)) : !Number.isSafeInteger(Number(trip.id)) || Number(trip.id) <= 0)) return
  close()
  router.push({ path: `/trips/${trip.id}`, query: { guide: String(props.guideId) } })
}
</script>
<template><div class="guide-to-trip"><button class="open-button" type="button" :disabled="disabled" @click="show">从这篇攻略收集地点</button><TripEditDialog v-if="open" label="选择一段旅行" @close="close"><div class="dialog"><div class="heading"><h2>选择一段旅行</h2><button type="button" aria-label="关闭" @click="close">×</button></div><p>进入旅行后可预览攻略，再自行识别和确认地点。</p><p v-if="guideIsDraft">草稿攻略仅可导入有权限的账号旅行。</p><p v-if="loading">正在加载旅行…</p><template v-else><p v-if="error" class="error" role="alert">{{ error }} <button type="button" @click="show">重试</button></p><label v-if="trips.length">选择旅行<select v-model="selected"><option value="" disabled>请选择</option><option v-for="trip in trips" :key="`${trip.local ? 'local' : 'cloud'}-${trip.id}`" :value="String(trip.id)">{{ trip.title }} · {{ trip.destination || '目的地待定' }} · {{ trip.local ? '本机' : '账号' }}</option></select></label><p v-else-if="!error">还没有可用的旅行，可以先创建一段。</p><div class="actions"><router-link to="/trips/new">创建旅行 →</router-link><button v-if="trips.length" class="primary" :disabled="!selected" @click="proceed">打开攻略预览</button></div></template></div></TripEditDialog></div></template>
<style scoped>.open-button,.primary{min-height:44px;border:0;border-radius:9px;padding:10px 14px;background:#1a6e50;color:#fff;font-weight:700}.dialog{background:#fff;border-radius:14px;padding:22px;color:#203b35}.heading{display:flex;justify-content:space-between;align-items:center;gap:10px}.heading h2{font-size:18px;margin:0}.heading button{min-width:44px;min-height:44px;border:0;background:none;font-size:24px}.dialog p{font-size:13px;color:#73877a;line-height:1.6;margin:12px 0}.dialog label{display:grid;gap:7px;font-size:13px;font-weight:bold}.dialog select{width:100%;min-height:44px;padding:10px;border:1px solid #d7e3d9;border-radius:8px;background:#fff}.dialog .error{color:#a33}.error button{border:0;background:none;color:#a33;text-decoration:underline}.actions{display:flex;justify-content:space-between;align-items:center;margin-top:18px;gap:10px}.actions a{font-size:12px;color:#1a6e50}.primary:disabled,.open-button:disabled{opacity:.5}</style>
