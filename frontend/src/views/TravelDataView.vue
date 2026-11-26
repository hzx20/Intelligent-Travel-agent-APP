<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { api } from '../api'
import { useAuth } from '../store/auth'
import WeatherAttribution from '../components/WeatherAttribution.vue'

const { user, openLogin } = useAuth()
const status = ref(null), statusError = ref(''), statusBusy = ref(false)
const city = ref(''), weather = ref(null), weatherError = ref(''), weatherBusy = ref(false)
const now = new Date()
const date = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
const flightCities = {
  北京: 'BJS', 上海: 'SHA', 广州: 'CAN', 深圳: 'SZX', 杭州: 'HGH', 成都: 'CTU', 重庆: 'CKG',
  西安: 'SIA', 南京: 'NKG', 武汉: 'WUH', 厦门: 'XMN', 昆明: 'KMG', 合肥: 'HFE',
}
const citiesByCode = new Map(Object.entries(flightCities).map(([name, code]) => [code, name]))
const flight = reactive({ kind: 'number', date, flight_number: '', origin: '', destination: '' })
const originCode = computed(() => resolveCityCode(flight.origin))
const destinationCode = computed(() => resolveCityCode(flight.destination))
const flightResult = ref(null), flightError = ref(''), flightBusy = ref(false)
const flightBlocks = computed(() => flightResult.value?.response.texts.map(formatFlightText) ?? [])
let epoch = 0

function resolveCityCode(input) {
  const value = input.normalize('NFKC').trim()
  if (/^[A-Za-z]{3}$/.test(value)) return value.toUpperCase()
  const city = value.replace(/市$/, '')
  return Object.hasOwn(flightCities, city) ? flightCities[city] : ''
}

function cityLabel(code) {
  const city = citiesByCode.get(code)
  return city ? `${city}（${code}）` : code
}

async function loadStatus() {
  const run = ++epoch
  statusBusy.value = true; statusError.value = ''; status.value = null
  weather.value = null; flightResult.value = null
  weatherError.value = ''; flightError.value = ''
  weatherBusy.value = false; flightBusy.value = false
  try { const value = await api.get('/api/travel-data/status'); if (run === epoch) status.value = value }
  catch (error) { if (run === epoch) statusError.value = error.message }
  finally { if (run === epoch) statusBusy.value = false }
}
watch(() => user.value?.id, id => {
  epoch++; status.value = null; weather.value = null; flightResult.value = null
  statusError.value = ''; weatherError.value = ''; flightError.value = ''
  statusBusy.value = false; weatherBusy.value = false; flightBusy.value = false
  if (id) loadStatus()
}, { immediate: true })
onBeforeUnmount(() => { epoch++ })

async function queryWeather() {
  if (weatherBusy.value || !city.value.trim()) return
  const run = epoch
  weatherBusy.value = true; weatherError.value = ''; weather.value = null
  try {
    const value = await api.get(`/api/travel-data/weather?city=${encodeURIComponent(city.value.trim())}`, { timeout: 30000 })
    if (run === epoch) weather.value = value
  } catch (error) { if (run === epoch) weatherError.value = error.message }
  finally { if (run === epoch) weatherBusy.value = false }
}
async function queryFlight() {
  if (flightBusy.value) return
  flightError.value = ''; flightResult.value = null
  const byNumber = flight.kind === 'number'
  const body = {
    kind: flight.kind, date: flight.date,
    flight_number: byNumber ? flight.flight_number.trim().toUpperCase() : '',
    origin: byNumber ? '' : originCode.value,
    destination: byNumber ? '' : destinationCode.value,
  }
  if (!byNumber) {
    if (!body.origin || !body.destination) {
      flightError.value = '请选择常用城市，或填写城市 IATA 三字代码'
      return
    }
    if (body.origin === body.destination) {
      flightError.value = '出发和到达城市不能相同'
      return
    }
  }
  const run = epoch
  flightBusy.value = true
  try {
    const value = await api.post('/api/travel-data/flights/query', body, { timeout: 40000, readOnly: true })
    if (run === epoch) flightResult.value = { response: value, query: body }
  } catch (error) { if (run === epoch) flightError.value = error.message }
  finally { if (run === epoch) flightBusy.value = false }
}
function formatFlightText(text) {
  try { return { format: 'json', content: JSON.stringify(JSON.parse(text), null, 2) } }
  catch { return { format: 'text', content: text } }
}
</script>

<template>
  <main class="travel-data">
    <h1>天气与航班</h1>
    <p class="intro">出发前了解目的地天气，查询航班动态及供应商报价。</p>
    <div v-if="!user" class="panel"><p>登录后查询旅行信息。</p><button type="button" @click="openLogin('login')">登录</button></div>
    <template v-else>
      <p v-if="statusBusy" role="status">正在检查服务配置…</p>
      <p v-if="statusError" role="alert">{{ statusError }} <button type="button" @click="loadStatus">重新检查</button></p>
      <div v-if="status" class="panels">
        <section class="panel" aria-labelledby="weather-title">
          <h2 id="weather-title">目的地天气</h2>
          <p>数据提供：和风天气 · 未来 7 天预报</p>
          <p v-if="!status.qweather.configured" role="status">服务尚未配置完整，请管理员在服务端填写和风天气 key 和专属域名。</p>
          <form v-else @submit.prevent="queryWeather">
            <label>城市名称<input v-model="city" required maxlength="80" placeholder="例如：杭州" :disabled="weatherBusy" /></label>
            <button :disabled="weatherBusy || !city.trim()">{{ weatherBusy ? '查询中…' : '查询天气' }}</button>
          </form>
          <p v-if="weatherError" role="alert">{{ weatherError }}</p>
          <div v-if="weather" aria-live="polite" class="query-result">
            <h3>{{ weather.city }}</h3>
            <ul class="forecast"><li v-for="day in weather.casts" :key="day.date"><strong>{{ day.date }}</strong><span>白天 {{ day.day }} · 夜间 {{ day.night }}</span><span>{{ day.temp_low }} ~ {{ day.temp_high }} ℃</span></li></ul>
            <WeatherAttribution :attributions="weather.attributions" />
          </div>
        </section>
        <section class="panel" aria-labelledby="flight-title">
          <h2 id="flight-title">航班查询</h2>
          <p>数据提供：飞常准。每次查询可能消耗供应商额度。</p>
          <p v-if="!status.variflight.configured" role="status">服务尚未配置，请管理员在服务端填写飞常准 key。</p>
          <form v-else @submit.prevent="queryFlight">
            <fieldset :disabled="flightBusy">
              <label>查询内容<select v-model="flight.kind"><option value="number">航班动态（按航班号）</option><option value="route">城市间直飞航班</option><option value="prices">城市间航班票价</option></select></label>
              <label>出发日期<input v-model="flight.date" type="date" required /></label>
              <label v-if="flight.kind === 'number'">航班号<input v-model="flight.flight_number" required maxlength="8" pattern="[A-Za-z0-9]{2}[0-9]{3,4}[A-Za-z]?" placeholder="例如：MU2157" /></label>
              <template v-else>
                <p id="flight-city-help" class="hint">输入常用城市名称，或直接填写其他城市的 IATA 三字代码。</p>
                <label>出发城市<input v-model="flight.origin" required maxlength="20" list="flight-cities" autocomplete="off" aria-describedby="flight-city-help" placeholder="例如：上海或 SHA" /></label>
                <label>到达城市<input v-model="flight.destination" required maxlength="20" list="flight-cities" autocomplete="off" aria-describedby="flight-city-help" placeholder="例如：北京或 BJS" /></label>
                <datalist id="flight-cities"><option v-for="(code, name) in flightCities" :key="code" :value="name" :label="code" /></datalist>
                <p v-if="originCode && destinationCode && originCode !== destinationCode" class="hint city-preview">{{ cityLabel(originCode) }} → {{ cityLabel(destinationCode) }}</p>
              </template>
              <button :disabled="flightBusy">{{ flightBusy ? '查询中…' : '查询航班（使用供应商额度）' }}</button>
            </fieldset>
          </form>
          <p v-if="flightError" role="alert">{{ flightError }}</p>
          <div v-if="flightResult" aria-live="polite" class="query-result">
            <h3>航班信息</h3>
            <p v-if="flightResult.query.kind === 'number'">{{ flightResult.query.flight_number }} · {{ flightResult.query.date }}</p>
            <p v-else>{{ cityLabel(flightResult.query.origin) }} → {{ cityLabel(flightResult.query.destination) }} · {{ flightResult.query.date }} · {{ flightResult.query.kind === 'route' ? '直飞航班' : '航班票价' }}</p>
            <p>来源：飞常准 · 查询时间 {{ new Date(flightResult.response.queried_at).toLocaleString() }}</p>
            <template v-for="(block, index) in flightBlocks" :key="index">
              <pre v-if="block.format === 'json'">{{ block.content }}</pre>
              <p v-else class="supplier-text">{{ block.content }}</p>
            </template>
            <pre v-if="!flightBlocks.length && flightResult.response.structured">{{ JSON.stringify(flightResult.response.structured, null, 2) }}</pre>
            <p class="hint">动态和报价可能变化，以航空公司及实际预订渠道为准。此处暂不支持购票。</p>
          </div>
        </section>
      </div>
    </template>
  </main>
</template>

<style scoped>
.travel-data { max-width: 1100px; margin: auto; padding: 28px 18px 90px; color: #24382f; }
h1 { margin: 0 0 12px; font-size: 26px; } h2 { margin-top: 0; font-size: 21px; }
.intro, .hint { color: #5b6e64; } p { line-height: 1.7; }
.city-preview { margin: 0; }
.query-result { margin-top: 18px; padding-top: 16px; border-top: 1px solid #edf1ee; }
.query-result h3 { margin: 0 0 6px; }
.query-result p { margin: 6px 0; }
.panels { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; margin-top: 24px; }
.panel { min-width: 0; padding: 24px; border: 1px solid #dce6df; border-radius: 18px; background: #fff; }
form, fieldset { display: flex; flex-direction: column; gap: 14px; } fieldset { border: 0; padding: 0; margin: 0; min-width: 0; }
label { display: flex; flex-direction: column; gap: 7px; font-size: 14px; }
input, select { min-width: 0; width: 100%; box-sizing: border-box; padding: 11px; border-radius: 9px; border: 1px solid #bdcfc3; font: inherit; background: white; }
button { padding: 11px 15px; border: 0; border-radius: 9px; background: #1a6e50; color: white; cursor: pointer; font: inherit; }
button:disabled { opacity: .55; cursor: wait; } [role="alert"] { color: #a52e2e; overflow-wrap: anywhere; }
.forecast { padding: 0; list-style: none; } .forecast li { display: flex; flex-wrap: wrap; gap: 8px 16px; padding: 12px 0; border-bottom: 1px solid #edf1ee; }
.forecast strong { flex-basis: 100%; font-size: 14px; } .forecast span { font-size: 14px; }
.query-result :is(pre, .supplier-text) { margin: 12px 0; white-space: pre-wrap; overflow-wrap: anywhere; max-height: 480px; overflow-y: auto; padding: 14px; background: #f4f7f4; border-radius: 10px; }
.query-result pre { font-size: 13px; }
@media(max-width: 760px) { .panels { grid-template-columns: 1fr; } .panel { padding: 18px; } }
</style>
