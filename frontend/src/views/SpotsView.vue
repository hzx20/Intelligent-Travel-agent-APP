<script setup>
/** 景点列表：关键词/城市/免费三筛选 + 每页 12 条真实分页（折叠式页码） */
import { computed, onMounted, ref } from 'vue'
import SpotCard from '../components/SpotCard.vue'
import { api } from '../api'
import { pageList } from '../utils/pagination'

const CITIES = ['全部城市', '成都', '杭州', '西安', '北京', '三亚']
const keyword = ref('')
const city = ref('全部城市')
const isFree = ref('all')
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 12
const pages = ref(1)
const loading = ref(true)
const error = ref('')
const searched = ref('')

async function load(p = 1) {
  loading.value = true
  try {
    const params = new URLSearchParams({
      page: p, page_size: pageSize, is_free: isFree.value,
    })
    if (city.value !== '全部城市') params.set('city', city.value)
    if (searched.value) params.set('keyword', searched.value)
    const r = await api.get(`/api/spots?${params}`)
    items.value = r.items
    total.value = r.total
    page.value = r.page
    pages.value = Math.max(1, Math.ceil(r.total / pageSize))
    error.value = ''
  } catch (e) {
    error.value = e.message || '加载失败'
    items.value = []
  } finally {
    loading.value = false
  }
}

function search() {
  searched.value = keyword.value.trim()
  load(1)
}
function goPage(n) {
  if (n < 1 || n > pages.value || n === page.value) return
  load(n)
}
const pageItems = computed(() => pageList(page.value, pages.value))
onMounted(() => load(1))
</script>

<template>
  <div class="wrap">
    <div class="filters">
      <input v-model="keyword" class="finput" placeholder="🔍 搜索景点名 / 标签 / 区域，支持多关键词" @keyup.enter="search" />
      <select v-model="city" class="fsel" @change="search">
        <option v-for="c in CITIES" :key="c" :value="c">{{ c }}</option>
      </select>
      <select v-model="isFree" class="fsel" @change="search">
        <option value="all">门票不限</option>
        <option value="free">免费</option>
        <option value="paid">收费</option>
      </select>
      <button class="btn-search" @click="search">搜索</button>
    </div>
    <div class="count">筛选结果：{{ city }} · {{ isFree === 'all' ? '门票不限' : (isFree === 'free' ? '免费' : '收费') }} —— 共 {{ total }} 条</div>

    <p v-if="error" class="empty">⚠️ 加载失败：{{ error }}<br /><small>先确认后端已启动（双击「启动网站.bat」），再刷新页面重试</small></p>
    <p v-else-if="loading" class="loading">加载中…</p>
    <p v-else-if="!items.length" class="empty">没有符合条件的景点，换个关键词试试？</p>
    <div v-else class="grid-3">
      <SpotCard v-for="s in items" :key="s.id" :spot="s" />
    </div>

    <div class="pager" v-if="pages > 1">
      <span class="pg" :class="{ disabled: page <= 1 }" @click="page > 1 && goPage(page - 1)">‹ 上一页</span>
      <template v-for="(n, i) in pageItems" :key="`${n}-${i}`">
        <span v-if="n === '...'" class="pg ellipsis">…</span>
        <span v-else class="pg" :class="{ on: n === page }" @click="goPage(n)">{{ n }}</span>
      </template>
      <span class="pg" :class="{ disabled: page >= pages }" @click="page < pages && goPage(page + 1)">下一页 ›</span>
    </div>
  </div>
</template>

<style scoped>
.filters { display: flex; gap: 10px; flex-wrap: wrap; }
.finput {
  flex: 1; min-width: 220px; border: 1.5px solid #bbb; border-radius: 10px;
  padding: 10px 12px; font-size: 13.5px; background: #fff;
}
.fsel { border: 1.5px solid #bbb; border-radius: 10px; padding: 10px; font-size: 13px; background: #fff; }
.btn-search { background: var(--green); color: #fff; border: none; border-radius: 10px; padding: 10px 20px; }
.count { font-size: 12px; color: #888; margin: 10px 0 12px; }
</style>
