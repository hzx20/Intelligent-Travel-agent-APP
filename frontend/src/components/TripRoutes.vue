<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { hasRouteCoordinates, isLocationFreeRest, isRouteMode, routeLegKey, routePairs, routePoint, summarizeAdoptedRoutes, summarizeRoutes, walkingLimitState } from '../utils/tripRouteSummary'
const props = defineProps({
  tripId: { type: [Number, String], required: true },
  tripVersion: { type: Number, required: true },
  day: { type: Object, required: true },
  walkingPreferences: { type: Object, default: () => ({}) },
  local: Boolean,
  disabled: Boolean,
})
const emit = defineEmits(['routes', 'preference'])
const mode = ref('walking'), records = ref({}), loading = ref(false), info = ref('')
const now = ref(Date.now())
let generation = 0, expiryTimer = null, lastTripId = props.tripId
const modes = [{ value: 'walking', label: '步行' }, { value: 'driving', label: '驾车' }]
const hasBreaks = computed(() => props.day.items.some(isLocationFreeRest))
const pairKey = (kind, start, end) => JSON.stringify([kind, start.id, end.id, routePoint(start), routePoint(end)])
const canChoose = computed(() => props.local || !!props.day.route_preferences)
const pairs = computed(() => routePairs(props.day.items).map(({ start, end }) => {
  const id = routeLegKey(start, end)
  const selection = props.day.route_preferences?.[id]
  return { start, end, id, selection: isRouteMode(selection) ? selection : null, key: pairKey(mode.value, start, end) }
}))
const hasSelection = computed(() => pairs.value.some(pair => pair.selection))
const readyPair = ({ start, end }) => hasRouteCoordinates(start) && hasRouteCoordinates(end)
function fresh(result) { return result?.status === 'ok' && Number.isFinite(Date.parse(result.queried_at)) && now.value - Date.parse(result.queried_at) < 15 * 60 * 1000 }
const adopted = computed(() => pairs.value.filter(pair => pair.selection)
  .map(({ start, end, selection }) => records.value[pairKey(selection, start, end)]).filter(fresh))
const usable = computed(() => hasSelection.value ? adopted.value : routesFor(mode.value).map(route => ({ ...route, preview: true })))
const adoptedSummary = computed(() => summarizeAdoptedRoutes(pairs.value.length, adopted.value))
const routesFor = kind => pairs.value
  .map(({ start, end }) => records.value[pairKey(kind, start, end)])
  .filter(fresh)
const totals = computed(() => modes.map(kind => ({
  ...kind,
  ...summarizeRoutes(pairs.value.length, routesFor(kind.value)),
})).filter(row => row.count))
const walkingStatus = computed(() => {
  const { total, count, walkingDistance } = adoptedSummary.value
  const summary = hasSelection.value ? { total, count, distance: walkingDistance } : totals.value.find(row => row.value === 'walking')
  return walkingLimitState(summary, props.walkingPreferences.daily_limit_m)
})
const climbSegments = computed(() => (hasSelection.value ? adopted.value.filter(route => route.mode === 'walking') : routesFor('walking'))
  .filter(route => walkingFeatures(route)).length)
const showWalkingPreferences = computed(() => props.walkingPreferences.prefer_short_walks || walkingStatus.value || props.walkingPreferences.check_climbs)
watch(usable, result => emit('routes', result), { immediate: true })
watch(() => [props.tripId, props.tripVersion, props.day.id, pairs.value.map(pair => pair.key).join(';')], () => {
  generation++; loading.value = false; info.value = ''
  if (props.tripId !== lastTripId) { records.value = {}; lastTripId = props.tripId; return }
  const keep = new Set(pairs.value.flatMap(({ start, end }) => modes.map(kind => pairKey(kind.value, start, end))))
  records.value = Object.fromEntries(Object.entries(records.value).filter(([key]) => keep.has(key)))
}, { flush: 'sync' })
onMounted(() => { expiryTimer = window.setInterval(() => { now.value = Date.now() }, 30000) })
onBeforeUnmount(() => { generation++; window.clearInterval(expiryTimer) })
function label(kind) { return kind === 'walking' ? '步行' : '驾车' }
function savedResult(pair) { return records.value[pairKey(pair.selection, pair.start, pair.end)] }
function setChoice(pair, event) {
  const value = event.target.value || null
  event.target.value = pair.selection || ''
  if (value !== pair.selection) emit('preference', { origin_id: pair.start.id, destination_id: pair.end.id, mode: value })
}
async function query(selectedOnly = false) {
  const run = ++generation
  const version = props.tripVersion
  now.value = Date.now()
  const snapshot = pairs.value.map(pair => {
    const kind = selectedOnly ? pair.selection : mode.value
    return { ...pair, kind, key: pairKey(kind, pair.start, pair.end) }
  })
  loading.value = true; info.value = ''
  // Process new pairs first so a repeatedly failing leg cannot starve later ones.
  const pending = snapshot.filter(p => p.kind && readyPair(p) && !fresh(records.value[p.key]))
    .sort((a, b) => Number(Boolean(records.value[a.key])) - Number(Boolean(records.value[b.key])))
  for (const pair of pending.slice(0, 20)) {
    if (generation !== run) return
    try {
      const params = new URLSearchParams({ origin_id: pair.start.id, destination_id: pair.end.id, mode: pair.kind, version })
      const point = item => ({lng:item.lng, lat:item.lat, amap_id:item.amap_id || ''})
      const data = props.local
        ? await api.post('/api/map/guest-route', {origin:point(pair.start), destination:point(pair.end), mode:pair.kind}, {timeout:12000})
        : await api.get(`/api/trips/${props.tripId}/days/${props.day.id}/route?${params}`, { timeout: 12000 })
      const result = props.local ? {...data, origin_id:pair.start.id, destination_id:pair.end.id, mode:pair.kind, trip_version:version} : data
      if (generation !== run) return
      records.value = { ...records.value, [pair.key]: result.trip_version === version ? result : {status:'unavailable', message:'行程已更新，请刷新后重新查询'} }
    } catch (error) {
      if (generation !== run) return
      records.value = { ...records.value, [pair.key]: { status: 'unavailable', message: [404, 409].includes(error.status) ? '行程已更新，请刷新后重试' : error.status === 429 ? '路线查询较频繁，请稍后重试' : '路线查询失败，请稍后重试' } }
    }
  }
  if (generation !== run) return
  if (pending.length > 20) info.value = '每次最多查询 20 段，点击继续查询剩余路段。'
  else if (!pending.length) info.value = '当前没有需要查询的新路段。'
  loading.value = false
}
function duration(seconds) { return Math.ceil(seconds / 60) }
function distance(meters) { return meters >= 1000 ? `${(meters / 1000).toFixed(1)} 公里` : `${meters} 米` }
function walkingFeatures(result) {
  const tags = result?.walking_features
  if (!Array.isArray(tags)) return ''
  return [tags.includes('stairs') ? '台阶' : '', tags.includes('slope') ? '斜坡' : ''].filter(Boolean).join('、')
}
</script>
<template>
  <section class="routes"><div class="heading"><div><h2>地点间交通</h2><p>逐段选择步行或驾车，也可先查询备选路线</p></div><label>比较方式 <select v-model="mode" :disabled="loading"><option value="walking">步行</option><option value="driving">驾车</option></select></label></div><p v-if="local" class="hint">主动查询时，会将相邻地点的坐标和地点标识发送至服务器及高德地图；路线仅供参考。</p>
    <p v-if="!pairs.length" class="empty">至少添加两个安排后，可查询地点间交通。</p>
    <template v-else>
      <aside v-if="showWalkingPreferences" class="walking-review" aria-label="步行偏好核对">
        <b>步行偏好 · {{ hasSelection ? '已采用路线' : '全程步行比较' }}</b>
        <p v-if="walkingPreferences.prefer_short_walks">尽量少走路</p>
        <p v-if="walkingStatus">每天地点间步行上限 {{ distance(walkingPreferences.daily_limit_m) }}</p>
        <p v-if="walkingStatus === 'exceeded'" class="walking-alert">{{ hasSelection ? '已采用的步行小计' : '全程步行的已查小计' }}已超上限，可比较驾车或调整地点。</p>
        <p v-else-if="walkingStatus === 'within_segments'">地点间步行小计在上限内，场馆内和上下车接驳步行另计。</p>
        <p v-else-if="walkingStatus === 'pending'">{{ hasSelection ? '仍有未选择或未查到的路段，暂无法核对上限。' : '步行路段尚未查齐，暂无法核对上限。' }}</p>
        <p v-if="walkingPreferences.check_climbs && climbSegments" class="walking-alert">{{ climbSegments }} 段已查步行路线有台阶或斜坡标注。</p>
        <p v-else-if="walkingPreferences.check_climbs">台阶和斜坡情况以路线标注和现场为准。</p>
      </aside>
      <div class="route-actions">
        <button class="query" :disabled="loading || disabled" @click="query(false)">{{ loading ? '正在查询…' : '查询 / 重试交通' }}</button>
        <button v-if="hasSelection" class="query" :disabled="loading || disabled" @click="query(true)">查询已采用路线</button>
      </div>
      <p v-if="info" class="hint" role="status">{{ info }}</p>
      <div v-if="hasSelection" class="route-totals" aria-label="已采用交通小计" aria-live="polite">
        <p><b>已采用路线已查 {{ adoptedSummary.count }} / {{ adoptedSummary.total }} 段</b><span v-if="adoptedSummary.count"> · 约 {{ duration(adoptedSummary.seconds) }} 分钟</span></p>
        <p v-if="adoptedSummary.walkingCount">已查步行 {{ distance(adoptedSummary.walkingDistance) }} · 约 {{ duration(adoptedSummary.walkingSeconds) }} 分钟</p>
        <p v-else-if="adoptedSummary.count === adoptedSummary.total">已查路线中无地点间步行段。</p>
        <p class="hint">仅汇总已查到的已采用路线。场馆内和上下车接驳步行另计。</p>
      </div>
      <div v-if="totals.length" class="route-totals comparisons" aria-label="备选交通小计">
        <b>同一方式比较</b>
        <p v-for="row in totals" :key="row.value"><b>{{ row.label }}已查 {{ row.count }} / {{ row.total }} 段</b> · {{ distance(row.distance) }} · 约 {{ duration(row.seconds) }} 分钟</p>
        <p class="hint">此处按统一方式比较各段。逐段选择后，按已采用的小计核对行程。</p>
      </div>
      <p v-if="hasBreaks" class="hint">无地点的休息仍保留在日程中，路线连接休息前后的地点。</p>
      <ol class="legs">
        <li v-for="pair in pairs" :key="pair.id">
          <div class="leg-heading"><div><b>{{ pair.start.name }}</b><span> → </span><b>{{ pair.end.name }}</b></div>
            <label v-if="canChoose && readyPair(pair)">本段方式
              <select :aria-label="`${pair.start.name}至${pair.end.name}的交通方式`" :value="pair.selection || ''" :disabled="disabled || loading" @change="setChoice(pair, $event)">
                <option value="">未选择</option><option value="walking">步行</option><option value="driving">驾车</option>
              </select>
            </label>
          </div>
          <small v-if="!readyPair(pair)">地点缺少坐标，暂不能查询或选择</small>
          <template v-else>
            <small v-if="pair.selection" class="leg-selection">已采用{{ label(pair.selection) }}<template v-if="fresh(savedResult(pair))"> · 约 {{ duration(savedResult(pair).duration_s) }} 分钟 · {{ distance(savedResult(pair).distance_m) }} · 高德地图参考</template><template v-else> · {{ savedResult(pair)?.status === 'unavailable' ? savedResult(pair).message : '耗时待查询' }}</template></small>
            <small v-if="pair.selection === 'walking' && fresh(savedResult(pair)) && walkingFeatures(savedResult(pair))" class="terrain-note">已采用路线高德标注：{{ walkingFeatures(savedResult(pair)) }}</small>
            <template v-if="mode !== pair.selection">
              <small v-if="fresh(records[pair.key])">备选{{ label(mode) }}约 {{ duration(records[pair.key].duration_s) }} 分钟 · {{ distance(records[pair.key].distance_m) }} · 高德地图参考{{ records[pair.key].cached ? '（缓存）' : '' }} · 查询于 {{ new Date(records[pair.key].queried_at).toLocaleString() }}</small>
              <small v-if="mode === 'walking' && fresh(records[pair.key]) && walkingFeatures(records[pair.key])" class="terrain-note">高德标注：{{ walkingFeatures(records[pair.key]) }}</small>
              <small v-if="records[pair.key]?.status === 'unavailable'">{{ records[pair.key].message }}</small>
              <small v-else-if="records[pair.key]?.status === 'ok' && !fresh(records[pair.key])">备选路线已过期，请重新查询</small>
              <small v-else-if="!records[pair.key] && !pair.selection">尚未查询或选择交通方式</small>
            </template>
          </template>
        </li>
      </ol>
      <p class="hint">{{ hasSelection ? '地图实线为已采用且有路径的路线。' : '未选择交通方式时，地图实线预览备选路线。' }}耗时会随实际情况变化；坡度、台阶和通行条件请结合现场核对。</p>
    </template>
  </section>
</template>
<style scoped>
.walking-review { margin-top: 14px; padding: 10px 13px; border: 1px solid #dce8df; border-radius: 8px; font-size: 12px; line-height: 1.6; }
.walking-alert { color: #805715; font-weight: 600; }
.route-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.leg-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.leg-heading select { border: 1px solid #dce7de; border-radius: 7px; padding: 6px; background: #fff; color: #325b47; }
.legs .leg-selection { color: #1a6e50; font-weight: 600; }
.route-totals.comparisons { background: #f5f7f5; }
</style>
<style scoped>.route-totals{margin-top:14px;padding:11px 13px;border-radius:8px;background:#f0f6f1;font-size:12px;line-height:1.65}.legs .terrain-note{color:#805715}.routes{border-top:1px solid #edf1ed;padding:24px}.heading{display:flex;justify-content:space-between;align-items:start;gap:10px}.heading h2{font-size:19px}.heading p,.hint,.empty{font-size:12px;color:#82948a;line-height:1.6;margin-top:7px}.heading label{font-size:12px;color:#71867a;white-space:nowrap}.heading select{border:1px solid #dce7de;border-radius:7px;padding:7px;background:#fff;color:#325b47}.query{margin-top:14px;padding:9px 13px;border:0;border-radius:8px;background:#e7f2ea;color:#1a6e50;font-weight:bold}.query:disabled{opacity:.6}.legs{list-style:none;margin-top:12px}.legs li{border-top:1px solid #edf1ed;padding:10px 0;font-size:12px}.legs li span{color:#a1b3a6}.legs small{display:block;color:#718b7a;margin-top:5px;line-height:1.5}@media(max-width:760px){.routes{padding:20px 16px}}</style>
