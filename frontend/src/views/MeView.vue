<script setup>
/** 个人中心：资料卡（改昵称）+ 我的收藏 / 我发布的攻略 两个 tab */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import SpotCard from '../components/SpotCard.vue'
import { api } from '../api'
import { useAuth } from '../store/auth'
import { registerAuthChangeGuard } from '../store/authChangeGuard'
import { guardTripUnload } from '../utils/tripDraftGuard'

const router = useRouter()
const route = useRoute()
const requestedTab = () => route.query.tab === 'guides' ? 'guides' : 'favorites'
const { user, openLogin, acceptProfile } = useAuth()
const profile = ref(null)
const tab = ref('favorites') // favorites | guides
const favs = ref([])
const guides = ref([])
const nickname = ref('')
const nickMsg = ref(''), nickError = ref(''), saving = ref(false), pendingWrite = ref(null), conflict = ref(false)
const baseNickname = ref(''), baseVersion = ref(null)
const profileState = reactive({loading:false,error:''}), favoriteState = reactive({loading:false,error:''}), guideState = reactive({loading:false,error:''})
const hasDraft = computed(() => Boolean(pendingWrite.value || nickname.value !== baseNickname.value))
let generation = 0, unregisterAuthGuard = null
const sequence = {profile:0,favorites:0,guides:0}
const active = (run,uid) => run === generation && uid === user.value?.id
function confirmDeparture() { return !(hasDraft.value || saving.value) || window.confirm('昵称还有未保存的输入或待确认的保存。离开后本页输入会丢失，确定离开吗？') }
function beforeUnload(event) { guardTripUnload(event,hasDraft.value,saving.value) }
onBeforeRouteLeave(confirmDeparture)
onMounted(() => { window.addEventListener('beforeunload',beforeUnload); unregisterAuthGuard = registerAuthChangeGuard(confirmDeparture) })
onBeforeUnmount(() => { generation++; window.removeEventListener('beforeunload',beforeUnload); unregisterAuthGuard?.() })
function validProfile(data,uid) { return data?.id === uid && Number.isSafeInteger(data.version) && data.version > 0 && typeof data.nickname === 'string' && typeof data.avatar === 'string' }
async function loadProfile(mode = 'check') {
  const run = generation, uid = user.value?.id, request = ++sequence.profile
  if (!uid || saving.value) return
  profileState.loading = true; profileState.error = ''
  try {
    const data = await api.get('/api/me/profile')
    if (!active(run,uid) || request !== sequence.profile) return
    if (!validProfile(data,uid)) throw new Error('无法读取支持版本保护的个人资料，请更新服务后重试。')
    if (data.version < Math.max(profile.value?.version || 0,user.value?.profile_version || 0)) {
      if (!profile.value) throw new Error('资料已更新，请重试读取最新资料。')
      nickMsg.value = '已忽略较旧的资料，当前输入仍保留。'; return
    }
    const adopt = mode === 'adopt' || !profile.value
    profile.value = data; acceptProfile(data)
    if (adopt) {
      nickname.value = data.nickname; baseNickname.value = data.nickname; baseVersion.value = data.version
      pendingWrite.value = null; conflict.value = false; nickMsg.value = ''; nickError.value = ''
    } else { nickError.value = ''; nickMsg.value = '已读取云端昵称，本页输入和原保存版本仍保留。' }
  } catch (e) { if (active(run,uid) && request === sequence.profile) profileState.error = e.message }
  finally { if (active(run,uid) && request === sequence.profile) profileState.loading = false }
}
async function loadList(kind) {
  const run = generation, uid = user.value?.id, request = ++sequence[kind]
  if (!uid) return
  const state = kind === 'favorites' ? favoriteState : guideState
  state.loading = true; state.error = ''
  try {
    const data = await api.get(`/api/me/${kind}`)
    if (!active(run,uid) || request !== sequence[kind]) return
    if (!Array.isArray(data)) throw new Error('无法读取列表，请稍后重试。')
    if (kind === 'favorites') favs.value = data
    else guides.value = data
  } catch (e) { if (active(run,uid) && request === sequence[kind]) state.error = e.message }
  finally { if (active(run,uid) && request === sequence[kind]) state.loading = false }
}

async function saveNickname() {
  if (saving.value || profileState.loading || !profile.value || conflict.value) return
  const raw = nickname.value
  const body = pendingWrite.value || Object.freeze({nickname:raw.trim(),version:baseVersion.value})
  if (!body.nickname || body.nickname.length > 50) { nickError.value = '请填写1到50字的昵称。'; return }
  const run = generation, uid = user.value?.id
  saving.value = true; nickMsg.value = ''; nickError.value = ''
  try {
    const data = await api.patch('/api/me/profile/edit',body)
    if (!active(run,uid)) return
    if (!validProfile(data,uid) || data.version !== body.version + 1 || data.nickname !== body.nickname || data.version < profile.value.version) {
      const error = new Error('昵称保存结果暂时无法确认，请先核对云端昵称。'); error.outcomeUnknown = true; throw error
    }
    sequence.profile++; profileState.loading = false
    profile.value = data; baseNickname.value = data.nickname; baseVersion.value = data.version
    if (nickname.value === raw) nickname.value = data.nickname
    pendingWrite.value = null; conflict.value = false; acceptProfile(data); nickMsg.value = '昵称已保存。'
  } catch (e) {
    if (!active(run,uid)) return
    nickError.value = [404,405,428].includes(e.status) ? '当前服务无法安全保存资料，请重新加载确认账号或更新服务后再试。' : e.message
    if (e.outcomeUnknown || e.code?.startsWith('SESSION_')) pendingWrite.value = body
    conflict.value = e.status === 409
  } finally { if (active(run,uid)) saving.value = false }
}
async function adoptCloudNickname() {
  if (saving.value || profileState.loading || !window.confirm('重新载入会用云端昵称替换本页输入。确定继续吗？')) return
  await loadProfile('adopt')
}
watch(() => user.value?.id,uid => {
  generation++; for (const key of Object.keys(sequence)) sequence[key]++
  profile.value = null; favs.value = []; guides.value = []; nickname.value = ''; baseNickname.value = ''; baseVersion.value = null
  saving.value = false; pendingWrite.value = null; conflict.value = false; nickMsg.value = ''; nickError.value = ''; tab.value = requestedTab()
  for (const state of [profileState,favoriteState,guideState]) { state.loading = false; state.error = '' }
  if (uid) { loadProfile('initial'); loadList('favorites'); loadList('guides') }
}, {immediate:true,flush:'sync'})
watch(() => route.query.tab,() => { tab.value = requestedTab() })
</script>

<template>
  <div class="wrap">
    <p v-if="!user" class="empty">请先登录后访问个人中心 <button @click="openLogin('login')">登录</button></p>
    <template v-else>
      <section class="profile-area" aria-label="个人资料">
      <p v-if="profileState.error" class="panel-error" role="alert">{{ profileState.error }} <button :disabled="profileState.loading || saving" @click="loadProfile()">重试资料</button></p>
      <p v-if="profileState.loading && !profile" class="loading">正在加载个人资料…</p>
      <!-- 资料卡 -->
      <div v-if="profile" class="panel profile">
        <div class="avatar">{{ (profile.nickname || profile.username).slice(0, 1) }}</div>
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
          <label>昵称<input v-model="nickname" :readonly="Boolean(pendingWrite) || conflict" :disabled="saving || profileState.loading" maxlength="50" placeholder="修改昵称" /></label>
          <button :disabled="saving || profileState.loading || Boolean(pendingWrite) || conflict || !hasDraft" @click="saveNickname">{{ saving ? '保存中…' : '保存昵称' }}</button>
        </div>
      </div>
      <div v-if="profile" class="profile-tools">
        <p v-if="nickError" role="alert">{{ nickError }}</p><p v-if="nickMsg" class="msg" role="status">{{ nickMsg }}</p>
        <p v-if="pendingWrite" class="hint">本次保存结果待核对，重试沿用原昵称和原版本。刷新或离开不会自动保存本页输入。</p>
        <button :disabled="saving || profileState.loading" @click="loadProfile()">核对云端昵称</button>
        <button v-if="pendingWrite" :disabled="saving || profileState.loading || conflict" @click="saveNickname">继续原次保存</button>
        <button v-if="hasDraft || conflict" :disabled="saving || profileState.loading" @click="adoptCloudNickname">重新载入云端昵称</button>
      </div>
      </section>

      <!-- Tab 切换 -->
      <router-link to="/footprints" class="footprints-link">查看我的旅行足迹 →</router-link>
      <div class="tabs">
        <button type="button" :class="{ on: tab === 'favorites' }" :aria-pressed="tab === 'favorites'" @click="tab = 'favorites'">我的收藏（{{ favoriteState.error && !favs.length ? '—' : favs.length }}）</button>
        <button type="button" :class="{ on: tab === 'guides' }" :aria-pressed="tab === 'guides'" @click="tab = 'guides'">我发布的攻略（{{ guideState.error && !guides.length ? '—' : guides.length }}）</button>
      </div>

      <!-- 收藏 -->
      <div v-if="tab === 'favorites'">
        <p v-if="favoriteState.error" class="panel-error" role="alert">{{ favoriteState.error }} <button :disabled="favoriteState.loading" @click="loadList('favorites')">重试收藏</button></p>
        <p v-if="favoriteState.loading" class="loading">正在加载收藏…</p>
        <p v-if="!favs.length && !favoriteState.error && !favoriteState.loading" class="empty">还没有收藏景点，去<router-link class="lk" to="/spots">列表页</router-link>逛逛吧</p>
        <div v-else class="grid">
          <SpotCard v-for="f in favs" :key="f.spot.id" :spot="f.spot" />
        </div>
      </div>

      <!-- 攻略 -->
      <div v-else>
        <p v-if="guideState.error" class="panel-error" role="alert">{{ guideState.error }} <button :disabled="guideState.loading" @click="loadList('guides')">重试攻略</button></p>
        <p v-if="guideState.loading" class="loading">正在加载攻略…</p>
        <div class="gbar">
          <span class="hint">共 {{ guideState.error && !guides.length ? '—' : guides.length }} 篇（含草稿，草稿仅自己可见）</span>
          <button class="btn-write" @click="router.push('/guides/new')">✍️ 写攻略</button>
        </div>
        <p v-if="!guides.length && !guideState.error && !guideState.loading" class="empty">还没有发布攻略，点右上角「写攻略」开始第一篇 ✍️</p>
        <div v-else class="glist">
          <div v-for="g in guides" :key="g.id" class="gitem">
            <router-link :to="`/guides/${g.id}`"><b>
              {{ g.title }}
              <span v-if="g.is_draft" class="draft">草稿</span>
            </b></router-link>
            <small>{{ g.city || '未填城市' }} · {{ g.created_at }} · 👁 {{ g.views }}</small>
            <router-link class="lk" :to="`/guides/${g.id}/edit`">编辑</router-link>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.profile-area{margin-bottom:10px}.profile-tools{padding:12px 16px;font-size:12px;line-height:1.7;background:var(--green-soft);border-radius:10px;margin-top:10px}.profile-tools button,.panel-error button{min-height:44px;padding:8px 12px;margin:8px 8px 0 0;border:1px solid var(--green-border);border-radius:8px;background:#fff;color:var(--green);font:inherit;cursor:pointer}.profile-tools p[role=alert],.panel-error{color:#a33}.panel-error{font-size:13px;line-height:1.7;padding:12px;background:#fff8f4;border-radius:10px}.profile-tools .hint{color:var(--text-sub)}.p-edit label{display:grid;gap:5px;font-size:12px}.p-edit button{min-height:44px;align-self:end}.p-edit input[readonly]{background:#f6f8f5}.p-edit button:disabled,.profile-tools button:disabled,.panel-error button:disabled{opacity:.5;cursor:default}.tabs button{min-height:44px}.gitem a{color:inherit}.gitem .lk{color:var(--green)}
.footprints-link{display:inline-block;margin:16px 0;color:#1a6e50;font-weight:700}
.panel { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; }
.profile { display: flex; gap: 16px; align-items: center; }
.avatar {
  flex-shrink:0;
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
.tabs button {
  padding: 8px 18px; border-radius: 20px; background: #fff;
  border: 1px solid var(--line); cursor: pointer; font-size: 13.5px; color: #666;
}
.tabs button.on { background: var(--green); border-color: var(--green); color: #fff; }
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
