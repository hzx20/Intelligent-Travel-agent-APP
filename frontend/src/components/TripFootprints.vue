<script setup>
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { api } from '../api'
import { validWorkspaceTrip } from '../utils/tripWorkspace'
import FootprintPhoto from './FootprintPhoto.vue'
const props = defineProps({ trip: { type: Object, required: true }, day: { type: Object, required: true }, busy: Boolean })
const emit = defineEmits(['updated', 'dirty'])
const records = ref([]), itemId = ref(''), visitedOn = ref(''), note = ref(''), saving = ref(false), error = ref('')
const loading = ref(false), loadError = ref(''), uploading = ref(false)
const editing = ref(null)
const pendingPhoto = shallowRef(null), photoReview = ref(null), checkingPhoto = ref(false)
const canRetryPhoto = computed(() => Boolean(pendingPhoto.value && photoReview.value?.photosUnchanged &&
  photoReview.value.version === pendingPhoto.value.version && props.trip.version === pendingPhoto.value.version))
let sequence = 0, disposed = false
watch([itemId, visitedOn, note, saving, editing, pendingPhoto], () => emit('dirty', Boolean(itemId.value || visitedOn.value || note.value || saving.value || editing.value || pendingPhoto.value)), { immediate: true, flush: 'sync' })
watch(() => props.day.id, () => { itemId.value = '' })
async function load() {
  const run = ++sequence, id = props.trip.id
  loading.value = true; loadError.value = ''
  try {
    const data = await api.get(`/api/trips/${id}/footprints`, { timeout: 10000 })
    if (run === sequence && props.trip.id === id) {
      if (!Array.isArray(data?.items)) throw new Error('无法读取足迹列表，请重试。')
      records.value = data.items
      return data.items
    }
  }
  catch (e) { if (run === sequence) loadError.value = e.message }
  finally { if (run === sequence) loading.value = false }
}
watch(() => props.trip.id, () => { finishPhotoReview(); checkingPhoto.value = false })
watch(() => [props.trip.id, props.trip.version], load, { immediate: true })
onBeforeUnmount(() => { disposed = true; sequence++ })
async function add() {
  if (!itemId.value || editing.value || saving.value || props.busy) return
  saving.value = true; error.value = ''
  const id = props.trip.id
  try {
    const updated = await api.post(`/api/trips/${id}/footprints`, { version: props.trip.version, item_id: Number(itemId.value), city: props.trip.destination || '', visited_on: visitedOn.value || null, note: note.value })
    if (!disposed && props.trip.id === id) { itemId.value = ''; visitedOn.value = ''; note.value = ''; emit('updated', updated) }
  } catch (e) { if (!disposed && props.trip.id === id) error.value = e.message } finally { if (!disposed) saving.value = false }
}
function clearNew(){if(saving.value)return;itemId.value='';visitedOn.value='';note.value='';error.value=''}
function startEdit(record) {
  if (saving.value || props.busy || editing.value) return
  error.value = ''
  editing.value = { id: record.id, version: props.trip.version, city: record.city || '',
    visited_on: record.visited_on || '', note: record.note || '' }
}
async function saveEdit() {
  if (!editing.value || saving.value || props.busy) return
  const draft = editing.value
  const id = props.trip.id
  saving.value = true; error.value = ''
  try {
    const updated = await api.patch(`/api/trips/${id}/footprints/${draft.id}`, {
      version: draft.version, city: draft.city, visited_on: draft.visited_on || null, note: draft.note,
    })
    if (!disposed && props.trip.id === id) { editing.value = null; emit('updated', updated) }
  } catch (e) {
    if (!disposed && props.trip.id === id) error.value = e.status === 409 ? `${e.message}。请保留需要的内容，刷新行程后重试。` : e.message
  } finally { if (!disposed) saving.value = false }
}
async function remove(record) {
  if (saving.value || props.busy || editing.value) return
  if (!window.confirm('删除这条足迹记录及其照片？')) return
  saving.value = true; error.value = ''
  const id = props.trip.id
  try { const updated = await api.post(`/api/trips/${id}/footprints/${record.id}/delete`, { version: props.trip.version }); if (!disposed && props.trip.id === id) emit('updated', updated) }
  catch (e) { if (!disposed && props.trip.id === id) error.value = e.message } finally { if (!disposed) saving.value = false }
}
function upload(record, event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file || saving.value || props.busy || editing.value || pendingPhoto.value) return
  if (!['image/jpeg', 'image/png'].includes(file.type) || file.size > 4 * 1024 * 1024) {
    error.value = '请选择不超过 4 MB 的 JPEG 或 PNG 图片。'; return
  }
  return sendPhoto({ tripId: props.trip.id, footprintId: record.id, version: props.trip.version, photoIds: [...record.photo_ids], file })
}
async function sendPhoto(attempt) {
  saving.value = true; uploading.value = true; error.value = ''
  const { tripId, footprintId, version, file } = attempt
  try {
    const updated = await api.uploadPhoto(`/api/trips/${tripId}/footprints/${footprintId}/photos`, file, version)
    if (!validWorkspaceTrip(updated, tripId) || updated.version <= version) {
      throw Object.assign(new Error('上传回执无法确认，请先核对已保存的照片。'), { outcomeUnknown: true })
    }
    if (!disposed && props.trip.id === tripId) {
      finishPhotoReview()
      emit('updated', updated)
    }
  } catch (e) {
    if (!disposed && props.trip.id === tripId) {
      if (e.outcomeUnknown) pendingPhoto.value = attempt
      photoReview.value = null
      error.value = e.message
      if (e.status === 409) error.value += pendingPhoto.value ? '。请先核对已保存的照片。' : '。请刷新行程后重试。'
    }
  }
  finally { if (!disposed) { saving.value = false; uploading.value = false } }
}
function finishPhotoReview() {
  pendingPhoto.value = null
  photoReview.value = null
  error.value = ''
}
function closePhotoReview() {
  if (!photoReview.value && !window.confirm('上传结果仍待核对，确定清除这张待核对图片？')) return
  finishPhotoReview()
}
async function reviewPhotos() {
  const attempt = pendingPhoto.value
  if (!attempt || checkingPhoto.value || saving.value || props.busy) return
  const active = () => !disposed && pendingPhoto.value === attempt && props.trip.id === attempt.tripId
  checkingPhoto.value = true; photoReview.value = null; error.value = ''
  try {
    const latest = await api.get(`/api/trips/${attempt.tripId}`, { timeout: 10000 })
    if (!active()) return
    if (!validWorkspaceTrip(latest, attempt.tripId)) {
      throw new Error('无法核对旅行版本，请重试。')
    }
    emit('updated', latest)
    await nextTick()
    if (!active()) return
    const items = await load()
    if (!items || !active()) return
    const photoIds = items.find(item => item.id === attempt.footprintId)?.photo_ids
    const photosUnchanged = Array.isArray(photoIds) && photoIds.length < 3 &&
      photoIds.length === attempt.photoIds.length && photoIds.every(id => attempt.photoIds.includes(id))
    photoReview.value = { version: latest.version, photosUnchanged }
  } catch (e) { if (active()) error.value = e.message }
  finally { if (active()) checkingPhoto.value = false }
}
function retryPhoto() {
  if (!canRetryPhoto.value || checkingPhoto.value || saving.value || props.busy || editing.value) return
  return sendPhoto(pendingPhoto.value)
}
async function removePhoto(record, photoId) {
  if (saving.value || props.busy || editing.value) return
  if (!window.confirm('删除这张足迹照片？')) return
  saving.value = true; error.value = ''
  const id = props.trip.id
  try {
    const updated = await api.post(`/api/trips/${id}/footprints/${record.id}/photos/${photoId}/delete`, {version: props.trip.version})
    if (!disposed && props.trip.id === id) emit('updated', updated)
  } catch (e) { if (!disposed && props.trip.id === id) error.value = e.message } finally { if (!disposed) saving.value = false }
}
</script>
<template>
  <section class="footprints">
    <p class="eyebrow">TRAVEL MEMORIES</p><h2>我的足迹</h2>
    <p>亲自标记到访的地点，记录属于自己的旅行回忆。</p>
    <form @submit.prevent="add">
      <fieldset :disabled="saving || busy || Boolean(editing)"><label>当天安排<select v-model="itemId" required><option value="">选择到访地点</option><option v-for="item in day.items" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
      <label>到访日期<input v-model="visitedOn" type="date"></label><label>备注<input v-model="note" maxlength="2000" placeholder="可留空"></label>
      <button :disabled="saving || busy || editing">记录到访</button><button v-if="itemId || visitedOn || note" type="button" :disabled="saving || busy" @click="clearNew">清空到访表单</button></fieldset>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="uploading" role="status">正在上传并处理照片…</p>
    <div v-if="pendingPhoto" class="photo-review">
      <p v-if="photoReview" role="status">{{ canRetryPhoto ? '已核对保存状态，可以重试本次照片。' : '已重新读取照片，请查看列表确认是否已保存。' }}</p>
      <button type="button" :disabled="checkingPhoto || saving || busy" @click="reviewPhotos">{{ checkingPhoto ? '正在核对…' : '核对已保存照片' }}</button>
      <button v-if="canRetryPhoto" type="button" :disabled="checkingPhoto || saving || busy || editing" @click="retryPhoto">重试本次照片</button>
      <button type="button" :disabled="checkingPhoto || saving || busy" @click="closePhotoReview">{{ photoReview ? '结束本次核对' : '清除待核对图片' }}</button>
    </div>
    <p v-if="loading" role="status">正在加载足迹…</p><p v-else-if="loadError" class="error" role="alert">足迹加载失败：{{ loadError }} <button type="button" @click="load">重试</button></p>
    <ul v-if="!loading && !loadError"><li v-for="record in records" :key="record.id"><div class="record">
      <div class="record-heading"><div><b>{{ record.place_name }}</b><small>{{ record.city || '城市未填' }} · {{ record.visited_on || '日期未填' }} · {{ record.location_status === 'associated' ? '已关联地图' : '地点待确认' }}<template v-if="record.note"> · {{ record.note }}</template></small></div><div class="record-actions"><button :disabled="saving || busy || editing" @click="startEdit(record)">编辑</button><button :disabled="saving || busy || editing" @click="remove(record)">删除足迹</button></div></div>
      <form v-if="editing?.id === record.id" class="edit-form" @submit.prevent="saveEdit"><fieldset :disabled="saving || busy"><label>城市<input v-model="editing.city" maxlength="100" placeholder="可留空"></label><label>到访日期<input v-model="editing.visited_on" type="date"></label><label>备注<textarea v-model="editing.note" maxlength="2000" rows="3" placeholder="可留空"></textarea></label><div class="edit-actions"><button :disabled="saving || busy">保存修改</button><button type="button" :disabled="saving" @click="editing = null">取消</button></div></fieldset></form>
      <div v-if="record.photo_ids?.length" class="photos"><div v-for="photo in record.photo_ids" :key="photo" class="photo"><FootprintPhoto :trip-id="trip.id" :footprint-id="record.id" :photo-id="photo"/><button :disabled="saving || busy || editing" @click="removePhoto(record, photo)">删除照片</button></div></div>
      <label v-if="record.photo_ids?.length < 3" class="upload">添加照片（最多 3 张，每张 4 MB）<input type="file" accept="image/jpeg,image/png" :disabled="saving || busy || editing || Boolean(pendingPhoto)" @change="upload(record, $event)"></label>
      <small>照片仅自己可见；保存时会缩放、转为 JPEG 并移除拍摄元数据。</small>
    </div></li></ul>
  </section>
</template>
<style scoped>
.photo-review { display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0; }
.photo-review p { flex-basis: 100%; }
.photo-review button { padding: 7px 10px; border: 1px solid #dbe5de; border-radius: 7px; background: white; color: #1a6e50; }
.photo-review button:disabled { opacity: .5; }
</style>
<style scoped>.footprints{padding:24px;border-top:1px solid #e8eee9}.eyebrow{font-size:10px;color:#789487;letter-spacing:.14em;font-weight:bold}.footprints h2{font-size:19px;margin:6px 0}.footprints p,.footprints small{font-size:12px;color:#788c80;line-height:1.6}.footprints form{display:grid;gap:10px;margin-top:12px}.footprints fieldset{border:0;padding:0;margin:0;min-width:0;display:grid;gap:10px}.footprints label{display:grid;gap:5px;font-size:12px}.footprints input,.footprints select,.footprints textarea{width:100%;border:1px solid #dbe5de;border-radius:7px;padding:9px;background:white;font:inherit}.footprints form button{justify-self:start;background:#1a6e50;color:white;border:0;border-radius:7px;padding:10px 15px}.footprints ul{list-style:none}.footprints li{display:flex;justify-content:space-between;gap:10px;border-top:1px solid #edf1ed;padding:12px 0}.footprints small{display:block;white-space:pre-wrap}.footprints li button{border:0;background:none;color:#a04c3e}.footprints .error{color:#a33}.record{width:100%;min-width:0}.record-heading{display:flex;justify-content:space-between;gap:10px}.record-actions{display:flex;gap:10px;white-space:nowrap}.record-actions button:first-child{color:#1a6e50}.edit-form{padding:12px;background:#f5f8f4;border-radius:8px}.edit-form textarea{resize:vertical}.edit-actions{display:flex;gap:8px}.footprints .edit-form .edit-actions button{background:#1a6e50;color:white;padding:8px 12px}.footprints .edit-form .edit-actions button:last-child{background:#e7eee8;color:#315f46}.photos{display:flex;gap:10px;flex-wrap:wrap;margin-top:10px}.photo button{display:block;font-size:11px;padding:5px 0}.upload{margin:8px 0;color:#1a6e50}.upload input{max-width:290px;font-size:12px}@media(max-width:760px){.footprints{padding:20px 16px}}</style>
