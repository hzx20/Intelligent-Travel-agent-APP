<script setup>
/** 首页 = 规划发令台。
 *  首屏是线路规划助手的入口（PlannerHero），景点内容整体下沉为次要区块：
 *  热门目的地（城市卡，点了把城市填回发令台）+ 猜你喜欢（8 条，不再翻页）。
 *  轮播已移除——5 秒自动切换抢注意力，图片内容在景点卡片里本来就有。 */
import { onMounted, ref } from 'vue'
import SpotCard from '../components/SpotCard.vue'
import PlannerHero from '../components/PlannerHero.vue'
import { api } from '../api'
import { useAuth } from '../store/auth'

const { user } = useAuth()
const heroRef = ref(null)
const cities = ref([])   // 热门目的地（按景点总数排序取前 5）
const guess = ref([])    // 猜你喜欢 8 条
const loading = ref(true)
const error = ref('')

/** 城市卡的配色循环：五种低饱和渐变，跟全站自然绿不打架 */
const TONES = ['t0', 't1', 't2', 't3', 't4']

onMounted(async () => {
  try {
    const url = user.value ? '/api/spots/for-you?page=1' : '/api/spots/for-you-guest?page=1'
    // 两个接口互不依赖，并行拿，少等一轮
    const [regions, g] = await Promise.all([api.get('/api/spots/regions'), api.get(url)])
    cities.value = (regions.cities || []).slice(0, 5)
    guess.value = (g.items || []).slice(0, 8)
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
})

/** 点城市卡：把城市填进上面的发令台，用户改完天数直接出发 */
function pickCity(c) {
  heroRef.value?.prefill(c)
  window.scrollTo({ top: 0, behavior: 'smooth' })
}
</script>

<template>
  <div class="home">
    <!-- 首屏：规划发令台 -->
    <section class="hero">
      <div class="hero-inner">
        <PlannerHero ref="heroRef" />
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
          <div><span>地图核实成线</span><small>每个地点查真，再按顺序连成路线</small></div>
        </div>
      </div>

      <p v-if="error" class="empty">
        ⚠️ 数据加载失败：{{ error }}<br />
        <small>先确认后端已启动（双击「启动网站.bat」），再刷新页面重试</small>
      </p>
      <p v-else-if="loading" class="loading">加载中…</p>
      <p v-else-if="!cities.length && !guess.length" class="empty">
        还没有景点数据。先在 backend 目录跑一次采集脚本（scripts/fetch_pois.py），再刷新本页。
      </p>

      <template v-else>
        <!-- 热门目的地：点一下就把城市填回发令台，让景点内容服务规划 -->
        <template v-if="cities.length">
          <div class="sec">热门目的地 <small>点一下，直接开始规划</small></div>
          <div class="cities">
            <button
              v-for="(c, i) in cities"
              :key="c.city"
              class="city-card"
              :class="TONES[i % TONES.length]"
              @click="pickCity(c.city)"
            >
              <span class="ghost">{{ c.city.slice(0, 1) }}</span>
              <b>{{ c.city }}</b>
              <small>{{ c.count }} 个景点</small>
              <span class="go">开始规划 →</span>
            </button>
          </div>
        </template>

        <!-- 猜你喜欢：只留 8 条，翻页交回景点列表页 -->
        <template v-if="guess.length">
          <div class="sec">
            猜你喜欢
            <small>{{ user ? `按 ${user.nickname} 的收藏偏好推荐` : '按全站收藏热度推荐 · 登录后更懂你' }}</small>
            <router-link class="more" to="/spots">更多景点 →</router-link>
          </div>
          <div class="grid">
            <SpotCard v-for="s in guess" :key="s.id" :spot="s" />
          </div>
        </template>

        <!-- 攻略：一行轻量入口，不占首页主视线 -->
        <router-link class="guide-strip" to="/guides">
          <span class="gs-title">旅游攻略</span>
          <span class="gs-sub">看看别人怎么安排，或者把你的路线写下来</span>
          <span class="gs-arrow">→</span>
        </router-link>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* 首屏：撑满一屏减去导航高度，让发令台独占第一眼 */
.hero {
  position: relative;
  display: flex;
  align-items: center;
  min-height: min(620px, calc(100vh - 56px));
  padding: 24px 0 36px;
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
