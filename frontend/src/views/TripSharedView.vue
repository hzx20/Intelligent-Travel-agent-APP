<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../store/auth'
import { shareError } from '../utils/shareErrors'
import { validShareToken, validSharedJourney, validInvitationReceipt, validInvitationStatus } from '../utils/sharedJourney'
import { registerAuthChangeGuard } from '../store/authChangeGuard'

const props = defineProps({ mode: { type: String, required: true } })
const route = useRoute(), router = useRouter(), { user, openLogin } = useAuth()
const token = computed(() => route.hash.slice(1))
const trip = ref(null), loading = ref(false), error = ref(''), retryable = ref(false)
const pending=ref(null), receipt=ref(null), checked=ref(false), checking=ref(false), message=ref('')
const tokenValid=computed(()=>validShareToken(token.value))
const accepting=computed(()=>props.mode==='edit'&&(loading.value||checking.value))
const needsReview=computed(()=>Boolean(pending.value))
let generation = 0, unregisterAuthGuard=null
const scope=()=>({run:generation,token:token.value,uid:user.value?.id,mode:props.mode})
const current=s=>s.run===generation&&s.token===token.value&&s.uid===user.value?.id&&s.mode===props.mode
function clear(){generation++;trip.value=null;loading.value=false;checking.value=false;error.value='';retryable.value=false;pending.value=null;receipt.value=null;checked.value=false;message.value=''}

async function load() {
  if(loading.value)return
  ++generation
  const s=scope()
  error.value = ''; retryable.value = false
  if (!tokenValid.value) { trip.value=null;error.value = '链接不完整，请向分享者重新获取。'; return }
  if (props.mode !== 'view') return
  loading.value = true
  try {
    const result = await api.post('/api/trip-sharing/view', { token: s.token }, {timeout: 10000})
    if(!current(s))return
    if(!validSharedJourney(result))throw Error('返回的分享内容不完整，请重试。')
    trip.value = result
  } catch (e) { if (current(s)) {
    if([404,422].includes(e.status))trip.value=null
    const notice=shareError(e);error.value=notice.message;retryable.value=notice.retryable
  } }
  finally { if (current(s)) loading.value = false }
}
watch(() => [props.mode, token.value,user.value?.id],()=>{clear();load()}, { immediate: true,flush:'sync' })
function confirmDeparture(){return !needsReview.value||window.confirm('邀请结果尚未核对。离开或切换账号不会撤销已提交的加入请求；之后可用原账号和邀请链接核对。确定继续？')}
function beforeUnload(event){if(needsReview.value){event.preventDefault();event.returnValue=''}}
onBeforeRouteLeave(confirmDeparture)
onBeforeRouteUpdate((to,from)=>to.path===from.path&&to.hash===from.hash||confirmDeparture())
onMounted(()=>{unregisterAuthGuard=registerAuthChangeGuard(confirmDeparture);window.addEventListener('beforeunload',beforeUnload)})
onBeforeUnmount(() => { generation++;unregisterAuthGuard?.();window.removeEventListener('beforeunload',beforeUnload) })
async function accept() {
  if (!user.value) { openLogin('login'); return }
  if (props.mode!=='edit'||!tokenValid.value||accepting.value||receipt.value||error.value&&!retryable.value||pending.value&&!checked.value) return
  const s=scope()
  pending.value={uid:s.uid,token:s.token};checked.value=false
  loading.value = true; error.value = ''; retryable.value = false;message.value=''
  try {
    const result = await api.post('/api/trip-sharing/accept', { token: s.token }, {timeout: 15000})
    if (!current(s)) return
    if(!validInvitationReceipt(result))throw Error('无法确认加入回执')
    receipt.value=result;pending.value=null
    await router.replace(`/trips/${result.trip_id}`)
  } catch (e) { if (current(s)) {
    if(receipt.value){error.value='已确认加入，暂时无法打开旅行，请使用下方入口。';return}
    const notice=shareError(e,true);error.value=notice.message;retryable.value=notice.retryable
    if([404,422].includes(e.status))pending.value=null
  } }
  finally { if (current(s)) loading.value = false }
}
async function checkMembership(){
  if(!user.value){openLogin('login');return}
  if(props.mode!=='edit'||!tokenValid.value||accepting.value)return
  const s=scope();checking.value=true;checked.value=false;error.value='';message.value=''
  try{
    const result=await api.post('/api/trip-sharing/invitation-status',{token:s.token},{timeout:10000})
    if(!current(s))return
    if(!validInvitationStatus(result))throw Error('无法确认核对回执')
    if(result.state==='joined'){receipt.value=result;pending.value=null;message.value='已确认当前账号可以编辑这份旅行。核对没有提交加入请求。'}
    else {receipt.value=null;checked.value=true;message.value='当前账号尚未加入。核对没有提交加入请求；你可以自行确认是否继续。'}
  }catch(e){if(current(s)){
    const unsupported=[405,501].includes(e.status)||e.status===404&&e.message==='Not Found'
    if(!unsupported&&[404,422].includes(e.status)){receipt.value=null;pending.value=null;error.value=shareError(e,true).message;retryable.value=false}
    else {error.value=unsupported?'当前服务暂不支持核对，请更新服务后再确认。':'暂时无法核对加入状态，请稍后重试；没有提交新的加入请求。';retryable.value=true}
  }}finally{if(current(s))checking.value=false}
}
function jumpDay(number){const el=document.getElementById(`shared-day-${number}`);el?.focus({preventScroll:true});el?.scrollIntoView({behavior:'smooth',block:'start'})}
function dayDate(day) {
  if (!trip.value?.start_date) return `第 ${day.day_number} 天`
  const value = new Date(`${trip.value.start_date}T12:00:00`)
  value.setDate(value.getDate() + day.day_number - 1)
  return `${value.getMonth() + 1}月${value.getDate()}日`
}
</script>
<template>
  <main class="shared"><router-link class="back" to="/">← 旅行首页</router-link>
    <div v-if="error" class="state error" role="alert">{{ error }} <button v-if="mode==='view' && retryable" type="button" :disabled="loading" @click="load">重试</button><p v-if="trip">以下为上次成功读取的内容，暂未确认最新状态。</p></div>
    <section v-if="mode === 'edit'" class="invite"><p class="eyebrow">TRAVEL TOGETHER</p><h1>你收到一份同行邀请</h1><p>接受后，你可以查看和编辑这份旅行的安排、待安排地点、旅行账本及预订记录。请确认这是你希望加入的旅行。链接可能已过期或被撤销。</p><p v-if="!user">请先登录，再自行确认是否加入。</p><p v-else>将以「{{ user.nickname || user.username || '当前账号' }}」加入。</p>
      <div v-if="message" class="result" role="status">{{ message }}</div>
      <div v-if="receipt" class="result"><router-link class="primary" :to="`/trips/${receipt.trip_id}`">打开已确认旅行 →</router-link><button type="button" :disabled="accepting" @click="checkMembership">重新核对权限</button></div>
      <template v-else><p v-if="needsReview" class="review-note" role="status">加入请求的结果尚待确认。先核对当前账号是否已加入，再决定是否继续；重试不会切换账号。</p><div class="invite-actions"><button v-if="!needsReview || checked" class="primary" :disabled="accepting || !tokenValid || Boolean(error && !retryable)" @click="accept">{{ loading ? '正在加入…' : (user ? (needsReview ? '确认继续加入' : '接受邀请并加入') : '登录后继续') }}</button><button v-if="user && tokenValid" type="button" :disabled="accepting" @click="checkMembership">{{ checking ? '正在核对…' : '核对是否已加入' }}</button></div></template>
    </section>
    <template v-else><p v-if="loading" class="state" role="status">正在打开分享的行程…</p>
    <template v-if="trip"><header><p class="eyebrow">A SHARED JOURNEY · 只读</p><h1>{{ trip.title }}</h1><p>{{ trip.destination || '目的地待定' }} · {{ trip.start_date || '日期待定' }} · {{ trip.days.length }} 天</p><button class="refresh" type="button" :disabled="loading" @click="load">刷新分享行程</button><nav class="day-links" aria-label="分享行程日期"><button v-for="day in trip.days" :key="day.day_number" type="button" @click="jumpDay(day.day_number)">第 {{ day.day_number }} 天</button></nav></header><section v-for="day in trip.days" :key="day.day_number" :id="`shared-day-${day.day_number}`" tabindex="-1" class="day"><div class="day-header"><b>DAY {{ day.day_number }}</b><span>{{ dayDate(day) }} {{ day.theme ? `· ${day.theme}` : '' }}</span></div><p v-if="!day.items.length" class="empty">这一天还没有安排。</p><ol v-else><li v-for="(item, index) in day.items" :key="index"><span class="index">{{ index + 1 }}</span><div><p class="time">{{ item.start_time || '时间待定' }} · {{ item.kind }}</p><h2>{{ item.name }}</h2><p v-if="item.address">{{ item.address }}</p><p v-if="item.duration_min != null">停留 {{ item.duration_min }} 分钟</p><p v-if="item.activity">{{ item.activity }}</p><p v-if="item.note" class="note">{{ item.note }}</p></div></li></ol></section></template></template>
  </main>
</template>
<style scoped>
.shared{max-width:760px;width:90%;margin:32px auto 70px;color:#203b35}.back{color:#6c8276;font-size:13px}.shared header{margin:34px 0 24px}.eyebrow{font-size:10px;color:#789487;letter-spacing:.14em;font-weight:bold}.shared h1{font-size:30px;margin:9px 0}.shared header>p:last-child,.invite p:not(.eyebrow){color:#788c80;line-height:1.7}.state,.invite,.day{background:#fff;border:1px solid #e1e9e2;border-radius:14px;padding:24px;margin-top:20px}.invite h1{font-size:26px}.invite button{margin-top:18px;border:0;border-radius:8px;background:#1a6e50;color:#fff;font-weight:bold;padding:12px 16px}.day-header{display:flex;align-items:center;gap:15px;color:#1a6e50}.day-header span{color:#8b9b91;font-size:12px}.day ol{list-style:none;margin-top:18px}.day li{display:flex;gap:14px;padding:10px 0;border-top:1px solid #edf1ed}.index{flex:none;background:#e2f0e5;color:#1a6e50;width:26px;height:26px;border-radius:50%;display:grid;place-content:center;font-weight:bold}.day h2{font-size:16px;margin:6px 0}.day li p,.empty{font-size:12px;color:#788c80;line-height:1.7;white-space:pre-wrap}.day li .time{color:#1a6e50;font-weight:bold}.note{padding:8px;background:#f6f8f3;border-radius:6px;margin-top:8px}@media(max-width:600px){.shared h1{font-size:25px}.state,.invite,.day{padding:18px}}
</style>
<style scoped>
.shared{overflow-wrap:anywhere}.shared h1{line-height:1.3}.invite-actions,.result{display:flex;flex-wrap:wrap;gap:10px;align-items:center}.result{margin-top:16px;font-size:13px;line-height:1.7}.invite button,.shared .primary,.refresh,.error button{min-height:44px;padding:10px 15px;border:1px solid #cfdfd4;border-radius:8px;background:white;color:#1a6e50;font:inherit;font-size:13px;cursor:pointer}.invite .primary{background:#1a6e50;color:white;border-color:#1a6e50}.invite button:disabled,.refresh:disabled{opacity:.5;cursor:wait}.review-note,.error p{font-size:13px;line-height:1.7;margin-top:12px}.error{color:#a33}.refresh{margin-top:14px}.day-links{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.day-links button{display:grid;place-items:center;min-height:44px;padding:8px 14px;border:1px solid #dce7df;border-radius:8px;background:#fff;color:#1a6e50}.day{scroll-margin-top:76px}.day:focus-visible,.shared button:focus-visible,.shared a:focus-visible{outline:2px solid #4e936d;outline-offset:3px}.day-header{flex-wrap:wrap}.day li>div{min-width:0}.invite-actions button{margin-top:12px}@media(max-width:600px){.shared{width:92%;margin-bottom:95px}.invite h1{font-size:24px}}
</style>
