<script setup>
import { onMounted, onBeforeUnmount, nextTick, ref, watch } from 'vue'
import { loadAmap } from '../utils/amap'
import { waitForMapReady } from '../utils/mapReady'
import { isLocationFreeRest } from '../utils/tripRouteSummary'
const props = defineProps({ items: { type: Array, default: () => [] }, selectedId: { type: Number, default: null }, routes: { type: Array, default: () => [] }, connect: { type: Boolean, default: true },active:{type:Boolean,default:true},unavailableMessage:{type:String,default:'地图暂不可用，行程编辑不受影响。'},emptyMessage:{type:String,default:'添加带坐标的地点后，将在这里显示。'} })
const emit = defineEmits(['select'])
const container = ref(null), error = ref(''), ready = ref(false), hasPoints = ref(false)
let map = null, AMap = null, overlays = [], disposed = false, lastGeometry = null
let initialization = null
const starting=ref(false)
let focusedKey=null
function focusSelected(){
  if(!ready.value||!map||!props.active)return
  const item=props.items.find(i=>i.id===props.selectedId),key=item?JSON.stringify([item.id,item.lng,item.lat]):null
  if(key===focusedKey)return
  focusedKey=key
  if(item?.lng!=null&&item?.lat!=null)map.setCenter([item.lng,item.lat])
}
function fitAll() {
  if (ready.value && map && overlays.length && hasPoints.value) map.setFitView(overlays, false, [55, 55, 55, 55], 14)
}
function draw() {
  if (!ready.value || !map || !AMap || !props.active) return
  const geometry = JSON.stringify(props.items.map(i => [i.id, i.lng, i.lat]))
  const fit = geometry !== lastGeometry
  lastGeometry = geometry
  if (overlays.length) map.remove(overlays)
  overlays = []
  const points = props.items.filter(i => i.lng != null && i.lat != null)
  hasPoints.value = points.length > 0
  points.forEach((item) => {
    const index = props.items.findIndex(i => i.id === item.id) + 1
    // Only the numeric index is embedded; names remain plain marker titles.
    const selected = item.id === props.selectedId
    const marker = new AMap.Marker({ position: [item.lng, item.lat], title: item.name,
      content: `<div class="trip-map-marker${selected ? ' selected' : ''}">${index}</div>`,
      anchor: 'center', zIndex: selected ? 120 : 100 })
    marker.on('click', () => emit('select', item.id))
    overlays.push(marker)
  })
  // Match the route API's adjacency rule and keep unresolved place stops as breaks.
  const stops = props.items.filter(item => !isLocationFreeRest(item))
  if (props.connect) stops.slice(1).forEach((end, index) => {
    const start = stops[index]
    if (start.lng == null || start.lat == null || end.lng == null || end.lat == null) return
    const route = props.routes.find(r => r.origin_id === start.id && r.destination_id === end.id)
    const actual = route?.polyline?.length > 1
    overlays.push(new AMap.Polyline({ path: actual ? route.polyline : [[start.lng, start.lat], [end.lng, end.lat]], strokeColor: '#23815e', strokeWeight: 3, strokeStyle: actual ? 'solid' : 'dashed' }))
  })
  if (overlays.length) map.add(overlays)
  if (fit) fitAll()
}
async function activate(){
  if(!props.active||disposed||starting.value)return
  await nextTick()
  if(!props.active||disposed||starting.value)return
  if(map){draw();focusSelected();return}
  starting.value=true
  error.value=''
  try {
    AMap = await loadAmap()
    if (disposed||!props.active) return
    map = new AMap.Map(container.value, { zoom: 10, viewMode: '2D',resizeEnable:true })
    initialization = new AbortController()
    await waitForMapReady(map, initialization.signal)
    if (disposed) return
    ready.value = true; draw()
    focusSelected()
  } catch {
    try { map?.destroy() } catch { /* 清理失败也要显示重试入口 */ }
    map=null; ready.value=false; lastGeometry=null; focusedKey=null; overlays=[]
    if (!disposed) error.value = props.unavailableMessage
  }
  finally{initialization=null;starting.value=false}
}
onMounted(activate)
watch(()=>props.active,value=>{if(value)activate()})
onBeforeUnmount(() => { disposed = true; initialization?.abort(); map?.destroy(); map=null })
watch(() => props.items, ()=>{draw();focusSelected()}, { deep: true })
watch(() => props.routes, draw, { deep: true })
watch(() => props.connect, draw)
watch(() => props.selectedId, id => {
  draw()
  focusSelected()
})
</script>
<template><div class="map-wrap" :aria-busy="starting"><div ref="container" class="map"></div><div v-if="starting" class="map-message" role="status">正在加载地图，行程编辑不受影响…</div><div v-else-if="error" class="map-message" role="status">{{ error }} <button type="button" @click="activate">重试地图</button></div><div v-else-if="ready && !hasPoints" class="map-message empty">{{ emptyMessage }}</div><button v-if="ready && hasPoints" class="fit-button" type="button" @click="fitAll">查看全部地点</button><p v-if="ready && hasPoints && connect" class="map-note">实线：高德路线 · 虚线：地点顺序示意</p></div></template>
<style scoped>.map-wrap{position:relative;height:100%;min-height:350px;background:#e8eee8}.map{position:absolute;inset:0}.map-message{position:absolute;top:16px;left:16px;right:16px;padding:12px;background:#fffffff0;border-radius:9px;color:#5f7569;line-height:1.5;pointer-events:none}.map-message.empty{inset:0;display:grid;place-items:center;text-align:center;background:#eef3ee;border-radius:0;padding:24px}.fit-button{position:absolute;right:12px;top:12px;background:#fff;border:1px solid #d8e5db;border-radius:8px;padding:8px 10px;color:#1a6e50;font-size:12px;font-weight:700;cursor:pointer;box-shadow:0 2px 8px #183c2920}.map-note{position:absolute;bottom:12px;left:12px;background:#fffffff0;border-radius:7px;padding:7px 10px;color:#60786b;font-size:11px;pointer-events:none}.map :deep(.trip-map-marker){width:28px;height:28px;border:2px solid #fff;border-radius:50%;background:#21815b;color:#fff;font-size:12px;font-weight:700;display:grid;place-items:center;box-shadow:0 2px 8px #183c2955}.map :deep(.trip-map-marker.selected){background:#e57442;transform:scale(1.22);box-shadow:0 2px 10px #8a3f2980}.map-message button{pointer-events:auto;min-height:44px;margin-left:6px;padding:8px 12px;border:1px solid #d3e4d8;border-radius:7px;background:#fff;color:#1a6e50;cursor:pointer}
</style>
