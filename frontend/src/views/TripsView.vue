<script setup>
import {watch,ref,nextTick,onBeforeUnmount} from 'vue'
import {onBeforeRouteLeave} from 'vue-router'
import {api} from '../api'
import {useAuth} from '../store/auth'
import {guestTrips} from '../store/guestTrips'
import {registerAuthChangeGuard} from '../store/authChangeGuard'
import {createTripHome} from '../store/tripHome'

const {user,openLogin}=useAuth()
const home=createTripHome({api,guestTrips,owner:()=>user.value?.id||0,storage:()=>sessionStorage})
const {trips:tripState,history:historyState,local:localState,migrations:localMigrations,operation,recoveryError,notice,hasPending,locked}=home
const statusText={planning:'规划中',upcoming:'即将出发',completed:'已结束'}
const transferPanel=ref(null)
watch(()=>[operation.value?.source_id,operation.value?.resolved,recoveryError.value],async()=>{await nextTick();transferPanel.value?.focus({preventScroll:true});transferPanel.value?.scrollIntoView({block:'nearest'})})
const confirmLeave=()=>!hasPending.value||window.confirm('首页还有待确认的转换或复制。离开会保留本标签恢复信息，不会撤销已发请求，确定继续吗？')
const removeGuard=registerAuthChangeGuard(confirmLeave)
onBeforeRouteLeave(confirmLeave)
const beforeUnload=e=>{if(hasPending.value){e.preventDefault();e.returnValue=''}}
window.addEventListener('beforeunload',beforeUnload)
watch(()=>user.value?.id||0,()=>home.reset(),{immediate:true,flush:'sync'})
onBeforeUnmount(()=>{home.dispose();removeGuard();window.removeEventListener('beforeunload',beforeUnload)})
</script>

<template>
  <main class="trips-home">
    <section class="welcome">
      <div class="welcome-copy">
        <p class="eyebrow">YOUR NEXT ADVENTURE</p>
        <h1>把想去的地方，<br>变成下一段旅程。</h1>
        <p>收集灵感，安排每天的行程，让出发变得简单。</p>
        <div class="actions">
          <router-link class="primary" to="/trips/new">＋ 创建旅行</router-link>
          <router-link class="secondary" to="/plan">✦ AI 帮我规划</router-link>
        </div>
      </div>
      <div class="welcome-art" aria-hidden="true"><span>✳</span><div class="art-card"><small>WHERE TO NEXT?</small><b>下一站，<br>去发现世界。</b><i>↗</i></div><div class="art-stamp">TRAVEL<br>AT YOUR<br>OWN PACE</div></div>
    </section>
    <section class="content">
      <div class="section-title"><div><p class="eyebrow">MY JOURNEYS</p><h2>我的旅行</h2></div><router-link to="/trips/new">新建旅行 →</router-link></div>
      <div class="home-refresh"><span role="status" v-if="tripState.loading">正在更新账号旅行…</span><button v-if="user" :disabled="tripState.loading" @click="home.load('trips')">刷新账号旅行</button></div>
      <div v-if="!user && localState.loaded && !localState.error && !localState.items.length" class="empty-state"><div class="empty-icon">✳</div><h3>下一次旅行，从这里开始</h3><p>无需登录即可创建本机旅行；登录后可主动复制到账号。</p><router-link class="primary" to="/trips/new">创建第一段旅行</router-link></div>
      <div v-else-if="user && tripState.loaded && !tripState.error && !tripState.items.length" class="empty-state"><div class="empty-icon">✳</div><h3>账号中还没有旅行计划</h3><p>创建一段旅程，或将本机旅行、AI 历史方案复制到账号。</p><router-link class="primary" to="/trips/new">创建旅行</router-link></div>
      <div v-else-if="user && tripState.items.length" class="trip-grid">
        <router-link v-for="(trip, index) in tripState.items" :key="trip.id" :to="`/trips/${trip.id}`" class="trip-card">
          <div class="card-art" :class="`tone-${index % 3}`"><span>{{ trip.destination || '旅' }}</span><small>{{ statusText[trip.status] || trip.status }}</small><b>↗</b></div>
          <div class="card-copy"><h3>{{ trip.title }}</h3><p>{{ trip.destination || '目的地待定' }} · {{ trip.days.length }} 天</p><small>{{ trip.start_date || '出发日期待定' }} · {{ trip.days.reduce((n, d) => n + d.items.length, 0) }} 个安排</small></div>
        </router-link>
      </div>
      <p v-if="notice" class="message" role="status">{{ notice }}</p>
      <section v-if="operation || recoveryError" ref="transferPanel" tabindex="-1" class="transfer-panel" :class="{'is-confirmed':operation?.resolved}" aria-label="首页转换与复制状态" role="status">
        <template v-if="operation"><h3>{{ operation.resolved ? '账号旅行已确认' : '转换或复制待确认' }} · {{ operation.label }}</h3><p>{{ operation.kind === 'local' ? `正在使用首次提交的本机版本 ${operation.body.guest_version}，本机原件会保留。` : `正在使用首次选择的历史版本 ${operation.body.version}，原方案会保留。` }}</p><p v-if="operation.busy">正在确认，请稍候…</p><p v-if="operation.error">{{ operation.error }}</p><div class="transfer-actions"><router-link v-if="operation.resolved" class="primary" :to="`/trips/${operation.trip.id}`">打开已确认旅行 →</router-link><template v-else><button :disabled="operation.busy" @click="home.check">核对账号结果</button><button :disabled="operation.busy" @click="home.retry">继续原次操作</button></template></div></template>
        <p v-if="recoveryError" role="alert">{{ recoveryError }}</p><p v-if="!operation?.resolved">仅在本标签保留恢复信息；关闭标签或清除浏览器数据可能失去入口。确认离开不会撤销已发请求。</p><button v-if="!operation?.resolved" :disabled="operation?.busy" @click="home.discard">放弃本标签恢复</button>
      </section>
      <p v-if="tripState.error" class="message error" role="alert">账号旅行加载失败：{{ tripState.error }} <button :disabled="tripState.loading" @click="home.load('trips')">重试账号旅行</button></p>
      <div v-if="localState.items.length" class="local-section"><div class="section-title"><div><p class="eyebrow">ON THIS DEVICE</p><h2>本机旅行</h2></div><button :disabled="localState.loading" @click="home.load('local')">刷新本机旅行</button></div><p class="local-hint">仅保存在此浏览器，清除浏览器数据会丢失。{{ user ? '复制到账号时会一并复制预订记录及订单号，本机原件仍会保留。' : '登录后可主动复制到账号。' }} <button v-if="!user" @click="openLogin('login')">登录</button></p><div class="trip-grid"><div v-for="(trip, index) in localState.items" :key="trip.id" class="trip-card local-card"><router-link :to="`/trips/${trip.id}`"><div class="card-art" :class="`tone-${index % 3}`"><span>{{ trip.destination || '旅' }}</span><small>本机 · {{ statusText[trip.status] }}</small><b>↗</b></div><div class="card-copy"><h3>{{ trip.title }}</h3><p>{{ trip.destination || '目的地待定' }} · {{ trip.days.length }} 天</p><small>{{ trip.start_date || '出发日期待定' }} · {{ trip.days.reduce((n, d) => n + d.items.length, 0) }} 个安排</small></div></router-link><div v-if="user" class="local-actions"><router-link v-if="localMigrations[trip.id]" :to="`/trips/${localMigrations[trip.id].server_trip_id}`">查看已复制版本 →</router-link><button v-else :disabled="locked" @click="home.begin('local',trip)">复制到账号</button></div></div></div></div>
      <p v-if="localState.loading" class="message" role="status">正在更新本机旅行…</p><p v-if="localState.error" class="message error" role="alert">{{ localState.error }} <button :disabled="localState.loading" @click="home.load('local')">重试本机旅行</button></p>
      <p v-if="historyState.loading" class="message" role="status">正在更新历史方案…</p><p v-if="historyState.error" class="message error" role="alert">AI 历史方案加载失败：{{ historyState.error }} <button :disabled="historyState.loading" @click="home.load('history')">重试历史方案</button></p>
      <div v-if="user && (historyState.items.length || historyState.loaded)" class="history"><div class="section-title"><div><p class="eyebrow">YOUR AI PLANS</p><h2>已有 AI 方案</h2></div><div class="history-tools"><button :disabled="historyState.loading" @click="home.load('history')">刷新历史方案</button><router-link to="/plan">查看规划助手 →</router-link></div></div><p>把历史方案转为旅行后，就能继续编辑每天的安排。原方案仍会保留。</p><div v-for="plan in historyState.items" :key="plan.id" class="history-row"><div><b>{{ plan.title || `${plan.city}旅行` }}</b><small>{{ plan.city }} · {{ plan.days }} 天 · {{ plan.created_at }}</small></div><router-link v-if="tripState.items.find(t => t.source_plan_id === plan.id)" :to="`/trips/${tripState.items.find(t => t.source_plan_id === plan.id).id}`">打开旅行 →</router-link><button v-else :disabled="locked || Boolean(tripState.error)" @click="home.begin('plan',plan)">转为旅行</button></div></div>
      <div class="explore-strip"><div><p class="eyebrow">A LITTLE INSPIRATION</p><h2>还没决定去哪里？</h2><p>探索景点与旅行攻略，为下一次出发收集灵感。</p></div><router-link class="secondary" to="/spots">探索地点 →</router-link></div>
    </section>
  </main>
</template>

<style scoped>
.transfer-panel:focus{outline:2px solid #76a58f;outline-offset:3px}.transfer-panel.is-confirmed{border-color:#bed7c6;background:#f1f8f2}.history .section-title>div:first-child{flex-shrink:0}@media(max-width:700px){.history .section-title{flex-wrap:wrap;align-items:flex-start}.history .section-title h2{white-space:nowrap}}
.home-refresh,.history-tools,.transfer-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.home-refresh{justify-content:flex-end;min-height:44px;margin-bottom:12px;font-size:12px;color:#667c70}.trips-home button{min-height:44px;cursor:pointer}.home-refresh button,.section-title button,.transfer-panel button{padding:8px 12px;border:1px solid #d7e1d8;border-radius:8px;background:white;color:#1a6e50;font:inherit}.transfer-panel{margin:24px 0;padding:20px;border:1px solid #e1d2a9;border-radius:14px;background:#fffaec;overflow-wrap:anywhere}.transfer-panel h3{font-size:16px}.transfer-panel p{font-size:13px;line-height:1.7;margin:10px 0;color:#6f613f}.transfer-actions{margin:12px 0}.trips-home button:disabled{opacity:.5;cursor:default}.history-row>div:first-child,.card-copy{min-width:0;overflow-wrap:anywhere}.history-row{flex-wrap:wrap}.trip-card:focus-visible,.trips-home button:focus-visible,.trips-home a:focus-visible{outline:3px solid #487e69;outline-offset:3px}.local-card>a{display:block}

.local-section{margin-top:42px}.local-hint{color:#7a8d82;font-size:12px;line-height:1.7;margin:-8px 0 16px}.local-hint button,.local-actions button,.local-actions a{border:0;background:none;color:#1a6e50;font-weight:700;font-size:12px}.local-actions{padding:0 18px 16px}.local-actions button:disabled{opacity:.5}
.trips-home{color:#203b35}.welcome{background:#e7eee7;min-height:340px;display:flex;align-items:center;justify-content:space-between;gap:40px;padding:55px max(6%,calc((100% - 1120px)/2));overflow:hidden}.welcome-copy{position:relative;z-index:1}.eyebrow{color:#69877b;font-size:11px;font-weight:700;letter-spacing:.16em}.welcome h1{font-size:clamp(32px,4vw,48px);line-height:1.3;letter-spacing:.02em;margin:15px 0}.welcome-copy>p:not(.eyebrow){color:#63776d;font-size:15px;line-height:1.7}.actions{display:flex;gap:10px;margin-top:25px;flex-wrap:wrap}.primary,.secondary{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 20px;border-radius:9px;font-size:14px;font-weight:700;border:1px solid #1a6e50}.primary{background:#1a6e50;color:white}.secondary{background:#fff;color:#1a6e50}.welcome-art{width:340px;height:220px;position:relative;flex:none}.welcome-art>span{position:absolute;font-size:116px;right:0;top:-35px;color:#b9d5c3}.art-card{position:absolute;inset:14px 45px 5px 20px;background:#f8f6ed;border:1px solid #dbe2d6;border-radius:12px;transform:rotate(-5deg);padding:25px;box-shadow:0 15px 35px #52715c20}.art-card small{letter-spacing:.13em;color:#779385;font-size:10px}.art-card b{display:block;font-size:27px;line-height:1.35;margin-top:24px}.art-card i{position:absolute;right:24px;bottom:20px;font-size:27px;font-style:normal}.art-stamp{position:absolute;right:-5px;bottom:0;background:#dfac70;border-radius:50%;width:90px;height:90px;display:grid;place-content:center;text-align:center;font-size:9px;letter-spacing:.08em;line-height:1.4;transform:rotate(12deg);color:#fff}.content{max-width:1120px;margin:auto;padding:44px 0 70px}.section-title{display:flex;justify-content:space-between;align-items:end;gap:15px;margin-bottom:20px}.section-title h2,.explore-strip h2{font-size:23px;margin-top:7px}.section-title>a{font-size:13px;color:#1a6e50;font-weight:700}.empty-state{background:#fff;border:1px solid #e4ebe5;border-radius:16px;padding:38px;text-align:center}.empty-icon{font-size:34px;color:#a4c6b2}.empty-state h3{font-size:17px;margin:10px}.empty-state p,.history>p,.explore-strip p:not(.eyebrow){color:#73847b;line-height:1.7;margin:8px 0 18px}.empty-state button{cursor:pointer}.trip-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:18px}.trip-card{background:white;border:1px solid #e4ebe5;border-radius:14px;overflow:hidden;transition:transform .15s}.trip-card:hover{transform:translateY(-3px)}.card-art{height:142px;padding:18px;position:relative;background:#dbe8dd;overflow:hidden}.tone-1{background:#f0e1cf}.tone-2{background:#dce7e9}.card-art>span{position:absolute;font-size:98px;font-weight:700;opacity:.13;right:8px;bottom:-18px}.card-art small{background:#ffffffc9;border-radius:20px;padding:5px 10px}.card-art b{position:absolute;bottom:16px;left:18px;font-size:23px}.card-copy{padding:18px}.card-copy h3{font-size:17px}.card-copy p{margin:8px 0;color:#5f7468}.card-copy small{color:#87968d}.history{margin-top:52px}.history-row{display:flex;justify-content:space-between;align-items:center;gap:12px;border-bottom:1px solid #e1e7e1;padding:15px 2px}.history-row small{display:block;color:#829088;margin-top:5px}.history-row button,.history-row a{border:0;background:none;color:#1a6e50;font-weight:700;white-space:nowrap}.history-row button:disabled{opacity:.5}.explore-strip{margin-top:55px;padding:26px 30px;border-radius:15px;background:#f1eadc;display:flex;align-items:center;justify-content:space-between;gap:20px}.explore-strip p:not(.eyebrow){margin-bottom:0}.message{padding:25px;color:#6a7b72}.error{color:#a53d37}.error button{border:0;background:none;text-decoration:underline}.content{width:88%}@media(max-width:700px){.welcome{padding:38px 6% 42px;min-height:0}.welcome h1{font-size:31px}.welcome-art{display:none}.content{width:90%;padding-top:34px}.trip-grid{grid-template-columns:1fr 1fr;gap:10px}.card-art{height:115px}.card-copy{padding:14px}.card-copy h3{font-size:15px}.explore-strip{align-items:start;flex-direction:column}.history-row{align-items:flex-start}.history-row small{line-height:1.5}}@media(max-width:380px){.trip-grid{grid-template-columns:1fr}}
</style>
