<script setup>
/** 个人中心：资料卡（改昵称）+ 我的收藏 / 我发布的攻略 两个 tab */
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SpotCard from '../components/SpotCard.vue'
import { api } from '../api'
import { useAuth } from '../store/auth'

const router = useRouter()
const { user, openLogin } = useAuth()
const profile = ref(null)
const tab = ref('favorites') // favorites | guides
const favs = ref([])
const guides = ref([])
const loading = ref(true)
const nickname = ref('')
const nickMsg = ref('')

async function loadAll() {
  loading.value = true
  try {
    profile.value = await api.get('/api/me/profile')
    nickname.value = profile.value.nickname
    favs.value = await api.get('/api/me/favorites')
    guides.value = await api.get('/api/me/guides')
  } finally {
    loading.value = false
  }
}

async function saveNickname() {
  nickMsg.value = ''
  try {
    profile.value = await api.patch('/api/me/profile', { nickname: nickname.value.trim() })
    nickMsg.value = '已保存'
  } catch (e) {
    nickMsg.value = e.message
  }
}

onMounted(() => {
  if (!user.value) { openLogin('login'); return }
  loadAll()
})
</script>

<template>
  <div class="wrap">
    <p v-if="!user" class="empty">请先登录后访问个人中心</p>
    <p v-else-if="loading" class="loading">加载中…</p>
    <template v-else>
      <!-- 资料卡 -->
      <div class="panel profile">
        <div class="avatar">{{ profile.nickname.slice(0, 1) }}</div>
        <div class="p-info">
          <b class="p-name">{{ profile.nickname }}</b>
          <small class="p-sub">@{{ profile.username }} · {{ profile.created_at }} 注册
            <span v-if="profile.is_admin" class="adm-tag">管理员</span>
          </small>
          <div class="p-stats">
            <span>收藏 <b>{{ profile.favorite_count }}</b></span>
            <span>攻略 <b>{{ profile.guide_count }}</b></span>
          </div>
        </div>
        <div class="p-edit">
          <input v-model="nickname" maxlength="50" placeholder="修改昵称" />
          <button @click="saveNickname">保存</button>
          <span class="msg">{{ nickMsg }}</span>
        </div>
      </div>

      <!-- Tab 切换 -->
      <div class="tabs">
        <span :class="{ on: tab === 'favorites' }" @click="tab = 'favorites'">我的收藏（{{ favs.length }}）</span>
        <span :class="{ on: tab === 'guides' }" @click="tab = 'guides'">我发布的攻略（{{ guides.length }}）</span>
      </div>

      <!-- 收藏 -->
      <div v-if="tab === 'favorites'">
        <p v-if="!favs.length" class="empty">还没有收藏景点，去<a class="lk" href="/spots">列表页</a>逛逛吧</p>
        <div v-else class="grid">
          <SpotCard v-for="f in favs" :key="f.spot.id" :spot="f.spot" />
        </div>
      </div>

      <!-- 攻略 -->
      <div v-else>
        <div class="gbar">
          <span class="hint">共 {{ guides.length }} 篇（含草稿，草稿仅自己可见）</span>
          <button class="btn-write" @click="router.push('/guides/new')">✍️ 写攻略</button>
        </div>
        <p v-if="!guides.length" class="empty">还没有发布攻略，点右上角「写攻略」开始第一篇 ✍️</p>
        <div v-else class="glist">
          <div v-for="g in guides" :key="g.id" class="gitem" @click="router.push(`/guides/${g.id}`)">
            <b>
              {{ g.title }}
              <span v-if="g.is_draft" class="draft">草稿</span>
            </b>
            <small>{{ g.city || '未填城市' }} · {{ g.created_at }} · 👁 {{ g.views }}</small>
            <span class="lk" @click.stop="router.push(`/guides/${g.id}/edit`)">编辑</span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.panel { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; }
.profile { display: flex; gap: 16px; align-items: center; }
.avatar {
  width: 56px; height: 56px; border-radius: 50%;
  background: var(--green); color: #fff; font-size: 22px;
  display: flex; align-items: center; justify-content: center;
}
.p-name { font-size: 16px; }
.p-sub { display: block; color: var(--text-sub); font-size: 12px; margin: 4px 0; }
.adm-tag { color: #c0392b; border: 1px solid #e5b4b4; border-radius: 10px; padding: 0 6px; margin-left: 4px; }
.p-stats { display: flex; gap: 16px; font-size: 12.5px; color: #666; }
.p-stats b { color: var(--green); }
.p-edit { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.p-edit input { border: 1.5px solid #ccc; border-radius: 8px; padding: 7px 10px; font-size: 13px; width: 150px; }
.p-edit button { background: var(--green); color: #fff; border: none; border-radius: 8px; padding: 8px 14px; }
.msg { font-size: 12px; color: var(--green); }
.tabs { display: flex; gap: 8px; margin: 18px 0 14px; }
.tabs span {
  padding: 8px 18px; border-radius: 20px; background: #fff;
  border: 1px solid var(--line); cursor: pointer; font-size: 13.5px; color: #666;
}
.tabs span.on { background: var(--green); border-color: var(--green); color: #fff; }
.glist { display: flex; flex-direction: column; gap: 10px; }
.gitem {
  background: #fff; border: 1px solid var(--line); border-radius: 10px;
  padding: 12px 16px; cursor: pointer;
}
.gitem:hover { border-color: var(--green-border); }
.gitem small { color: var(--text-sub); margin-left: 10px; }
.gbar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.gbar .hint { font-size: 12px; color: var(--text-sub); }
.btn-write {
  border: none; border-radius: 9px; padding: 7px 15px;
  font-size: 13px; background: var(--green); color: #fff;
}
.gitem .lk { margin-left: 10px; font-size: 12.5px; }
.draft {
  font-size: 11px; color: #b7791f; background: #fdf3e2;
  border: 1px solid #f0dcb8; border-radius: 20px; padding: 1px 8px; margin-left: 6px;
}
.lk { color: var(--green); }
@media (max-width: 640px) { .p-edit { margin-left: 0; } .profile { flex-wrap: wrap; } }
</style>
