<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../store/auth'
import TripMap from '../components/TripMap.vue'
import PlaceSearch from '../components/PlaceSearch.vue'
import TripRoutes from '../components/TripRoutes.vue'
import AiTripAssistant from '../components/AiTripAssistant.vue'
import TripExpenses from '../components/TripExpenses.vue'
import TripBudgetReview from '../components/TripBudgetReview.vue'
import TripReservations from '../components/TripReservations.vue'
import TripGuideImport from '../components/TripGuideImport.vue'
import TripSharingPanel from '../components/TripSharingPanel.vue'
import TripEditDialog from '../components/TripEditDialog.vue'
import TripFootprints from '../components/TripFootprints.vue'
import { guestTrips, isGuestTripId, isGuestTripStorageChange } from '../store/guestTrips'
import { allowTripDeparture, guardTripUnload } from '../utils/tripDraftGuard'
import { registerAuthChangeGuard } from '../store/authChangeGuard'
import { formatTripText } from '../utils/tripTextExport'
import { scheduleWarning } from '../utils/tripScheduleWarnings'
import { isLocationFreeRest, routeLegKey } from '../utils/tripRouteSummary'
import {validWorkspaceTrip,workspaceTabs,workspaceView} from '../utils/tripWorkspace'

const route = useRoute(), { user, openLogin } = useAuth()
const router=useRouter(),activeSection=ref('itinerary')
const viewportQuery=typeof window!=='undefined'?window.matchMedia?.('(max-width: 760px)'):null
const compact=ref(Boolean(viewportQuery?.matches))
const onViewportChange=e=>{compact.value=e.matches}
const trip = ref(null), loading = ref(false), busy = ref(false), error = ref(''), dayId = ref(null), selectedId = ref(null)
const editingId = ref(null), showForm = ref(false), pendingName = ref('')
const showTripForm = ref(false), showDayForm = ref(false), dragItem = ref(null)
const dayTheme = ref(''), dayEditId = ref(null), dayEditVersion = ref(null)
const itemEditVersion = ref(null), tripEditVersion = ref(null), itemDayId = ref(null)
const itemInitial = ref(''), tripInitial = ref(''), dayInitial = ref('')
const routes = ref([])
const localTrip = computed(() => isGuestTripId(String(route.params.id)))
const sections=computed(()=>workspaceTabs(localTrip.value))
const coreVisible=computed(()=>['itinerary','map'].includes(activeSection.value))
const mapVisible=computed(()=>coreVisible.value&&(!compact.value||activeSection.value==='map'))
const editorBlocked=computed(()=>busy.value||loading.value||accessLost.value)
const incomingGuideId = computed(() => {
  const raw = route.query.guide
  if (typeof raw !== 'string' || !/^[1-9]\d*$/.test(raw)) return null
  const id = Number(raw)
  return Number.isSafeInteger(id) ? id : null
})
const remoteUpdate = ref(false), accessLost = ref(false), childDirty = reactive({ ai: false, guide: false, budget: false, expenses: false, reservations: false, footprints: false, sharing: false })
const remoteVersion = ref(null)
const editorReset = ref(0)
const hasDraft = computed(() => showForm.value || showTripForm.value || showDayForm.value || Boolean(pendingName.value.trim()) || Object.values(childDirty).some(Boolean))
let syncTimer = null, syncRunning = false, loadGeneration = 0, unregisterAuthGuard = null
let loadedOwner=null
const tripForm = reactive({ title: '', destination: '', start_date: '', status: 'planning', walking_preferences: {} })
const canEditWalkingPreferences = computed(() => localTrip.value || Object.hasOwn(trip.value || {}, 'walking_preferences'))
const blank = () => ({ name: '', kind: '地点', start_time: '', duration_min: null, address: '', lng: null, lat: null, amap_id: '', note: '', activity: '', source: '' })
const form = reactive(blank())
const originalItem = ref(null)
const pendingCandidates = computed(() => {
  const item = originalItem.value
  return item && form.name === item.name && !form.amap_id && form.lng == null &&
    Array.isArray(item.location_candidates) ? item.location_candidates : []
})
const itemKinds = ['地点', '景点', '室内场馆', '餐饮', '交通', '酒店', '自由活动', '休息']
const currentDay = computed(() => trip.value?.days.find(d => d.id === dayId.value) || trip.value?.days[0])
const nearbyAnchors = computed(() => {
  const items = trip.value?.days.find(day => day.id === itemDayId.value)?.items || []
  const index = items.findIndex(item => item.id === editingId.value)
  const split = index < 0 ? items.length : index
  return [...items.slice(0, split).reverse(), ...items.slice(split + 1)].filter(
    item => Number.isFinite(item.lng) && Number.isFinite(item.lat))
})
const selectedLocationMissing=computed(()=>{const item=currentDay.value?.items.find(i=>i.id===selectedId.value);return item&&(item.lng==null||item.lat==null)})
const statusText = { planning: '规划中', upcoming: '即将出发', completed: '已结束' }
async function load({reset=false,beforeApply=null}={}) {
  const run = ++loadGeneration, id = String(route.params.id), uid = user.value?.id
  const preserve=!reset&&trip.value&&String(trip.value.id)===id&&loadedOwner===uid
  error.value = '';busy.value = false
  if(!preserve){trip.value=null;loadedOwner=null;dayId.value=null;selectedId.value=null;routes.value=[];activeSection.value='itinerary';remoteUpdate.value=false;remoteVersion.value=null;accessLost.value=false}
  if (!user.value && !localTrip.value){loading.value=false;return false}
  loading.value = true
  try {
    const result = localTrip.value ? await guestTrips.get(id) : await api.get(`/api/trips/${id}`)
    if (run !== loadGeneration || id !== String(route.params.id) || uid !== user.value?.id) return
    if(!validWorkspaceTrip(result,id))throw Error('无法确认旅行内容，请重试；当前已加载内容仍保留。')
    if(preserve&&result.version<Math.max(trip.value.version,remoteVersion.value||0))throw Error('读取到较早的行程版本，请稍后重试；当前内容仍保留。')
    if(typeof beforeApply==='function')beforeApply()
    trip.value = result;loadedOwner=uid
    dayId.value = result.days.some(d=>d.id===dayId.value)?dayId.value:result.days[0].id
    if(!currentDay.value.items.some(i=>i.id===selectedId.value))selectedId.value=null
    remoteUpdate.value=false;remoteVersion.value=null;accessLost.value=false
    return true
  } catch (e) { if (run === loadGeneration){error.value=e.message;if([401,404].includes(e.status))accessLost.value=true};return false }
  finally { if (run === loadGeneration) loading.value = false }
}
async function syncTrip() {
  if (syncRunning || loading.value || (!localTrip.value && !user.value) || !trip.value || document.hidden || busy.value || accessLost.value) return
  const id = trip.value.id, run = loadGeneration, uid = user.value?.id, local = localTrip.value
  syncRunning = true
  try {
    const latest = local ? await guestTrips.get(id) : await api.get(`/api/trips/${id}`, { timeout: 10000 })
    if(!validWorkspaceTrip(latest,id))return
    if (run !== loadGeneration || uid !== user.value?.id || String(route.params.id) !== String(id) || !trip.value || trip.value.id !== id || latest.version <= trip.value.version) return
    remoteVersion.value = latest.version
    if (hasDraft.value || busy.value) remoteUpdate.value = true
    else { trip.value = latest; remoteUpdate.value = false }
  } catch (e) { if (run === loadGeneration && uid === user.value?.id && (e.status === 404 || (!local && e.status === 401))) accessLost.value = true }
  finally { syncRunning = false }
}
async function refreshRemote() {
  if(busy.value||loading.value)return
  if (hasDraft.value && !window.confirm('刷新会关闭编辑窗口并清除未应用的建议、输入和仅在本页显示的分享地址，确定继续？')) return
  await load({beforeApply:clearDrafts})
}
function clearDrafts() {
  showForm.value = false; showTripForm.value = false; showDayForm.value = false; pendingName.value = ''
  Object.keys(childDirty).forEach(key => { childDirty[key] = false })
  editorReset.value++
}
function confirmDeparture() {
  return allowTripDeparture(hasDraft.value, busy.value, message => window.confirm(message))
}
function beforeUnload(event) {
  guardTripUnload(event, hasDraft.value, busy.value)
}
function onStorage(event) {
  if (localTrip.value && isGuestTripStorageChange(event, String(route.params.id))) syncTrip()
}
function onVisibilityChange() { if (!document.hidden) syncTrip() }
onBeforeRouteLeave(() => confirmDeparture())
onBeforeRouteUpdate((to, from) => {
  if(String(to.params.id)!==String(from.params.id))return confirmDeparture()
  if(to.query.day!==from.query.day&&childDirty.footprints){error.value='请先保存、取消或清空足迹表单，再切换日期。';activeSection.value='records';return false}
  return true
})
onMounted(() => { viewportQuery?.addEventListener?.('change',onViewportChange);syncTimer = window.setInterval(syncTrip, 8000); window.addEventListener('beforeunload', beforeUnload); window.addEventListener('storage', onStorage); document.addEventListener('visibilitychange', onVisibilityChange); unregisterAuthGuard = registerAuthChangeGuard(confirmDeparture) })
onBeforeUnmount(() => { loadGeneration++;viewportQuery?.removeEventListener?.('change',onViewportChange); if (syncTimer) window.clearInterval(syncTimer); window.removeEventListener('beforeunload', beforeUnload); window.removeEventListener('storage', onStorage); document.removeEventListener('visibilitychange', onVisibilityChange); unregisterAuthGuard?.() })
watch(() => route.params.id, (id, previous) => {
  if (id === previous) return
  clearDrafts()
}, {flush:'sync'})
watch(() => user.value?.id, (id, previous) => { if (id !== previous) clearDrafts() }, {flush:'sync'})
watch([()=>user.value?.id, () => route.params.id],()=>load({reset:true}), { immediate: true,flush:'sync' })
watch(()=>[trip.value?.id,route.query.panel,route.query.day,incomingGuideId.value],()=>{
  if(!trip.value)return
  const view=workspaceView(route.query,trip.value,localTrip.value);activeSection.value=view.panel
  if(view.day&&view.day.id!==dayId.value){dayId.value=view.day.id;selectedId.value=null}
  if(incomingGuideId.value&&view.panel==='assistant')nextTick(()=>document.getElementById('guide-import')?.scrollIntoView({block:'start'}))
},{immediate:true})
async function mutate(url, payload, method = 'post', editVersion = null) {
  if (busy.value||loading.value||accessLost.value) return false
  busy.value = true; error.value = ''
  const run = loadGeneration, activeId = String(route.params.id), uid = user.value?.id
  const active = () => run === loadGeneration && activeId === String(route.params.id) && uid === user.value?.id
  let updated
  try {
    const body = { ...payload, version: editVersion ?? trip.value.version }
    if (!localTrip.value) updated = await api[method](url, body)
    else {
      const id = trip.value.id, tail = url.slice(`/api/trips/${id}`.length)
      const dayItem = tail.match(/^\/days\/(\d+)\/items$/)
      const dayEdit = tail.match(/^\/days\/(\d+)$/)
      const dayRemove = tail.match(/^\/days\/(\d+)\/remove$/)
      const routePreference = tail.match(/^\/days\/(\d+)\/route-preference$/)
      const item = tail.match(/^\/items\/(\d+)(?:\/(move|delete))?$/)
      const place = tail.match(/^\/places\/(\d+)\/(schedule|delete)$/)
      if (!tail && method === 'patch') updated = await guestTrips.patch(id, body)
      else if (tail === '/days') updated = await guestTrips.addDay(id, body)
      else if (dayEdit && method === 'patch') updated = await guestTrips.updateDay(id, Number(dayEdit[1]), body)
      else if (dayRemove) updated = await guestTrips.removeDay(id, Number(dayRemove[1]), body)
      else if (routePreference) updated = await guestTrips.setRoutePreference(id, Number(routePreference[1]), body)
      else if (dayItem) updated = await guestTrips.addItem(id, Number(dayItem[1]), body)
      else if (item && item[2] === 'move') updated = await guestTrips.moveItem(id, Number(item[1]), body)
      else if (item && item[2] === 'delete') updated = await guestTrips.deleteItem(id, Number(item[1]), body)
      else if (item && method === 'patch') updated = await guestTrips.updateItem(id, Number(item[1]), body)
      else if (tail === '/places') updated = await guestTrips.addPlace(id, body)
      else if (place && place[2] === 'schedule') updated = await guestTrips.schedulePlace(id, Number(place[1]), body)
      else if (place && place[2] === 'delete') updated = await guestTrips.deletePlace(id, Number(place[1]), body)
      else throw new Error('本地旅行暂不支持此操作')
    }
    if (!active()) return false
    if(!validWorkspaceTrip(updated,activeId))throw Error('保存回执无法确认，请保留输入并核对行程，避免重复保存。')
    receiveTripUpdate(updated)
    return true
  }
  catch (e) { if (active()) error.value = e.status === 409 && !String(e.message).includes('仍有') ? `${e.message}。请重新加载后再修改。` : e.message; return false }
  finally { if (active()) busy.value = false }
}
function receiveTripUpdate(updated) {
  if (!trip.value || !updated || updated.id !== trip.value.id || String(updated.id) !== String(route.params.id)) return
  if (updated.version > trip.value.version) {
    trip.value = updated
    if (remoteVersion.value === null || updated.version >= remoteVersion.value) remoteUpdate.value = false
  }
}
function itemData() { return Object.fromEntries(Object.keys(blank()).map(k => [k, form[k]])) }
function newItem() {
  originalItem.value = null
  editingId.value = null; Object.assign(form, blank()); itemInitial.value = JSON.stringify(itemData())
  itemEditVersion.value = trip.value.version; itemDayId.value = currentDay.value.id; error.value = ''; showForm.value = true
}
function editItem(item) {
  originalItem.value = item
  editingId.value = item.id; Object.assign(form, blank(), item); itemInitial.value = JSON.stringify(itemData())
  itemEditVersion.value = trip.value.version; itemDayId.value = currentDay.value.id; error.value = ''; showForm.value = true
}
function closeItem() {
  if (!busy.value && (JSON.stringify(itemData()) === itemInitial.value || window.confirm('关闭将丢弃未保存的安排，确定关闭吗？'))) showForm.value = false
}
function clearLocation() {
  if (form.lng != null || form.lat != null || form.amap_id) {
    form.lng = null; form.lat = null; form.amap_id = ''; form.address = ''; form.source = ''
  }
}
function selectLocation(place) {
  Object.assign(form, { name: place.name, address: place.address, lng: place.lng, lat: place.lat,
                        amap_id: place.amap_id, source: place.source })
}
async function saveItem() {
  const data = itemData()
  data.duration_min = data.duration_min === '' ? null : data.duration_min
  const path = editingId.value ? `/api/trips/${trip.value.id}/items/${editingId.value}` : `/api/trips/${trip.value.id}/days/${itemDayId.value}/items`
  if (await mutate(path, data, editingId.value ? 'patch' : 'post', itemEditVersion.value)) showForm.value = false
}
async function move(item, targetDay, index) { await mutate(`/api/trips/${trip.value.id}/items/${item.id}/move`, { day_id: targetDay, index }) }
function dragStart(item) { dragItem.value = item }
async function dropAt(index) {
  const item = dragItem.value; dragItem.value = null
  if (!item) return
  const from = currentDay.value.items.findIndex(i => i.id === item.id)
  if (from < 0) return
  const target = index > from ? index - 1 : index
  if (target !== from) await move(item, currentDay.value.id, target)
}
function editTrip() {
  Object.assign(tripForm, {
    title: trip.value.title,
    destination: trip.value.destination,
    start_date: trip.value.start_date || '',
    status: trip.value.status,
    walking_preferences: { prefer_short_walks: false, check_climbs: false, daily_limit_m: null, ...trip.value.walking_preferences },
  })
  tripInitial.value = JSON.stringify(tripForm)
  tripEditVersion.value = trip.value.version
  error.value = ''
  showTripForm.value = true
}
function closeTrip() { if (!busy.value && (JSON.stringify(tripForm) === tripInitial.value || window.confirm('关闭将丢弃未保存的旅行信息，确定关闭吗？'))) showTripForm.value = false }
function editDay() {
  dayTheme.value = currentDay.value.theme; dayInitial.value = dayTheme.value; dayEditId.value = currentDay.value.id; dayEditVersion.value = trip.value.version
  error.value = ''; showDayForm.value = true
}
function closeDay() { if (!busy.value && (dayTheme.value === dayInitial.value || window.confirm('关闭将丢弃未保存的主题，确定关闭吗？'))) showDayForm.value = false }
async function saveDay() {
  if (await mutate(`/api/trips/${trip.value.id}/days/${dayEditId.value}`, {theme:dayTheme.value}, 'patch', dayEditVersion.value)) showDayForm.value = false
}
async function saveTrip() {
  const { walking_preferences, ...fields } = tripForm
  const data = { ...fields, start_date: fields.start_date || null }
  if (canEditWalkingPreferences.value) {
    data.walking_preferences = { ...walking_preferences, daily_limit_m: walking_preferences.daily_limit_m === '' ? null : walking_preferences.daily_limit_m }
  }
  if (await mutate(`/api/trips/${trip.value.id}`, data, 'patch', tripEditVersion.value)) showTripForm.value = false
}
async function remove(item) { if (window.confirm(`删除“${item.name}”？`)) await mutate(`/api/trips/${trip.value.id}/items/${item.id}/delete`, {}) }
async function addPending() {
  const name = pendingName.value.trim(); if (!name) return
  if (await mutate(`/api/trips/${trip.value.id}/places`, { name, source: '手动添加' })) pendingName.value = ''
}
async function addSearchResult(place) { await mutate(`/api/trips/${trip.value.id}/places`, place) }
async function schedule(place) { await mutate(`/api/trips/${trip.value.id}/places/${place.id}/schedule`, { day_id: currentDay.value.id }) }
async function deletePlace(place) { if (window.confirm(`移除待安排地点“${place.name}”？`)) await mutate(`/api/trips/${trip.value.id}/places/${place.id}/delete`, {}) }
function sectionDirty(id){
  if(id==='assistant')return childDirty.ai||childDirty.guide
  if(id==='records')return childDirty.expenses||childDirty.reservations||childDirty.footprints
  if(id==='sharing')return childDirty.sharing
  if(id==='places')return Boolean(pendingName.value.trim())
  return id==='itinerary'&&(showForm.value||showDayForm.value||showTripForm.value)
}
async function openWorkspace(id,target=null){
  if(!sections.value.some(s=>s.id===id))return
  const failed=await router.replace({query:{...route.query,panel:id}})
  if(failed)return
  activeSection.value=id
  await nextTick()
  const el=document.getElementById(target||`trip-panel-${id}`)
  el?.focus?.({preventScroll:true});el?.scrollIntoView?.({behavior:'smooth',block:'start'})
}
function jumpTo(id) { return openWorkspace(id==='trip-map'?'map':'itinerary',id) }
function showItemOnMap(id) {
  selectedId.value = id
  if (compact.value) jumpTo('trip-map')
}
function selectMapItem(id) {
  selectedId.value = id
  openWorkspace('itinerary',`itinerary-item-${id}`)
}
async function changeDay(day) {
  if(editorBlocked.value||day.id===currentDay.value?.id)return
  if(childDirty.footprints){error.value='请先保存、取消或清空足迹表单，再切换日期。';await openWorkspace('records');return}
  const failed=await router.replace({query:{...route.query,day:String(day.day_number)}})
  if(failed)return
    if(error.value==='请先保存、取消或清空足迹表单，再切换日期。')error.value=''
  dayId.value=day.id;selectedId.value=null;showForm.value=false
}
async function addDay() {
  if (await mutate(`/api/trips/${trip.value.id}/days`, {})) await changeDay(trip.value.days[trip.value.days.length - 1])
}
async function removeDay() {
  if (hasDraft.value) { error.value = '请先处理未保存的输入或预览，再删除日期。'; return }
  const day = currentDay.value
  if (!window.confirm(`删除第 ${day.day_number} 天？只能删除没有关联内容的最后一天。`)) return
  if (await mutate(`/api/trips/${trip.value.id}/days/${day.id}/remove`, {})) await changeDay(trip.value.days[trip.value.days.length - 1])
}
function saveRoutePreference(choice) {
  return mutate(`/api/trips/${trip.value.id}/days/${currentDay.value.id}/route-preference`, choice, 'patch')
}
function saveBudgetChecklist(checklist, version) {
  return mutate(`/api/trips/${trip.value.id}/budget-review`, { checklist }, 'patch', version)
}
function nextLeg(index) {
  const day = currentDay.value, first = day.items[index]
  if (isLocationFreeRest(first)) return null
  const nextIndex = day.items.findIndex((item, position) => position > index && !isLocationFreeRest(item))
  if (nextIndex === -1) return null
  const next = day.items[nextIndex], key = routeLegKey(first, next)
  const mode = day.route_preferences?.[key]
  const route = routes.value.find(r => !r.preview && r.mode === mode && r.origin_id === first.id && r.destination_id === next.id)
  return { first, next, mode, route, breaks: day.items.slice(index + 1, nextIndex) }
}
function segmentHint(index) {
  const leg = nextLeg(index)
  if (!leg) return ''
  if (!leg.mode) return '至下一站的交通方式待选择'
  const mode = leg.mode === 'walking' ? '步行' : '驾车'
  return leg.route ? `${mode}至下一站约 ${Math.max(1, Math.ceil(leg.route.duration_s / 60))} 分钟 · 高德地图参考` : `已采用${mode} · 耗时待查询`
}
function warningToNext(index) {
  const items = currentDay.value.items, leg = nextLeg(index)
  return scheduleWarning(items[index], items[index + 1]) ||
    (leg?.route ? scheduleWarning(leg.first, leg.next, leg.route, leg.breaks) : '')
}
function dayDate(day) {
  if (!trip.value?.start_date) return `第 ${day.day_number} 天`
  const date = new Date(`${trip.value.start_date}T12:00:00`); date.setDate(date.getDate() + day.day_number - 1)
  return `${date.getMonth() + 1}月${date.getDate()}日`
}
function exportItinerary() {
  if (!trip.value || hasDraft.value || busy.value || accessLost.value || remoteUpdate.value) return
  const file = new Blob(['\ufeff', formatTripText(trip.value)], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(file), link = document.createElement('a')
  link.href = url; link.download = '旅行行程.txt'; link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 60000)
}
</script>
<template>
  <main class="trip-page">
    <router-link class="back" to="/">← 我的旅行</router-link>
    <div v-if="!user && !localTrip" class="state">登录后查看旅行。 <button @click="openLogin('login')">登录</button></div>
    <div v-else-if="loading && !trip" class="state">正在加载行程…</div>
    <div v-else-if="!trip" class="state" role="alert">{{ error || '旅行不存在' }} <button @click="load">重试</button></div>
    <template v-else>
      <header class="trip-header"><div><p class="eyebrow">YOUR JOURNEY · {{ statusText[trip.status] }}</p><h1>{{ trip.title }}</h1><p>{{ trip.destination || '目的地待定' }} <span>·</span> {{ trip.start_date || '日期待定' }} <span>·</span> {{ trip.days.length }} 天</p></div><div class="header-actions"><button class="ai-link" :disabled="editorBlocked" @click="editTrip">编辑旅行信息</button><button class="ai-link" :disabled="hasDraft || busy || accessLost || remoteUpdate" @click="exportItinerary">导出行程文本</button><button class="ai-link" @click="openWorkspace('assistant')">✦ AI 规划助手</button><button class="ai-link" :disabled="editorBlocked || loading" @click="refreshRemote">刷新旅行</button></div></header>
      <p class="export-hint">导出当前页面已保存的每日安排，包含安排备注；不包含预订记录和账本。{{ hasDraft || busy ? '请先处理未保存的内容或等待保存完成。' : remoteUpdate ? '请先刷新其他页面的更新。' : '' }}</p>
      <p v-if="localTrip" class="local-note">本机旅行仅保存在此浏览器，清除浏览器数据会丢失；登录后可在首页复制到账号。{{ !guestTrips.supportsCrossTabLock() ? '当前浏览器不支持跨标签页协调，请只在一个标签页编辑。' : '' }}</p>
      <p v-if="accessLost" class="local-note" role="alert">{{ localTrip ? '本机旅行已被其他页面删除或浏览器数据已清除；当前页面中的未保存内容仍保留。' : '当前账号已无法访问此旅行，未保存的内容仍显示在页面上；请联系旅行所有者或重新登录。' }}</p>
      <p v-else-if="remoteUpdate" class="local-note" role="status">{{ localTrip ? '本机旅行已在其他页面更新。' : '同行者更新了旅行。' }}为保留你的未保存内容，页面暂未自动覆盖。 <button @click="refreshRemote">刷新旅行</button></p>
      <p v-if="error" class="error" role="alert">{{ error }} <button @click="refreshRemote">重新加载</button></p>
      <p v-if="loading" class="workspace-status" role="status">正在更新行程，已加载内容仍保留…</p>
      <nav class="trip-tabs" aria-label="旅行工作区"><button v-for="section in sections" :key="section.id" type="button" :aria-pressed="activeSection === section.id" :class="{active:activeSection === section.id}" :aria-controls="section.id === 'map' ? 'trip-map' : `trip-panel-${section.id}`" @click="openWorkspace(section.id)">{{ section.label }}<small v-if="section.id === 'places' && trip.places.length">{{ trip.places.length }}</small><span v-if="sectionDirty(section.id)" class="draft-dot" aria-label="有未保存内容"></span></button></nav>
      <p v-if="hasDraft" class="workspace-status" role="status">有未保存的内容。切换工作区会保留，离开或刷新旅行前请先处理。</p>
      <div v-show="coreVisible" class="workspace"><section v-show="!compact || activeSection !== 'map'" id="trip-panel-itinerary" tabindex="-1" class="itinerary"><div class="days" aria-label="选择日期"><button v-for="day in trip.days" :key="day.id" :class="{ active: currentDay.id === day.id }" :disabled="editorBlocked" :aria-pressed="currentDay.id === day.id" @click="changeDay(day)"><b>DAY {{ day.day_number }}</b><small>{{ dayDate(day) }}</small></button><button class="new-day" :disabled="editorBlocked || trip.days.length >= 30" @click="addDay"><b>＋ 添加一天</b><small>最多 30 天</small></button></div><div id="trip-day-content" tabindex="-1" class="day-title"><div><p class="eyebrow">THE ITINERARY</p><h2>第 {{ currentDay.day_number }} 天 <small>{{ currentDay.theme }}</small></h2></div><div class="day-actions"><button class="edit-day" :disabled="editorBlocked" @click="editDay">编辑主题</button><button v-if="trip.days.length > 1 && currentDay.id === trip.days[trip.days.length - 1].id" class="remove-day" :disabled="editorBlocked" @click="removeDay">删除最后一天</button><button class="add" :disabled="editorBlocked" @click="newItem">＋ 添加安排</button></div></div>
        <div class="mobile-map-tools"><button type="button" @click="jumpTo('trip-map')">查看当天地图 ↓</button></div><div v-if="!currentDay.items.length" class="day-empty"><b>这一天还是空白</b><p>添加一个地点，开始安排你的旅程。</p><button :disabled="editorBlocked" @click="newItem">添加第一个安排 →</button></div>
        <ol v-else class="timeline" @dragover.prevent @drop.prevent="dropAt(currentDay.items.length)"><li v-for="(item, index) in currentDay.items" :key="item.id" :id="`itinerary-item-${item.id}`" tabindex="-1" :class="{ selected: selectedId === item.id }" draggable="true" @dragstart="dragStart(item)" @dragend="dragItem = null" @dragover.prevent @drop.stop.prevent="dropAt(index)"><button class="number" :aria-label="`在地图中查看 ${item.name}`" @click="showItemOnMap(item.id)">{{ index + 1 }}</button><div class="item-card" @click="selectedId = item.id"><div class="item-top"><div><span class="time">{{ item.start_time || '时间待定' }}</span><span class="kind">{{ item.kind }}</span></div><div class="item-buttons"><button :disabled="editorBlocked" @click.stop="editItem(item)">{{ item.location_candidates?.length ? '确认地点' : '编辑' }}</button><button :disabled="editorBlocked" @click.stop="remove(item)">删除</button></div></div><h3>{{ item.name }}</h3><p v-if="item.address">{{ item.address }}</p><p v-if="item.activity">{{ item.activity }}</p><p v-if="item.note" class="note">{{ item.note }}</p><p v-if="item.source">来源：{{ item.source }}</p><div class="item-bottom"><span v-if="item.duration_min != null">停留 {{ item.duration_min }} 分钟</span><div><button :disabled="index === 0 || editorBlocked" @click.stop="move(item, currentDay.id, index - 1)">↑ 上移</button><button :disabled="index === currentDay.items.length - 1 || editorBlocked" @click.stop="move(item, currentDay.id, index + 1)">↓ 下移</button><select aria-label="移动到其他日期" :value="''" :disabled="editorBlocked" @change="move(item, Number($event.target.value), 999)"><option value="" disabled>移动到</option><option v-for="day in trip.days.filter(d => d.id !== currentDay.id)" :key="day.id" :value="day.id">第 {{ day.day_number }} 天</option></select></div></div><p v-if="segmentHint(index)" class="segment-hint">{{ segmentHint(index) }}</p><p v-if="warningToNext(index)" class="schedule-warning" role="status">{{ warningToNext(index) }}</p></div></li></ol>
        <TripRoutes :trip-id="trip.id" :trip-version="trip.version" :day="currentDay" :walking-preferences="trip.walking_preferences" :local="localTrip" :disabled="editorBlocked || remoteUpdate" @routes="routes = $event" @preference="saveRoutePreference" />

      </section><aside v-show="!compact || activeSection === 'map'" id="trip-map" tabindex="-1" class="map-panel"><div class="map-heading"><b>今日地图</b><small>点击地点查看位置</small><button class="back-to-day" type="button" @click="jumpTo('trip-day-content')">返回当天安排 ↑</button></div><p v-if="selectedLocationMissing" class="map-selection-note" role="status">这个安排尚未关联地图，可在编辑安排时搜索并选择地点。</p><TripMap :active="mapVisible" :items="currentDay.items" :selected-id="selectedId" :routes="routes" @select="selectMapItem" /></aside></div>

      <section v-show="activeSection === 'places'" id="trip-panel-places" tabindex="-1" class="pending work-panel"><h2>待安排地点 <small>{{ trip.places.length }}</small></h2><p>先收集想去的地方，再决定放进哪一天。</p><router-link class="library-link" to="/spots">从景点库收集 →</router-link><PlaceSearch :city="trip.destination" :busy="editorBlocked" @add="addSearchResult" /><form class="pending-form" @submit.prevent="addPending"><input v-model="pendingName" aria-label="手动输入想去的地点" :disabled="editorBlocked" maxlength="160" placeholder="手动输入想去的地点"><button :disabled="editorBlocked">加入</button></form><p v-if="!trip.places.length" class="collection-empty">还没有收集地点。可搜索地点、手动输入，或从景点库加入旅行。</p><div v-for="place in trip.places" :key="place.id" class="place"><span>{{ place.name }}</span><div><button :disabled="editorBlocked" @click="schedule(place)">安排到当天</button><button :disabled="editorBlocked" @click="deletePlace(place)">移除</button></div></div></section>
      <section v-show="activeSection === 'assistant'" id="trip-panel-assistant" tabindex="-1" class="work-panel"><div class="workspace-heading"><p class="eyebrow">YOUR TRAVEL ASSISTANT</p><h2>一起完善这段旅行</h2><p>基于当前旅行生成建议，或从攻略整理地点；核对后再应用。</p></div>
        <AiTripAssistant :key="`ai-${editorReset}`" :trip="trip" :busy="editorBlocked" :local="localTrip" @updated="receiveTripUpdate" @dirty="childDirty.ai = $event" />
        <TripGuideImport :key="`guide-${editorReset}`" :trip="trip" :busy="editorBlocked" :local="localTrip" :initial-guide-id="incomingGuideId" @updated="receiveTripUpdate" @dirty="childDirty.guide = $event" />
      </section>
      <section v-show="activeSection === 'records'" id="trip-panel-records" tabindex="-1" class="work-panel"><div class="workspace-heading"><p class="eyebrow">TRAVEL RECORDS</p><h2>旅行记录</h2><p>保存预订信息，记录花销和属于自己的到访回忆。</p><p>当前记录日期：第 {{ currentDay.day_number }} 天 <button class="edit-day" @click="openWorkspace('itinerary')">返回行程切换日期</button></p><nav class="record-links" aria-label="记录快捷入口"><button @click="openWorkspace('records','trip-reservations')">预订</button><template v-if="!localTrip"><button @click="openWorkspace('records','trip-expenses')">账本</button><button @click="openWorkspace('records','trip-footprints')">足迹</button></template></nav></div>
        <TripBudgetReview v-if="!localTrip && Object.hasOwn(trip, 'budget_review')" :key="`budget-${editorReset}`" :trip="trip" :disabled="editorBlocked" :save-checklist="saveBudgetChecklist" @dirty="childDirty.budget = $event" />
        <div id="trip-reservations" class="record-part" tabindex="-1"><TripReservations :key="`reservations-${editorReset}`" :trip="trip" :busy="editorBlocked" :local="localTrip" @updated="receiveTripUpdate" @dirty="childDirty.reservations = $event" /></div>
        <template v-if="!localTrip"><div id="trip-expenses" class="record-part" tabindex="-1"><TripExpenses :key="`expenses-${editorReset}`" :trip="trip" :current-day-id="currentDay.id" :busy="editorBlocked" @updated="receiveTripUpdate" @dirty="childDirty.expenses = $event" /></div><div id="trip-footprints" class="record-part" tabindex="-1"><TripFootprints :key="`footprints-${editorReset}`" :trip="trip" :day="currentDay" :busy="editorBlocked" @updated="receiveTripUpdate" @dirty="childDirty.footprints = $event" /></div></template>
        <p v-else class="workspace-note">本机可记录预订信息；登录并复制到账号后，可使用旅行记账、足迹和私有照片。</p>
      </section>
      <section v-if="!localTrip" v-show="activeSection === 'sharing'" id="trip-panel-sharing" tabindex="-1" class="work-panel"><div class="workspace-heading"><p class="eyebrow">TRAVEL TOGETHER</p><h2>与同行者一起安排</h2><p>邀请可信任的人协作，或生成只读行程链接。</p></div><div :inert="loading || accessLost || undefined"><TripSharingPanel :key="`sharing-${editorReset}`" :trip-id="trip.id" @trip-changed="syncTrip" @dirty="childDirty.sharing = $event" /></div></section>
      <TripEditDialog v-if="showForm" :label="editingId ? '编辑安排' : '添加安排'" :busy="editorBlocked" @close="closeItem"><form class="edit-form" @submit.prevent="saveItem"><div class="form-head"><h2>{{ editingId ? '编辑安排' : '添加安排' }}</h2><button type="button" aria-label="关闭" :disabled="editorBlocked" @click="closeItem">×</button></div><fieldset :disabled="editorBlocked"><label>名称 <input v-model="form.name" maxlength="160" required placeholder="地点或活动名称" @input="clearLocation"></label><p class="location-hint">{{ form.lng != null && form.lat != null ? `已关联地点：${form.address || form.name}` : '地点尚未关联地图，可搜索并选择；自由活动也可以不关联。' }}</p><PlaceSearch :city="trip.destination" :busy="editorBlocked" action-label="选择地点" :candidates="pendingCandidates" :nearby-anchors="nearbyAnchors" collapse-on-select @add="selectLocation" /><div class="two"><label>开始时间 <input v-model="form.start_time" type="time"></label><label>停留分钟 <input v-model.number="form.duration_min" type="number" min="0" max="1440" placeholder="可留空"></label></div><label>类型 <select v-model="form.kind"><option v-if="!itemKinds.includes(form.kind)">{{ form.kind }}</option><option v-for="kind in itemKinds" :key="kind">{{ kind }}</option></select></label><label>地址 <input v-model="form.address" maxlength="300" placeholder="可留空"></label><label>安排说明 <textarea v-model="form.activity" maxlength="4000" rows="2"></textarea></label><label>备注 <textarea v-model="form.note" maxlength="4000" rows="2" placeholder="预约、注意事项等"></textarea></label></fieldset><p v-if="error" class="error" role="alert">{{ error }}</p><button class="save" :disabled="editorBlocked">{{ busy ? '保存中…' : '保存安排' }}</button></form></TripEditDialog>
      <TripEditDialog v-if="showDayForm" label="编辑当天主题" :busy="editorBlocked" @close="closeDay"><form class="edit-form" @submit.prevent="saveDay"><div class="form-head"><h2>编辑当天主题</h2><button type="button" aria-label="关闭" :disabled="editorBlocked" @click="closeDay">×</button></div><label>每日主题 <input v-model="dayTheme" :disabled="editorBlocked" maxlength="100" placeholder="例如：西湖慢游，可留空"></label><p v-if="error" class="error" role="alert">{{ error }}</p><button class="save" :disabled="editorBlocked">{{ busy ? "保存中…" : "保存主题" }}</button></form></TripEditDialog>
      <TripEditDialog v-if="showTripForm" label="编辑旅行信息" :busy="editorBlocked" @close="closeTrip"><form class="edit-form" @submit.prevent="saveTrip"><div class="form-head"><h2>编辑旅行信息</h2><button type="button" aria-label="关闭" :disabled="editorBlocked" @click="closeTrip">×</button></div><fieldset :disabled="editorBlocked"><label>旅行名称 <input v-model="tripForm.title" maxlength="100" required></label><label>目的地 <input v-model="tripForm.destination" maxlength="100"></label><label>出发日期 <input v-model="tripForm.start_date" type="date"></label><label>旅行状态 <select v-model="tripForm.status"><option value="planning">规划中</option><option value="upcoming">即将出发</option><option value="completed">已结束</option></select></label><div v-if="canEditWalkingPreferences" class="walking-settings">
          <b>步行偏好</b>
          <label class="walking-check"><input type="checkbox" v-model="tripForm.walking_preferences.prefer_short_walks"> 尽量少走路</label>
          <label class="walking-check"><input type="checkbox" v-model="tripForm.walking_preferences.check_climbs"> 关注台阶和斜坡</label>
          <label>每天地点间步行上限（米） <input type="number" min="1" max="100000" step="1" v-model.number="tripForm.walking_preferences.daily_limit_m" placeholder="可留空"></label>
          <p>按已查地点间路线比较，场馆内步行另计。</p>
        </div>
      </fieldset><p v-if="error" class="error" role="alert">{{ error }}</p><button class="save" :disabled="editorBlocked">{{ busy ? '保存中…' : '保存旅行信息' }}</button></form></TripEditDialog>
    </template>
  </main>
</template>
<style scoped>
.walking-settings{display:grid;gap:9px;padding:12px;background:#f3f7f3;border-radius:8px}.walking-settings .walking-check{display:flex;align-items:center;gap:8px}.walking-settings .walking-check input{width:auto}.walking-settings p{font-size:12px;color:#61776a}
.mobile-map-tools,.back-to-day{display:none}.header-actions{display:flex;gap:8px;flex-wrap:wrap}.day-actions{display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap}.day-title>div:first-child{min-width:0;overflow-wrap:anywhere}.edit-day{border:0;background:none;color:#1a6e50;font-size:12px;white-space:nowrap}.edit-day:disabled{opacity:.5}.remove-day{border:0;background:none;color:#9b5549;font-size:12px}.remove-day:disabled{opacity:.5}
.local-note{background:#fff6e5;color:#7c6331;border-radius:9px;padding:12px 15px;margin:0 0 16px;font-size:12px;line-height:1.6}.export-hint{color:#788c80;font-size:12px;line-height:1.6;margin:-16px 0 16px}.ai-link:disabled{opacity:.5;cursor:not-allowed}
.item-card .schedule-warning{color:#8a4f1b;background:#fff5e5;border-radius:6px;padding:8px;margin-top:8px}
.trip-page{width:90%;max-width:1280px;margin:28px auto 65px;color:#203b35}.back{color:#6c8276;font-size:13px}.trip-header{display:flex;justify-content:space-between;align-items:end;gap:15px;margin:28px 0}.eyebrow{color:#789487;font-weight:700;letter-spacing:.14em;font-size:10px}.trip-header h1{font-size:30px;margin:8px 0}.trip-header p:not(.eyebrow){color:#7a8d82}.trip-header p span{padding:0 5px}.ai-link,.add{border:1px solid #1a6e50;color:#1a6e50;background:#fff;border-radius:8px;padding:10px 14px;font-weight:700}.workspace{display:grid;grid-template-columns:minmax(0,1fr) minmax(320px, .9fr);gap:18px;align-items:start}.itinerary,.map-panel{background:#fff;border:1px solid #e1e9e2;border-radius:15px;overflow:hidden}.days{display:flex;overflow-x:auto;border-bottom:1px solid #e8eee9;padding:0 20px;gap:6px}.days button{border:0;border-bottom:3px solid transparent;background:none;padding:17px 15px 13px;color:#8b9b92;white-space:nowrap}.days button.active{color:#1a6e50;border-bottom-color:#1a6e50}.days b,.days small{display:block}.days small{font-size:11px;margin-top:6px}.day-title{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:25px 24px 15px}.day-title h2,.pending h2{font-size:19px;margin-top:6px}.day-title h2 small{font-size:12px;color:#8a9c91;font-weight:normal}.add{padding:9px 12px}.day-empty{margin:12px 24px 24px;padding:30px;text-align:center;border:1px dashed #d5e2d9;border-radius:12px;color:#7b9083}.day-empty p{margin:8px}.day-empty button,.place button{border:0;background:none;color:#1a6e50;font-weight:700}.timeline{list-style:none;padding:4px 24px 20px}.timeline li{display:flex;gap:13px;position:relative;padding-bottom:17px;scroll-margin-top:80px}.timeline li:not(:last-child):before{content:'';position:absolute;left:13px;top:30px;bottom:0;border-left:1px dashed #bad2c2}.number{flex:none;width:27px;height:27px;border:0;border-radius:50%;background:#dceee3;color:#1a6e50;font-weight:700;z-index:1}.selected .number{background:#1a6e50;color:white}.item-card{flex:1;min-width:0;border:1px solid #e5ece6;border-radius:10px;padding:15px}.selected .item-card{border-color:#a5cdb4}.item-top,.item-bottom{display:flex;align-items:center;justify-content:space-between;gap:8px}.time{font-size:12px;font-weight:bold;color:#1a6e50}.kind{font-size:11px;color:#91a297;margin-left:9px}.item-buttons button,.item-bottom button{border:0;background:none;color:#71867a;font-size:12px;margin-left:8px}.item-card h3{font-size:16px;margin:9px 0}.item-card p{font-size:12px;color:#788c80;line-height:1.6;white-space:pre-wrap}.item-card .note{margin-top:8px;background:#f6f8f3;border-radius:6px;padding:8px}.item-bottom{margin-top:12px;font-size:11px;color:#8b9b91;flex-wrap:wrap}.item-bottom select{border:0;background:#f4f7f4;color:#617e6d;padding:5px;border-radius:5px;margin-left:8px;font-size:11px}.item-bottom button:disabled{opacity:.35}.pending{border-top:1px solid #edf1ed;padding:24px}.pending h2 small{background:#e5f1e8;padding:3px 7px;border-radius:12px;color:#1a6e50;font-size:11px}.pending>p{color:#85958b;font-size:12px;margin:8px 0 14px}.pending-form{display:flex;gap:8px}.pending-form input{flex:1;min-width:0;padding:10px;border:1px solid #dae5dd;border-radius:8px}.pending-form button{background:#e7f2ea;border:0;border-radius:8px;padding:0 15px;color:#1a6e50;font-weight:bold}.place{display:flex;justify-content:space-between;gap:5px;padding:12px 0;border-bottom:1px solid #edf1ed}.place button{font-size:12px;margin-left:8px}.map-panel{position:sticky;top:74px;scroll-margin-top:70px}.day-title{scroll-margin-top:70px}.map-heading{padding:18px 20px;display:flex;justify-content:space-between}.map-heading small{color:#91a198}.map-panel :deep(.map-wrap){height:500px}.state{background:white;border-radius:12px;padding:35px;margin-top:30px}.state button,.error button{border:0;background:none;color:#1a6e50;text-decoration:underline}.error{padding:12px;color:#a33;background:#fff3f1;border-radius:8px;margin-bottom:15px}.edit-form{background:white;border-radius:15px;padding:24px;width:min(500px,100%);max-height:90vh;overflow:auto;display:grid;gap:14px}.edit-form,.edit-form fieldset{grid-template-columns:minmax(0,1fr)}.edit-form>*,.edit-form fieldset>*{min-width:0}.edit-form fieldset{border:0;padding:0;margin:0;min-width:0;display:grid;gap:14px}.form-head{display:flex;justify-content:space-between}.form-head h2{font-size:19px}.form-head button{border:0;background:none;font-size:25px}.edit-form label{font-size:13px;font-weight:700;display:grid;gap:6px}.edit-form input,.edit-form select,.edit-form textarea{min-width:0;width:100%;border:1px solid #dbe5de;border-radius:7px;padding:10px;font:inherit;font-weight:normal}.two{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;min-width:0}.save{border:0;border-radius:8px;background:#1a6e50;color:white;font-weight:bold;padding:13px}.save:disabled{opacity:.6}@media(max-width:760px){.mobile-map-tools{display:block;padding:0 16px 12px}.mobile-map-tools button,.map-heading .back-to-day{display:inline-block;border:0;background:#e7f2ea;color:#1a6e50;border-radius:8px;padding:9px 12px;font-size:12px;font-weight:700}.map-heading small{display:none}.trip-page{width:92%;margin-top:20px}.trip-header{align-items:start;flex-direction:column}.trip-header h1{font-size:26px}.workspace{grid-template-columns:1fr}.map-panel{position:static}.map-panel :deep(.map-wrap){height:300px;min-height:300px}.days{padding:0 10px}.day-title{padding:20px 16px}.timeline{padding-left:16px;padding-right:16px}.pending{padding:20px 16px}.item-bottom{align-items:start}.item-bottom button:first-child{margin-left:0}}

.trip-tabs{position:sticky;top:56px;z-index:9;display:flex;gap:3px;padding:5px;margin:20px 0 12px;background:#fff;border:1px solid #d9e5dc;border-radius:12px;box-shadow:0 4px 14px #23442f0a;overflow-x:auto}.trip-tabs button{flex:1 0 44px;min-height:44px;padding:8px 6px;border:0;border-radius:8px;background:none;color:#62796d;font:inherit;font-size:13px;white-space:nowrap;display:flex;align-items:center;justify-content:center;gap:5px}.trip-tabs button.active{background:#1a6e50;color:white}.trip-tabs small{font-size:10px;background:#e8f4ee;color:#286448;padding:1px 5px;border-radius:12px}.draft-dot{width:6px;height:6px;flex:none;border-radius:50%;background:#dfaa5c}.workspace-status,.workspace-note{font-size:12px;line-height:1.8;color:#6d8274;padding:8px 2px}.work-panel{background:#fff;border:1px solid #dfe8e1;border-radius:14px;overflow:hidden;margin-top:16px;scroll-margin-top:126px}.workspace-heading{padding:24px 24px 6px}.workspace-heading h2{font-size:21px;margin:7px 0}.workspace-heading p:not(.eyebrow){font-size:12px;line-height:1.7;color:#73877a}.workspace-note{padding:20px 24px}.trip-page button,.trip-page select,.trip-page :deep(button),.trip-page :deep(select){min-height:44px}.trip-page button:focus-visible,.trip-page select:focus-visible,.trip-page a:focus-visible{outline:3px solid #679b7f;outline-offset:3px}.work-panel:focus,.itinerary:focus,.map-panel:focus{outline:2px solid #a5c8b2;outline-offset:3px}.trip-page h1,.item-card,.place span,.trip-page :deep(.entries){overflow-wrap:anywhere;min-width:0}.workspace{align-items:start}.map-panel{top:126px;scroll-margin-top:126px}.map-panel :deep(.map-wrap){height:calc(100vh - 188px);min-height:360px}.library-link{display:inline-flex;min-height:44px;align-items:center;color:#1a6e50;font-weight:700;margin:4px 0 12px}.collection-empty{background:#f3f7f3;border-radius:9px;padding:15px!important;line-height:1.8}.trip-page #trip-day-content,.trip-page .timeline li{scroll-margin-top:126px}
@media(max-width:760px){.trip-tabs{margin-top:16px}.trip-tabs button{font-size:12px}.workspace-heading{padding:20px 16px 4px}.workspace-note{padding:20px 16px}.map-panel :deep(.map-wrap){height:min(60vh,520px);min-height:320px}.day-title{align-items:flex-start;flex-wrap:wrap;gap:12px}.item-bottom{flex-wrap:wrap;gap:8px}.item-bottom>div{display:flex;flex-wrap:wrap;gap:5px}.header-actions{width:100%;flex-wrap:wrap}.trip-page :deep(input),.trip-page :deep(textarea),.trip-page :deep(select){max-width:100%}}

.record-links{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.record-links button{border:1px solid #dbe7de;border-radius:8px;background:#f3f8f4;color:#1a6e50;padding:8px 14px}.record-part{scroll-margin-top:126px}.map-selection-note{padding:10px 16px;color:#705e35;background:#fff8e8;font-size:12px;line-height:1.6}
</style>
