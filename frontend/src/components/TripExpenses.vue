<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api } from '../api'
const props = defineProps({ trip: { type: Object, required: true }, currentDayId: { type: Number, default: null }, busy: Boolean })
const emit = defineEmits(['updated', 'dirty'])
const items = ref([]), totals = ref({}), loading = ref(false), saving = ref(false), error = ref(''), editing = ref(null)
const loadError = ref(''), formTouched = ref(false)
const reviewRequired = ref(false), reviewMessage = ref(''), reviewing = ref(false)
let editVersion = null, createVersion = null, disposed = false
const form = reactive({ amount: '', currency: 'CNY', category: '其他', day_id: '', item_id: '', spent_on: '', note: '' })
const selectedDay = computed(() => props.trip.days.find(day => day.id === Number(form.day_id)))
watch(() => [formTouched.value, editing.value, saving.value], () => emit('dirty', Boolean(formTouched.value || editing.value || saving.value)), { immediate: true, flush: 'sync' })
function dateForDay(id) {
  const day = props.trip.days.find(d => d.id === Number(id))
  if (!props.trip.start_date || !day) return ''
  const date = new Date(`${props.trip.start_date}T12:00:00`)
  date.setDate(date.getDate() + day.day_number - 1)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
function reset() {
  editing.value = null
  editVersion = null; createVersion = null; formTouched.value = false
  reviewRequired.value = false; reviewMessage.value = ''; error.value = ''
  Object.assign(form, { amount: '', currency: 'CNY', category: '其他', day_id: props.currentDayId || '', item_id: '', spent_on: dateForDay(props.currentDayId), note: '' })
}
function minorToText(amount, currency) {
  if (currency === 'JPY') return String(amount)
  return `${Math.floor(amount / 100)}.${String(amount % 100).padStart(2, '0')}`
}
function toMinor(input, currency) {
  const pattern = currency === 'JPY' ? /^\d+$/ : /^\d+(?:\.\d{1,2})?$/
  if (!pattern.test(input.trim())) throw new Error(currency === 'JPY' ? '日元金额请填写正整数' : '金额最多保留两位小数')
  const [whole, fraction = ''] = input.trim().split('.')
  const n = Number(whole) * (currency === 'JPY' ? 1 : 100) + (currency === 'JPY' ? 0 : Number(fraction.padEnd(2, '0')))
  if (!Number.isSafeInteger(n) || n <= 0 || n > 100000000000) throw new Error('请输入有效的正金额')
  return n
}
let sequence = 0
async function load() {
  const run = ++sequence
  loading.value = true; loadError.value = ''
  try {
    const result = await api.get(`/api/trips/${props.trip.id}/expenses`, {timeout:10000})
    if (run === sequence) { items.value = result.items; totals.value = result.totals; return true }
  } catch (e) { if (run === sequence) loadError.value = e.message }
  finally { if (run === sequence) loading.value = false }
}
watch(() => [props.trip.id, props.trip.version], load, { immediate: true })
onBeforeUnmount(() => { disposed = true; sequence++ })
async function review() {
  if (reviewing.value || saving.value) return
  const id = props.trip.id
  reviewing.value = true; reviewMessage.value = ''
  try {
    const latest = await api.get(`/api/trips/${id}`, {timeout:10000})
    if (disposed || props.trip.id !== id) return
    emit('updated', latest)
    await nextTick()
    const loaded = await load()
    if (loaded && !disposed && props.trip.id === id) reviewMessage.value = '已重新读取账本，表单仍保留。若支出已存在，请清空表单；若没有，请核对后再重试。'
  } catch (e) { if (!disposed && props.trip.id === id) loadError.value = e.message }
  finally { if (!disposed) reviewing.value = false }
}
watch(() => props.currentDayId, () => {
  if (!editing.value && !formTouched.value) {
    form.day_id = props.currentDayId || ''; form.item_id = ''; form.spent_on = dateForDay(props.currentDayId)
  }
}, { immediate: true })
function dayChanged() { form.item_id = ''; form.spent_on = dateForDay(form.day_id) }
function edit(item) {
  if (saving.value || props.busy || ((formTouched.value || editing.value) && !window.confirm('编辑此记录将替换表单中未保存的内容，继续吗？'))) return
  editing.value = item.id
  editVersion = props.trip.version; formTouched.value = false; error.value = ''
  createVersion = null; reviewRequired.value = false; reviewMessage.value = ''
  Object.assign(form, { amount: minorToText(item.amount_minor, item.currency), currency: item.currency, category: item.category,
    day_id: item.day_id || '', item_id: item.item_id || '', spent_on: item.spent_on || '', note: item.note })
  if (!selectedDay.value?.items.some(i => i.id === Number(form.item_id))) form.item_id = ''
}
async function save() {
  if (saving.value || props.busy) return
  error.value = ''
  let amount
  try { amount = toMinor(form.amount, form.currency) } catch (e) { error.value = e.message; return }
  saving.value = true
  const id = props.trip.id
  if (!editing.value && createVersion === null) createVersion = props.trip.version
  try {
    const url = `/api/trips/${id}/expenses${editing.value ? `/${editing.value}` : ''}`
    const data = { version: editing.value ? editVersion : createVersion, amount_minor: amount, currency: form.currency, category: form.category,
      day_id: form.day_id ? Number(form.day_id) : null, item_id: form.item_id ? Number(form.item_id) : null,
      spent_on: form.spent_on || null, note: form.note }
    const updated = await api[editing.value ? 'patch' : 'post'](url, data)
    if (!disposed && props.trip.id === id) { reset(); emit('updated', updated) }
  } catch (e) { if (!disposed && props.trip.id === id) { reviewRequired.value ||= Boolean(e.outcomeUnknown); error.value = e.status === 409 ? '行程已更新。请保留输入并核对账本，避免重复记账；重新填写前请先清空这份表单。' : e.message } }
  finally { if (!disposed) saving.value = false }
}
async function remove(item) {
  if (saving.value || props.busy) return
  if (!window.confirm('删除这笔支出？')) return
  saving.value = true; error.value = ''
  const id = props.trip.id
  try {
    const updated = await api.post(`/api/trips/${id}/expenses/${item.id}/delete`, { version: props.trip.version })
    if (!disposed && props.trip.id === id) { if (editing.value === item.id) reset(); emit('updated', updated) }
  } catch (e) { if (!disposed && props.trip.id === id) error.value = e.status === 409 ? '行程已更新，请刷新后再操作。' : e.message }
  finally { if (!disposed) saving.value = false }
}
</script>
<template>
  <section class="expenses"><div class="heading"><div><p class="eyebrow">TRIP EXPENSES</p><h2>旅行记账</h2><p class="intro">记录每一笔花销，按币种分别合计。关联日期随安排移动，支出日期保持不变。</p></div></div>
    <p v-if="loading" class="intro">正在加载账本…</p><p v-else-if="loadError" class="error" role="alert">账本加载失败：{{ loadError }} <button type="button" class="plain" @click="load">重试</button></p><div v-else-if="Object.keys(totals).length" class="totals"><span v-for="(sum, currency) in totals" :key="currency">{{ currency }} <b>{{ minorToText(sum, currency) }}</b></span></div><p v-else class="intro">还没有支出记录。</p>
    <form class="expense-form" @submit.prevent="save" @input="formTouched = true" @change="formTouched = true"><fieldset :disabled="saving || busy"><div class="fields"><label>金额<input v-model="form.amount" inputmode="decimal" placeholder="例如 88.50" required></label><label>币种<select v-model="form.currency"><option>CNY</option><option>USD</option><option>EUR</option><option>JPY</option></select></label><label>分类<select v-model="form.category"><option>餐饮</option><option>交通</option><option>住宿</option><option>门票</option><option>购物</option><option>其他</option></select></label><label>关联日期<select v-model="form.day_id" @change="dayChanged"><option value="">不关联</option><option v-for="day in trip.days" :key="day.id" :value="day.id">第 {{ day.day_number }} 天</option></select></label><label>关联安排<select v-model="form.item_id" :disabled="!selectedDay"><option value="">不关联</option><option v-for="item in selectedDay?.items || []" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><label>支出日期<input v-model="form.spent_on" type="date"></label></div><label class="note">备注<input v-model="form.note" maxlength="500" placeholder="可留空"></label></fieldset><p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="reviewMessage" role="status">{{ reviewMessage }}</p><button v-if="reviewRequired" type="button" class="plain" :disabled="loading || saving || reviewing || busy" @click="review">核对已保存账本</button><div class="actions"><button class="primary" :disabled="saving || busy">{{ saving ? '保存中…' : (editing ? '保存修改' : '记一笔') }}</button><button v-if="editing || formTouched" type="button" class="plain" :disabled="saving || busy" @click="reset">{{ editing ? "取消编辑" : "清空表单" }}</button></div></form>
    <ul v-if="!loading && !loadError && items.length" class="entries"><li v-for="item in items" :key="item.id"><div><b>{{ item.category }} · {{ item.currency }} {{ minorToText(item.amount_minor, item.currency) }}</b><small>{{ item.spent_on || '日期未填' }}{{ item.item_name ? ` · ${item.item_name}` : '' }}{{ item.note ? ` · ${item.note}` : '' }}</small></div><div class="entry-actions"><button :disabled="saving || busy" @click="edit(item)">编辑</button><button :disabled="saving || busy" @click="remove(item)">删除</button></div></li></ul>
  </section>
</template>
<style scoped>.expenses{border-top:1px solid #e8eee9;padding:24px}.eyebrow{font-size:10px;letter-spacing:.14em;color:#789487;font-weight:700}.expenses h2{font-size:19px;margin:6px 0}.intro{font-size:12px;color:#819389;line-height:1.6;margin:7px 0 12px}.totals{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.totals span{background:#e9f3eb;color:#386b4e;padding:8px 12px;border-radius:8px;font-size:12px}.expense-form{background:#f6f9f6;border-radius:10px;padding:14px}.expense-form fieldset{border:0;padding:0;margin:0;min-width:0}.fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.expense-form label{display:grid;gap:5px;font-size:12px;font-weight:bold;color:#526d5e}.expense-form input,.expense-form select{min-width:0;width:100%;border:1px solid #dbe5de;border-radius:7px;padding:8px;background:white;color:#31483a;font:inherit;font-weight:normal}.note{margin-top:10px}.actions{display:flex;align-items:center;gap:10px;margin-top:12px}.primary{border:0;border-radius:7px;background:#1a6e50;color:white;font-weight:700;padding:9px 15px}.plain,.entry-actions button{border:0;background:none;color:#1a6e50;font-size:12px}.error{color:#a33;margin-top:8px;font-size:12px}.entries{list-style:none;margin-top:15px}.entries li{display:flex;justify-content:space-between;gap:10px;border-bottom:1px solid #edf1ed;padding:11px 0;font-size:12px}.entries small{display:block;color:#84968a;margin-top:5px;white-space:pre-wrap;overflow-wrap:anywhere}.entry-actions{display:flex;gap:7px;white-space:nowrap}button:disabled{opacity:.5}@media(max-width:760px){.expenses{padding:20px 16px}.fields{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
