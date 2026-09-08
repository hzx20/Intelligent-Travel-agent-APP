<script setup>
/** 攻略列表（v0.8）：多关键词检索 + 城市筛选 + 最新/最热排序 + 分页 + 写攻略入口 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../store/auth'
import { pageList } from '../utils/pagination'

const CITIES = ['全部城市', '成都', '杭州', '西安', '北京', '三亚']
const router = useRouter()
const { user, openLogin } = useAuth()

const keyword = ref('')
const searched = ref('')
const city = ref('全部城市')
const sort = ref('new')
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 10
const pages = ref(1)
const loading = ref(true)

async function load(p = 1) {
  loading.value = true
  try {
    const params = new URLSearchParams({ page: p, page_size: pageSize, sort: sort.value })
    if (city.value !== '全部城市') params.set('city', city.value)
    if (searched.value) params.set('keyword', searched.value)
    const r = await api.get(`/api/guides?${params}`)
    items.value = r.items
    total.value = r.total
    page.value = r.page
    pages.value = Math.max(1, Math.ceil(r.total / pageSize))
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
function writeGuide() {
  if (!user.value) { openLogin('login'); return }
  router.push('/guides/new')
}
const pageItems = computed(() => pageList(page.value, pages.value))
onMounted(() => load(1))
</script>

<template>
  <div class="wrap">
    <div class="filters">
      <input
        v-model="keyword" class="finput"
        placeholder="🔍 多个关键词空格分隔，如：成都 亲子（标题/正文/关联景点）"
        @keyup.enter="search"
      />
      <select v-model="city" class="fsel" @change="search">
        <option v-for="c in CITIES" :key="c" :value="c">{{ c }}</option>
      </select>
      <select v-model="sort" class="fsel" @change="search">
        <option value="new">最新发布</option>
        <option value="hot">最多浏览</option>
      </select>
      <button class="btn-search" @click="search">搜索</button>
    </div>

    <div class="bar">
      <b>全部攻略 <span class="tag">{{ total }} 篇</span></b>
      <button class="btn-write" @click="writeGuide">✍️ 写攻略{{ user ? '' : '（登录后开放）' }}</button>
    </div>

    <p v-if="loading" class="loading">加载中…</p>
    <p v-else-if="!items.length" class="empty">
      {{ searched || city !== '全部城市' ? '没有符合条件的攻略，换个关键词试试' : '还没有攻略，来写第一篇吧 ✍️' }}
    </p>

    <div v-else class="aflow">
      <div v-for="g in items" :key="g.id" class="acard" @click="router.push(`/guides/${g.id}`)">
        <div
          class="cover"
          :style="g.cover_image ? { backgroundImage: `url(${g.cover_image})` } : {}"
        >
          <span v-if="!g.cover_image" class="ph-txt">封面 · 图占位</span>
        </div>
        <div class="ai">
          <b>{{ g.title }}</b>
          <small>
            {{ g.author }} · {{ g.created_at.slice(0, 10) }}
            · 👁 {{ g.views }} · 📍 关联 {{ g.spot_count }} 个
          </small>
          <p class="desc">{{ g.summary }}</p>
        </div>
      </div>
    </div>

    <div v-if="pages > 1" class="pager">
      <span class="pg" :class="{ disabled: page === 1 }" @click="goPage(page - 1)">上一页</span>
      <template v-for="(p, i) in pageItems" :key="i">
        <span v-if="p === '…'" class="pg ellipsis">…</span>
        <span v-else class="pg" :class="{ on: p === page }" @click="goPage(p)">{{ p }}</span>
      </template>
      <span class="pg" :class="{ disabled: page === pages }" @click="goPage(page + 1)">下一页</span>
    </div>
  </div>
</template>

<style scoped>
.filters { display: flex; gap: 10px; margin-bottom: 12px; }
.finput {
  flex: 1; border: 1px solid var(--line); border-radius: 10px;
  padding: 9px 12px; font-size: 13.5px; background: #fff; font-family: inherit;
}
.finput:focus { outline: none; border-color: var(--green); }
.fsel {
  border: 1px solid var(--line); border-radius: 10px; padding: 9px 10px;
  font-size: 13px; background: #fff; font-family: inherit; color: #555;
}
.btn-search, .btn-write {
  border: none; border-radius: 10px; padding: 9px 16px;
  font-size: 13.5px; background: var(--green); color: #fff; white-space: nowrap;
}
.bar {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 0 12px; font-size: 15px;
}
.bar .tag {
  font-size: 12px; font-weight: 400; color: var(--green);
  background: var(--green-soft); border: 1px solid var(--green-border);
  padding: 1px 8px; border-radius: 20px; margin-left: 4px;
}
.aflow { display: flex; flex-direction: column; gap: 12px; }
.acard {
  display: flex; gap: 14px; background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); overflow: hidden; cursor: pointer;
  transition: transform .15s, box-shadow .15s;
}
.acard:hover { transform: translateY(-2px); box-shadow: 0 6px 18px rgba(26,110,80,.12); }
.cover {
  width: 200px; min-height: 118px; flex-shrink: 0;
  background: linear-gradient(135deg, #2a8a67, #1a6e50);
  background-size: cover; background-position: center;
  display: flex; align-items: center; justify-content: center;
}
.ph-txt { color: rgba(255,255,255,.9); font-size: 12px; }
.ai { padding: 12px 14px 12px 0; display: flex; flex-direction: column; gap: 5px; }
.ai b { font-size: 15px; line-height: 1.4; }
.ai small { color: var(--text-sub); font-size: 12px; }
.desc {
  color: #666; font-size: 12.5px; line-height: 1.6;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
@media (max-width: 640px) {
  .filters { flex-wrap: wrap; }
  .finput { flex: 1 1 100%; }
  .acard { flex-direction: column; }
  .cover { width: 100%; height: 150px; min-height: 0; }
  .ai { padding: 10px 12px 12px; }
}
</style>
