<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api } from '../api'
import { guestTrips } from '../store/guestTrips'
const props = defineProps({ trip: { type: Object, required: true }, busy: Boolean, local: Boolean })
const emit = defineEmits(['updated', 'dirty'])
const records = ref([]), loading = ref(false), saving = ref(false), editing = ref(null), error = ref(''), loadError = ref('')
const reviewRequired = ref(false), reviewMessage = ref(''), reviewing = ref(false)
const blank = () => ({ kind:'住宿', title:'', status:'待预订', start_date:'', end_date:'', item_id:'', reference:'', note:'' })
const form = reactive(blank())
const hasInput = computed(() => Boolean(form.title.trim() || form.start_date || form.end_date || form.item_id || form.reference.trim() || form.note.trim() || form.kind !== '住宿' || form.status !== '待预订' || editing.value || saving.value))
watch(hasInput, value => emit('dirty', value), { immediate:true, flush:'sync' })
let generation = 0, editVersion = null, createVersion = null, disposed = false
async function load() {
  const run = ++generation, id = props.trip.id
  loading.value = true; loadError.value = ''
  try {
    const result = props.local ? {items: props.trip.reservations || []} : await api.get(`/api/trips/${id}/reservations`, {timeout:10000})
    if (run === generation && props.trip.id === id) { records.value = result.items; return true }
  } catch (e) { if (run === generation) loadError.value = e.message }
  finally { if (run === generation) loading.value = false }
}
watch(() => [props.trip.id, props.trip.version], load, { immediate:true })
onBeforeUnmount(() => { disposed = true; generation++ })
function reset() { Object.assign(form, blank()); editing.value = null; editVersion = null; createVersion = null; error.value = ''; reviewRequired.value = false; reviewMessage.value = '' }
async function review() {
  if (reviewing.value || saving.value) return
  const id = props.trip.id
  reviewing.value = true; reviewMessage.value = ''
  try {
    if (!props.local) {
      const latest = await api.get(`/api/trips/${id}`, {timeout:10000})
      if (disposed || props.trip.id !== id) return
      emit('updated', latest)
    }
    await nextTick()
    const loaded = await load()
    if (loaded && !disposed && props.trip.id === id) reviewMessage.value = '已重新读取保存的记录，表单仍保留。若记录已存在，请清空表单；若没有，请核对后再重试。'
  } catch (e) { if (!disposed && props.trip.id === id) loadError.value = e.message }
  finally { if (!disposed) reviewing.value = false }
}
function edit(record) {
  if (saving.value || props.busy || (hasInput.value && !window.confirm('编辑此记录将替换表单中未保存的内容，继续吗？'))) return
  editing.value = record.id; editVersion = props.trip.version; error.value = ''
  createVersion = null; reviewRequired.value = false; reviewMessage.value = ''
  Object.assign(form, {kind:record.kind, title:record.title, status:record.status,
    start_date:record.start_date || '', end_date:record.end_date || '', item_id:record.item_id || '',
    reference:record.reference, note:record.note})
}
async function save() {
  if (saving.value || props.busy || !form.title.trim()) return
  if (form.start_date && form.end_date && form.end_date < form.start_date) { error.value = '结束日期不能早于开始日期'; return }
  saving.value = true; error.value = ''
  const id = props.trip.id, reservationId = editing.value
  if (!reservationId && createVersion === null) createVersion = props.trip.version
  const body = {...form, title:form.title.trim(), item_id:form.item_id ? Number(form.item_id) : null,
    start_date:form.start_date || null, end_date:form.end_date || null,
    version:reservationId ? editVersion : createVersion}
  try {
    const updated = props.local
      ? (reservationId ? await guestTrips.updateReservation(id, reservationId, body) : await guestTrips.addReservation(id, body))
      : (reservationId ? await api.patch(`/api/trips/${id}/reservations/${reservationId}`, body) : await api.post(`/api/trips/${id}/reservations`, body))
    if (!disposed && props.trip.id === id) { reset(); emit('updated', updated) }
  } catch (e) { if (!disposed && props.trip.id === id) { reviewRequired.value ||= Boolean(e.outcomeUnknown); error.value = e.status === 409 ? `${e.message}。请保留输入并核对已保存记录，避免重复新增；重新填写前请先清空这份表单。` : e.message } }
  finally { if (!disposed) saving.value = false }
}
async function remove(record) {
  if (saving.value || props.busy || !window.confirm(`删除“${record.title}”的手动记录？这不会取消实际预订。`)) return
  saving.value = true; error.value = ''
  const id = props.trip.id
  try {
    const updated = props.local ? await guestTrips.deleteReservation(id, record.id, {version:props.trip.version})
      : await api.post(`/api/trips/${id}/reservations/${record.id}/delete`, {version:props.trip.version})
    if (!disposed && props.trip.id === id) { if (editing.value === record.id) reset(); emit('updated', updated) }
  } catch (e) { if (!disposed && props.trip.id === id) error.value = e.status === 409 ? `${e.message}。请刷新后重试。` : e.message }
  finally { if (!disposed) saving.value = false }
}
</script>
<template><section class="reservations"><p class="eyebrow">TRIP RESERVATIONS</p><h2>预订记录</h2><p class="intro">手动记录住宿、交通或门票信息，不会创建或取消实际预订。{{ local ? "本机记录会在复制旅行时一并复制到账号。" : "同行编辑者可查看记录和订单号。" }}</p><p v-if="loading" class="intro">正在加载记录…</p><p v-else-if="loadError" class="error" role="alert">记录加载失败：{{ loadError }} <button type="button" class="plain" @click="load">重试</button></p><p v-else-if="!records.length" class="intro">还没有预订记录。</p><ul v-else><li v-for="record in records" :key="record.id"><div class="record"><b>{{ record.title }}</b><small>{{ record.kind }} · {{ record.status }} · {{ record.start_date || '开始日期未填' }}<template v-if="record.end_date"> 至 {{ record.end_date }}</template></small><small v-if="record.item_name">{{ record.item_id ? '关联安排' : '原关联安排已删除' }}：{{ record.item_name }}</small><small v-if="record.reference">订单号：{{ record.reference }}</small><small v-if="record.note">{{ record.note }}</small></div><div class="record-actions"><button :disabled="saving || busy" @click="edit(record)">编辑</button><button :disabled="saving || busy" @click="remove(record)">删除</button></div></li></ul><form @submit.prevent="save"><fieldset :disabled="saving || busy"><div class="fields"><label>类型<select v-model="form.kind"><option>住宿</option><option>交通</option><option>门票</option><option>其他</option></select></label><label>状态<select v-model="form.status"><option>待预订</option><option>已预订</option><option>已取消</option></select></label><label class="wide">名称<input v-model="form.title" maxlength="160" required placeholder="例如：酒店或车次"></label><label>开始日期<input v-model="form.start_date" type="date"></label><label>结束日期<input v-model="form.end_date" type="date"></label><label class="wide">关联行程安排<select v-model="form.item_id"><option value="">不关联</option><optgroup v-for="day in trip.days" :key="day.id" :label="`第 ${day.day_number} 天`"><option v-for="item in day.items" :key="item.id" :value="item.id">{{ item.name }}</option></optgroup></select></label><label class="wide">订单号（可留空）<input v-model="form.reference" maxlength="150" autocomplete="off"></label><label class="wide">备注<textarea v-model="form.note" maxlength="2000" rows="2"></textarea></label></div></fieldset><p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="reviewMessage" role="status">{{ reviewMessage }}</p><button v-if="reviewRequired" type="button" class="plain" :disabled="loading || saving || reviewing || busy" @click="review">核对已保存记录</button><div class="actions"><button class="primary" :disabled="saving || busy">{{ saving ? '保存中…' : editing ? '保存修改' : '保存记录' }}</button><button v-if="hasInput && !saving" type="button" class="plain" @click="reset">清空表单</button></div></form></section></template>
<style scoped>.reservations{padding:24px;border-top:1px solid #e8eee9}.eyebrow{font-size:10px;letter-spacing:.14em;color:#789487;font-weight:700}.reservations h2{font-size:19px;margin:6px 0}.intro{font-size:12px;color:#819389;line-height:1.6;margin:7px 0 12px}.reservations ul{list-style:none}.reservations li{display:flex;justify-content:space-between;gap:12px;border-top:1px solid #edf1ed;padding:11px 0;font-size:12px}.record{min-width:0;overflow-wrap:anywhere}.record small{display:block;color:#71867a;line-height:1.6;margin-top:4px;white-space:pre-wrap}.record-actions{display:flex;align-items:start;gap:8px;white-space:nowrap}.record-actions button,.plain{border:0;background:none;color:#1a6e50;font-size:12px}.reservations form{background:#f6f9f6;border-radius:10px;padding:14px;margin-top:14px}.reservations fieldset{border:0;padding:0;margin:0;min-width:0}.fields{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.fields label{display:grid;gap:5px;font-size:12px;font-weight:bold;color:#526d5e}.fields .wide{grid-column:1/-1}.fields input,.fields select,.fields textarea{min-width:0;width:100%;border:1px solid #dbe5de;border-radius:7px;padding:8px;background:white;color:#31483a;font:inherit;font-weight:normal}.fields textarea{resize:vertical}.actions{display:flex;align-items:center;gap:10px;margin-top:12px}.primary{border:0;border-radius:7px;background:#1a6e50;color:white;font-weight:700;padding:9px 15px}.error{color:#a33;margin-top:8px;font-size:12px}button:disabled{opacity:.5}@media(max-width:760px){.reservations{padding:20px 16px}}</style>
