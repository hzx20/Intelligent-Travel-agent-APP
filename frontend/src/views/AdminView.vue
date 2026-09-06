<script setup>
/** 管理后台：侧边栏模块切换（用户/景点/收藏/评论/攻略/AI 记录） */
import { onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { useAuth } from '../store/auth'

const { user, openLogin } = useAuth()
const MODULES = [
  { key: 'users', label: '用户管理' },
  { key: 'spots', label: '旅游景点管理' },
  { key: 'favorites', label: '景点收藏记录' },
  { key: 'comments', label: '景点评论审核' },
  { key: 'guides', label: '旅游攻略管理' },
  { key: 'aiplans', label: 'AI 规划记录' },
]
const active = ref('users')
const loading = ref(false)
const error = ref('')
const keyword = ref('')
const page = ref(1)
const pages = ref(1)
const total = ref(0)
const rows = ref([])

async function load(p = 1) {
  if (!user.value?.is_admin) return
  loading.value = true
  error.value = ''
  try {
    page.value = p
    const params = new URLSearchParams({ page: p, page_size: 20 })
    if (keyword.value.trim() && ['users', 'spots', 'guides'].includes(active.value)) {
      params.set('keyword', keyword.value.trim())
    }
    const r = await api.get(`/api/admin/${active.value}?${params}`)
    rows.value = r.items ?? r
    total.value = r.total ?? r.length
    pages.value = Math.max(1, Math.ceil(total.value / 20))
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function toggleActive(u) {
  await api.patch(`/api/admin/users/${u.id}?is_active=${!u.is_active}`, {})
  await load(page.value)
}

async function toggleFree(s) {
  await api.patch(`/api/admin/spots/${s.id}`, { is_free: !s.is_free })
  await load(page.value)
}

async function delComment(c) {
  if (!confirm('确定删除该评论（软删除）？')) return
  await api.raw('DELETE', `/api/admin/comments/${c.id}`)
  await load(page.value)
}

onMounted(() => {
  if (!user.value) { openLogin('login'); return }
  if (!user.value.is_admin) { error.value = '当前账户不是管理员'; return }
  load()
})
</script>

<template>
  <div class="wrap">
    <p v-if="!user" class="empty">请先登录</p>
    <p v-else-if="error" class="empty">{{ error }}（管理员账户可用脚本 tools/make_admin.py 开通）</p>
    <template v-else>
      <div class="sec">管理员后台 <small>8 个管理模块（攻略图片与关联景点并入攻略管理查看）</small></div>
      <div class="layout">
        <aside class="side">
          <span v-for="m in MODULES" :key="m.key" class="side-item" :class="{ on: active === m.key }"
                @click="active = m.key; load(1)">{{ m.label }}</span>
        </aside>

        <section class="main-panel">
          <div class="bar">
            <input v-if="['users','spots','guides'].includes(active)" v-model="keyword"
                   class="kinput" placeholder="按关键词搜索…" @keyup.enter="load(1)" />
            <button class="btn-s" @click="load(1)">刷新</button>
            <span class="total">共 {{ total }} 条 · 第 {{ page }}/{{ pages }} 页</span>
          </div>
          <p v-if="loading" class="loading">加载中…</p>
          <p v-else-if="!rows.length" class="empty">暂无数据</p>

          <!-- 用户管理 -->
          <table v-if="active === 'users' && rows.length" class="tbl">
            <thead><tr><th>ID</th><th>用户名</th><th>昵称</th><th>注册时间</th><th>收藏数</th><th>状态</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="u in rows" :key="u.id">
                <td>{{ u.id }}</td><td>{{ u.username }}</td><td>{{ u.nickname }}</td>
                <td>{{ u.created_at }}</td><td>{{ u.favorite_count }}</td>
                <td><span :class="u.is_active ? 'ok' : 'bad'">{{ u.is_active ? '正常' : '停用' }}</span>{{ u.is_admin ? ' · 管理员' : '' }}</td>
                <td><button class="mini" @click="toggleActive(u)">{{ u.is_active ? '停用' : '启用' }}</button></td>
              </tr>
            </tbody>
          </table>

          <!-- 景点管理 -->
          <table v-if="active === 'spots' && rows.length" class="tbl">
            <thead><tr><th>ID</th><th>名称</th><th>城市</th><th>标签</th><th>免费</th><th>收藏数</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="s in rows" :key="s.id">
                <td>{{ s.id }}</td><td>{{ s.name }}</td><td>{{ s.city }}</td>
                <td class="clip">{{ s.tags }}</td>
                <td><span :class="s.is_free ? 'ok' : ''">{{ s.is_free ? '免费' : (s.price ? `¥${s.price}` : '收费') }}</span></td>
                <td>{{ s.favorite_count }}</td>
                <td><button class="mini" @click="toggleFree(s)">{{ s.is_free ? '改为收费' : '改为免费' }}</button></td>
              </tr>
            </tbody>
          </table>

          <!-- 收藏记录 -->
          <table v-if="active === 'favorites' && rows.length" class="tbl">
            <thead><tr><th>ID</th><th>用户</th><th>景点</th><th>收藏时间</th></tr></thead>
            <tbody><tr v-for="f in rows" :key="f.id"><td>{{ f.id }}</td><td>{{ f.username }}</td><td>{{ f.spot }}</td><td>{{ f.created_at }}</td></tr></tbody>
          </table>

          <!-- 评论审核 -->
          <table v-if="active === 'comments' && rows.length" class="tbl">
            <thead><tr><th>ID</th><th>用户</th><th>景点</th><th>内容</th><th>评分</th><th>时间</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="c in rows" :key="c.id">
                <td>{{ c.id }}</td><td>{{ c.username }}</td><td>{{ c.spot }}</td>
                <td class="clip">{{ c.content }}</td><td>{{ c.rating ? '★'.repeat(c.rating) : '-' }}</td><td>{{ c.created_at }}</td>
                <td><button class="mini danger" @click="delComment(c)">删除</button></td>
              </tr>
            </tbody>
          </table>

          <!-- 攻略管理 -->
          <table v-if="active === 'guides' && rows.length" class="tbl">
            <thead><tr><th>ID</th><th>标题</th><th>作者</th><th>城市</th><th>图片数</th><th>关联景点</th><th>时间</th></tr></thead>
            <tbody>
              <tr v-for="g in rows" :key="g.id">
                <td>{{ g.id }}</td><td>{{ g.title }}</td><td>{{ g.author }}</td><td>{{ g.city }}</td>
                <td>{{ g.images.length }}</td><td>{{ g.spots.map(s => s.name).join('、') || '—' }}</td><td>{{ g.created_at }}</td>
              </tr>
            </tbody>
          </table>

          <!-- AI 记录 -->
          <table v-if="active === 'aiplans' && rows.length" class="tbl">
            <thead><tr><th>ID</th><th>用户</th><th>会话</th><th>状态</th><th>意图（城市）</th><th>时间</th></tr></thead>
            <tbody><tr v-for="p in rows" :key="p.id"><td>{{ p.id }}</td><td>{{ p.username }}</td><td>{{ p.session_id }}</td><td>{{ p.status }}</td><td>{{ p.intent }}</td><td>{{ p.created_at }}</td></tr></tbody>
          </table>

          <div class="pager" v-if="pages > 1">
            <span class="pg" @click="load(page - 1)">‹ 上一页</span>
            <span v-for="n in pages" :key="n" class="pg" :class="{ on: n === page }" @click="load(n)">{{ n }}</span>
            <span class="pg" @click="load(page + 1)">下一页 ›</span>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
.layout { display: flex; gap: 14px; align-items: flex-start; }
.side { display: flex; flex-direction: column; gap: 6px; min-width: 150px; }
.side-item {
  background: #fff; border: 1px solid var(--line); border-radius: 10px;
  padding: 10px 14px; font-size: 13.5px; color: #555; cursor: pointer;
}
.side-item.on { background: var(--green); border-color: var(--green); color: #fff; }
.main-panel { flex: 1; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; min-width: 0; }
.bar { display: flex; gap: 10px; align-items: center; margin-bottom: 10px; }
.kinput { flex: 0 1 240px; border: 1.5px solid #ccc; border-radius: 8px; padding: 7px 10px; font-size: 13px; }
.btn-s { background: var(--green); color: #fff; border: none; border-radius: 8px; padding: 8px 16px; }
.total { margin-left: auto; font-size: 12px; color: #888; }
.tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.tbl th, .tbl td { text-align: left; padding: 8px 8px; border-bottom: 1px solid var(--line); }
.tbl th { color: #888; font-weight: 600; background: #fafaf7; }
.clip { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ok { color: var(--green); }
.bad { color: #c0392b; }
.mini { border: 1px solid var(--green-border); background: var(--green-soft); color: var(--green); border-radius: 6px; padding: 3px 10px; font-size: 12px; }
.mini.danger { background: #fdeeee; border-color: #e5b4b4; color: #c0392b; }
@media (max-width: 640px) {
  .layout { flex-direction: column; }
  .side { flex-direction: row; flex-wrap: wrap; min-width: 0; }
}
</style>
