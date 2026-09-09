<script setup>
/** 首页：轮播（热门取图）+ 热门景区推荐 + 猜你喜欢（12 条/页 × 3 页真实翻页） */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import SpotCard from '../components/SpotCard.vue'
import { api, getToken } from '../api'
import { useAuth } from '../store/auth'

const { user } = useAuth()
const loading = ref(true)
const carousel = ref([])   // 轮播帧（取热门前三张有图）
const hot = ref([])        // 热门 4 张
const guess = ref([])      // 当前页猜你喜欢
const guessPage = ref(1)
const guessTotal = ref(0)
const guessPages = computed(() => Math.max(1, Math.ceil(guessTotal.value / 12)))
const cur = ref(0)
const error = ref('')     // 后端不可达时给个明白话，别让用户对着白屏猜
let timer = null

async function loadGuess(p) {
  const url = user.value
    ? `/api/spots/for-you?page=${p}`
    : `/api/spots/for-you-guest?page=${p}`
  const r = await api.get(url)
  guess.value = r.items
  guessTotal.value = r.total
  guessPage.value = r.page
}

async function guessGo(n) {
  if (n < 1 || n > guessPages.value) return
  await loadGuess(n)
}

onMounted(async () => {
  try {
    const hots = await api.get('/api/spots/hot')
    hot.value = hots
    carousel.value = hots.filter((s) => s.image_url).slice(0, 3)
    if (!carousel.value.length && hots.length) carousel.value = [hots[0]]
    await loadGuess(1)
    timer = setInterval(() => {
      if (carousel.value.length > 1) cur.value = (cur.value + 1) % carousel.value.length
    }, 5000)
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
})
onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="wrap" v-if="!loading">
    <!-- 轮播 -->
    <div class="carousel">
      <div class="frame" :style="carousel.length ? { backgroundImage: `url(${carousel[cur].image_url})` } : {}">
        <div class="cap" v-if="carousel.length">{{ carousel[cur].name }} · 旅行从这里开始</div>
      </div>
      <div class="dots">
        <span v-for="(f, i) in carousel" :key="i" class="dot" :class="{ on: i === cur }" @click="cur = i" />
      </div>
    </div>

    <!-- 热门景区推荐 -->
    <div class="sec">热门景区推荐 <small>全站收藏热度 Top4</small>
      <router-link class="more" to="/spots">查看全部景点 →</router-link>
    </div>
    <div class="grid">
      <SpotCard v-for="s in hot" :key="s.id" :spot="s" />
    </div>

    <!-- 猜你喜欢 -->
    <div class="sec">猜你喜欢
      <small>{{ user ? `按 ${user.nickname} 的收藏偏好推荐` : '按全站收藏热度推荐 · 登录后更懂你' }}</small>
    </div>
    <div class="grid">
      <SpotCard v-for="s in guess" :key="s.id" :spot="s" />
    </div>
    <div class="pager" v-if="guessPages > 1">
      <span class="pg" :class="{ on: guessPage === 1 }" @click="guessGo(1)">1</span>
      <span v-if="guessPages >= 2" class="pg" :class="{ on: guessPage === 2 }" @click="guessGo(2)">2</span>
      <span v-if="guessPages >= 3" class="pg" :class="{ on: guessPage === 3 }" @click="guessGo(3)">3</span>
      <span class="pg" @click="guessGo(guessPage + 1)">下一页 ›</span>
    </div>
  </div>
  <p v-else-if="error" class="empty">⚠️ 数据加载失败：{{ error }}<br /><small>先确认后端已启动（双击「启动网站.bat」），再刷新页面重试</small></p>
  <p v-else class="loading">加载中…</p>
</template>
