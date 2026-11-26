<script setup>
import {computed,onMounted,onBeforeUnmount,ref,watch} from 'vue'
import SpotCard from '../components/SpotCard.vue'
import PlannerHero from '../components/PlannerHero.vue'
import {api} from '../api'
import {useAuth} from '../store/auth'
import {createReadResource} from '../store/readResource'
import {validRegions,validRecommendations} from '../utils/discoveryData'
const {user}=useAuth(),heroRef=ref(null)
const regions=createReadResource({read:()=>api.get('/api/spots/regions'),validate:validRegions,initial:{cities:[],total:0}})
const recommendations=createReadResource({read:()=>api.get(user.value?'/api/spots/for-you?page=1':'/api/spots/for-you-guest?page=1'),validate:validRecommendations,initial:{items:[],total:0},owner:()=>user.value?.id||0})
const regionState=regions.state,recommendationState=recommendations.state
const cities=computed(()=>regionState.data.cities.slice(0,5)),totalSpots=computed(()=>regionState.data.total||0),guess=computed(()=>recommendationState.data.items.slice(0,8))
const TONES=['t0','t1','t2','t3','t4']
onMounted(()=>regions.load())
watch(()=>user.value?.id||0,()=>{recommendations.reset();recommendations.load()},{immediate:true,flush:'sync'})
onBeforeUnmount(()=>{regions.dispose();recommendations.dispose()})
function pickCity(c){if(heroRef.value?.prefill(c)!==false)window.scrollTo({top:0,behavior:'smooth'})}
</script>

<template>
  <div class="home">
    <!-- 首屏：规划发令台 -->
    <section class="hero">
      <div class="hero-inner">
        <PlannerHero ref="heroRef" :total-spots="totalSpots" />
      </div>
    </section>

    <div class="wrap">
      <!-- 三步说明：让用户明白这不是个普通搜索框 -->
      <div class="steps">
        <div class="step">
          <b>1</b>
          <div><span>说你想要什么</span><small>目的地、天数、同行人，一句话就够</small></div>
        </div>
        <div class="step">
          <b>2</b>
          <div><span>AI 追问补齐</span><small>缺什么问什么，不用填长表单</small></div>
        </div>
        <div class="step">
          <b>3</b>
          <div><span>生成路线建议</span><small>核对地点信息，再决定如何安排行程</small></div>
        </div>
      </div>

      <section aria-labelledby="popular-title" class="discovery-section">
        <h2 id="popular-title" class="sec">热门目的地 <small>点一下，开始规划</small></h2>
        <p v-if="regionState.loading" class="loading" role="status">正在更新目的地…</p>
        <p v-if="regionState.error" class="empty source-error" role="alert">目的地暂不可用：{{ regionState.error }} <button :disabled="regionState.loading" @click="regions.retry">重试目的地</button></p>
        <div v-if="cities.length" class="cities"><button v-for="(c,i) in cities" :key="c.city" class="city-card" :class="TONES[i % TONES.length]" :aria-label="`用${c.city}开始规划`" @click="pickCity(c.city)"><span class="ghost" aria-hidden="true">{{ c.city.slice(0,1) }}</span><b>{{ c.city }}</b><small>{{ c.count }} 个景点</small><span class="go">开始规划 →</span></button></div>
        <p v-else-if="regionState.loaded && !regionState.loading && !regionState.error" class="empty">暂无目的地推荐，也可以直接输入想去的地方。</p>
      </section>
      <section aria-labelledby="recommendation-title" class="discovery-section">
        <h2 id="recommendation-title" class="sec">猜你喜欢 <small>{{ user ? `按 ${user.nickname || user.username} 的收藏偏好推荐` : '按收藏热度推荐' }}</small><router-link class="more" to="/spots">更多景点 →</router-link></h2>
        <p v-if="recommendationState.loading" class="loading" role="status">正在更新推荐…</p>
        <p v-if="recommendationState.error" class="empty source-error" role="alert">推荐暂不可用：{{ recommendationState.error }} <button :disabled="recommendationState.loading" @click="recommendations.retry">重试推荐</button></p>
        <div v-if="guess.length" class="grid"><SpotCard v-for="s in guess" :key="s.id" :spot="s" /></div>
        <p v-else-if="recommendationState.loaded && !recommendationState.loading && !recommendationState.error" class="empty">暂无推荐景点，可以去景点库发现更多地点。</p>
      </section>
      <router-link class="guide-strip" to="/guides"><span class="gs-title">旅游攻略</span><span class="gs-sub">看看别人怎么安排，或者把你的路线写下来</span><span class="gs-arrow">→</span></router-link>
    </div>
  </div>
</template>

<style scoped>
.source-error{padding:16px;text-align:left}.source-error button{min-height:44px;border:1px solid var(--green-border);border-radius:8px;background:#fff;color:var(--green);padding:8px 12px;margin-left:8px}.discovery-section{margin-top:26px}.sec{flex-wrap:wrap}.city-card:focus-visible,.source-error button:focus-visible{outline:3px solid #548b73;outline-offset:3px}

/* 首屏：不再是"硬撑满一屏"。
   以前这里是 min-height: min(620px, calc(100vh - 56px))，可内容只有 260px 高，
   1440×900 下上下各空出 150px，首屏和下面「三步说明」之间像被切断。
   现在高度跟着内容走，只保留一点呼吸位。 */
.hero {
  position: relative;
  display: flex;
  align-items: center;
  padding: 40px 0 30px;
}
/* 顶部一层极淡的绿光，把白卡片托起来 */
.hero::before {
  content: '';
  position: absolute;
  inset: 0;
  background: radial-gradient(72% 90% at 50% 0%, #e2f0e9 0%, rgba(240, 239, 235, 0) 72%);
  pointer-events: none;
}
.hero-inner {
  position: relative;
  width: 88%;
  max-width: 860px;
  margin: 0 auto;
}

/* 三步说明 */
.steps {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  /* 与首屏卡片同宽居中，形成"聚焦列 → 全宽内容"的过渡，避免宽度突然跳变 */
  max-width: 860px;
  margin: 6px auto 0;
}
.step {
  display: flex;
  gap: 11px;
  align-items: flex-start;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 14px 16px;
}
.step b {
  flex: 0 0 24px;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: var(--green-soft);
  color: var(--green);
  font-size: 12.5px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.step span { display: block; font-size: 13.5px; font-weight: 600; }
.step small { display: block; font-size: 11.5px; color: var(--text-sub); margin-top: 3px; line-height: 1.55; }

/* 热门目的地：无图渐变卡，视觉上"入口"而不是"图集" */
.cities {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 12px;
}
.city-card {
  position: relative;
  overflow: hidden;
  border: none;
  border-radius: 14px;
  padding: 16px 14px 14px;
  min-height: 108px;
  color: #fff;
  text-align: left;
  font-family: inherit;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  transition: transform .16s, box-shadow .16s;
}
.city-card:hover { transform: translateY(-3px); box-shadow: 0 10px 22px rgba(20, 60, 45, .2); }
.city-card b { font-size: 17px; font-weight: 700; letter-spacing: .5px; }
.city-card small { font-size: 11.5px; opacity: .82; }
.city-card .go { font-size: 11.5px; margin-top: 7px; opacity: .78; }
.city-card .ghost {
  position: absolute;
  right: 8px;
  top: 6px;
  font-size: 56px;
  font-weight: 700;
  line-height: 1;
  opacity: .14;
  pointer-events: none;
}
.city-card.t0 { background: linear-gradient(135deg, #2f8d6a, #186b4d); }
.city-card.t1 { background: linear-gradient(135deg, #3d7f8f, #255e6d); }
.city-card.t2 { background: linear-gradient(135deg, #788f57, #4d6535); }
.city-card.t3 { background: linear-gradient(135deg, #b98a4f, #8b6130); }
.city-card.t4 { background: linear-gradient(135deg, #6b7c90, #414f62); }

/* 攻略：一条横带 */
.guide-strip {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 26px;
  padding: 14px 18px;
  background: #fff;
  border: 1px solid var(--line);
  border-radius: 12px;
  transition: border-color .15s, box-shadow .15s;
}
.guide-strip:hover { border-color: var(--green-border); box-shadow: 0 6px 18px rgba(26, 110, 80, .07); }
.gs-title { font-size: 14px; font-weight: 700; color: var(--green); }
.gs-sub { font-size: 12.5px; color: var(--text-sub); }
.gs-arrow { margin-left: auto; color: var(--green); }

@media (max-width: 900px) {
  .cities { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 640px) {
  .hero { min-height: 0; padding: 16px 0 22px; }
  .hero-inner { width: 92%; }
  .steps { grid-template-columns: 1fr; gap: 8px; margin-top: 16px; }
  .cities { grid-template-columns: repeat(2, 1fr); gap: 10px; }
  .city-card { min-height: 96px; padding: 14px 12px 12px; }
  .city-card b { font-size: 15.5px; }
  .guide-strip { gap: 6px; padding: 12px 14px; }
  .gs-sub { font-size: 12px; }
}
</style>
