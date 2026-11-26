<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
const props = defineProps({
  city: { type: String, default: '' },
  busy: Boolean,
  actionLabel: { type: String, default: '＋ 收集' },
  collapseOnSelect: Boolean,
  candidates: { type: Array, default: () => [] },
  nearbyAnchors: { type: Array, default: () => [] },
})
const emit = defineEmits(['add'])
const keyword = ref(''), searchCity = ref(props.city), results = ref([]), resultType = ref(''), searching = ref(false), searched = ref(false), adding = ref(false), error = ref('')
const checking = ref(null)
const nearbyAnchorId = ref(null)
const nearbyAnchor = computed(() => props.nearbyAnchors.find(item => item.id === nearbyAnchorId.value) || props.nearbyAnchors[0])
let searchGeneration = 0, addGeneration = 0
function invalidate() {
  searchGeneration++; addGeneration++; searching.value = false; adding.value = false
  results.value = []; resultType.value = ''; searched.value = false; error.value = ''
  checking.value = null
}
watch(() => props.city, value => { searchCity.value = value; invalidate() }, { flush: 'sync' })
watch([keyword, searchCity], invalidate, { flush: 'sync' })
watch(() => {
  const anchor = nearbyAnchor.value
  return anchor && JSON.stringify([anchor.id, anchor.lng, anchor.lat, anchor.amap_id])
}, invalidate, { flush: 'sync' })
onBeforeUnmount(() => { searchGeneration++; addGeneration++ })
function sameCandidate(spot, candidate) {
  const key = value => String(value || '').normalize('NFKC').toLowerCase().replace(/[\s()·\-–—]+/g, '')
  return candidate?.address && key(spot.name) === key(candidate.name) && key(spot.address) === key(candidate.address)
}
function searchCandidate(candidate) {
  if (props.busy || searching.value) return
  if (props.city.trim()) searchCity.value = props.city
  keyword.value = candidate.name
  search('map', candidate)
}
async function search(type, candidate = null, category = null) {
  const query = keyword.value.trim(), city = searchCity.value.trim()
  const anchor = nearbyAnchor.value
  if (props.busy || searching.value || (type === 'nearby' ? !anchor : !query)) return
  if (type !== 'nearby' && query.length > 80) { error.value = '地点名称过长，请缩短后搜索。'; return }
  if (type === 'map' && !city) { error.value = '搜索高德地图时，请先填写城市。'; return }
  const run = ++searchGeneration
  checking.value = candidate
  addGeneration++; adding.value = false
  searching.value = true; error.value = ''; searched.value = false; results.value = []
  try {
    const response = type === 'local'
      ? await api.get(`/api/spots?${new URLSearchParams({ keyword: query, city, page_size: '20' })}`, { timeout: 10000 })
      : await api.post('/api/map/place-search', type === 'nearby'
        ? { category, origin: { lng: anchor.lng, lat: anchor.lat, amap_id: anchor.amap_id || '' } }
        : { keyword: query, city }, { timeout: 10000 })
    if (run !== searchGeneration) return
    if (!Array.isArray(response?.items)) throw new Error('地点结果暂不可用，请重试。')
    results.value = response.items
    if (candidate) results.value = [...results.value].sort((a, b) => Number(Boolean(sameCandidate(b, candidate))) - Number(Boolean(sameCandidate(a, candidate))))
    resultType.value = type; searched.value = true
  } catch (e) { if (run === searchGeneration) error.value = e.message }
  finally { if (run === searchGeneration) searching.value = false }
}
async function add(spot) {
  if (adding.value || props.busy) return
  if (resultType.value !== 'local') {
    emit('add', { name: spot.name, address: spot.address, lng: spot.lng, lat: spot.lat,
                  amap_id: spot.amap_id, source: spot.source, note: '' })
    if (props.collapseOnSelect) invalidate()
    return
  }
  const run = ++addGeneration
  adding.value = true; error.value = ''
  try {
    const detail = await api.get(`/api/spots/${spot.id}`, { timeout: 10000 })
    if (run !== addGeneration) return
    emit('add', { name: spot.name, address: detail.address || '', lng: spot.lng, lat: spot.lat,
                  amap_id: spot.amap_id || '', source: detail.source || '景点库', note: '' })
    if (props.collapseOnSelect) invalidate()
  } catch (e) { if (run === addGeneration) error.value = e.message }
  finally { if (run === addGeneration) adding.value = false }
}
</script>
<template>
  <div class="place-search">
    <div v-if="candidates.length" class="saved-candidates">
      <b>规划留下的候选地点</b>
      <p class="hint">选一个馆区到高德核对，再从结果中关联并保存。</p>
      <div v-for="(candidate, index) in candidates" :key="index" class="result">
        <div><b>{{ candidate.name }}</b><small>{{ candidate.address || candidate.district }}</small></div>
        <button type="button" :disabled="busy || searching" :aria-label="`核对 ${candidate.name}`" @click="searchCandidate(candidate)">在高德核对</button>
      </div>
    </div>
    <div class="search-row"><input v-model="keyword" maxlength="80" placeholder="输入地点名称" aria-label="地点名称" @keydown.enter.prevent="search('local')"><input v-model="searchCity" maxlength="50" class="city" placeholder="城市" aria-label="搜索城市" @keydown.enter.prevent="search('local')"></div>
    <div class="search-actions"><button type="button" :disabled="searching || !keyword.trim()" @click="search('local')">{{ searching ? '搜索中…' : '搜索景点库' }}</button><button type="button" :disabled="searching || !keyword.trim()" @click="search('map')">搜索高德地图</button></div>
    <p class="hint">高德搜索会发送地点名称和城市；请选择正确的候选地点。没有找到时也可手动添加。</p>
    <details v-if="nearbyAnchor" class="nearby-search">
      <summary>按当天地点查附近</summary>
      <label>参考地点 <select aria-label="附近搜索的参考地点" :value="nearbyAnchor.id" :disabled="busy" @change="nearbyAnchorId = Number($event.target.value)">
        <option v-for="item in nearbyAnchors" :key="item.id" :value="item.id">{{ item.name }}</option>
      </select></label>
      <div class="search-actions"><button type="button" :disabled="busy || searching" @click="search('nearby', null, 'food')">查附近餐厅</button><button type="button" :disabled="busy || searching" @click="search('nearby', null, 'museum')">查附近展馆</button></div>
      <p class="hint">点击查询会将参考地点的坐标发送给高德。范围约 1.5 公里，结果需另查营业、费用及实际路线。</p>
    </details>
    <p v-if="searching" class="hint" role="status">正在查找地点…</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-else-if="searched && !results.length" class="hint">{{ resultType === 'nearby' ? '这个地点附近没有查到候选，可换一个参考地点或按名称搜索。' : '没有找到匹配地点，试试其他关键词、城市或手动添加。' }}</p>
    <div v-for="spot in results" :key="resultType === 'local' ? spot.id : spot.amap_id" class="result"><div><b>{{ spot.name }}</b><small>{{ spot.city }} {{ spot.district }}<template v-if="spot.address"> · {{ spot.address }}</template> · {{ resultType === 'local' ? '景点库' : '高德地图' }}</small><small v-if="resultType === 'nearby' && Number.isInteger(spot.straight_line_m)">距参考点直线约 {{ spot.straight_line_m }} 米</small><small v-if="checking && sameCandidate(spot, checking)" class="same-address">与候选名称及地址一致</small></div><button type="button" :disabled="busy || adding || searching" @click="add(spot)">{{ adding ? '正在获取…' : actionLabel }}</button></div>
  </div>
</template>
<style scoped>
.saved-candidates { margin-bottom: 12px; }
.saved-candidates .result { align-items: center; }
.saved-candidates .result > div { min-width: 0; overflow-wrap: anywhere; }
.result .same-address { color: #1a6e50; font-weight: 600; }
.nearby-search { margin-top: 10px; border-top: 1px solid #e0ebe2; padding-top: 8px; font-size: 13px; }
.nearby-search summary { cursor: pointer; color: #1a6e50; padding: 6px 0; font-weight: 600; }
.nearby-search label { display: block; margin-top: 5px; }
.nearby-search select { max-width: 100%; margin: 4px 0; border: 1px solid #dbe6dd; border-radius: 7px; padding: 6px 8px; color: #294a3b; background: white; }
.result > div { min-width: 0; overflow-wrap: anywhere; }
</style>
<style scoped>.place-search{background:#f5f8f4;border-radius:10px;padding:13px;margin-bottom:15px}.search-row,.search-actions{display:flex;gap:8px}.search-actions{margin-top:8px;flex-wrap:wrap}.place-search input{min-width:0;flex:1;padding:10px;border:1px solid #dbe6dd;border-radius:7px;background:white}.place-search input.city{flex:0 1 120px}.place-search button{border:0;background:#e0eee4;color:#1a6e50;border-radius:7px;padding:8px 12px;font-weight:700;white-space:nowrap}.place-search button:disabled{opacity:.5}.hint{font-size:11px;color:#87998d;margin:8px 0 0;line-height:1.5}.error{color:#a33;font-size:12px;margin-top:8px}.result{display:flex;justify-content:space-between;gap:10px;border-top:1px solid #e0ebe2;padding:10px 0}.result:first-of-type{margin-top:8px}.result b,.result small{display:block}.result small{margin-top:4px;font-size:11px;color:#899b90;line-height:1.5}.result button{padding:7px 10px}</style>
