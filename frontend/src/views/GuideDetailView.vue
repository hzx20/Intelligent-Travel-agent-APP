<script setup>
/** 攻略详情（v0.8）：图片画廊 + 纯正文两区块 + 关联景点胶囊（对齐原型第 6 页） */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import GuideToTrip from '../components/GuideToTrip.vue'
import { useAuth } from '../store/auth'
import { registerAuthChangeGuard } from '../store/authChangeGuard'
import { guardTripUnload } from '../utils/tripDraftGuard'

const route = useRoute()
const router = useRouter()
const guide = ref(null)
const error = ref('')
const gi = ref(0) // 当前画廊图片下标
const { user } = useAuth()
const actionError = ref(''), deleting = ref(false)
const deletionUncertain = ref(false)
const deletionStatus = ref(null)
let generation = 0, unregisterAuthGuard = null
function confirmDeparture() { return !deleting.value || window.confirm('删除请求仍在处理，离开后请在“我的攻略”核对结果。确定离开吗？') }
function beforeUnload(event) { guardTripUnload(event,false,deleting.value) }
onBeforeRouteLeave(confirmDeparture)
onBeforeRouteUpdate(confirmDeparture)
onMounted(() => { window.addEventListener('beforeunload',beforeUnload); unregisterAuthGuard = registerAuthChangeGuard(confirmDeparture) })
onBeforeUnmount(() => { generation++; window.removeEventListener('beforeunload',beforeUnload); unregisterAuthGuard?.() })

const images = computed(() => {
  if (!guide.value) return []
  if (guide.value.images?.length) return guide.value.images
  return guide.value.cover_image ? [guide.value.cover_image] : []
})
const paragraphs = computed(() =>
  (guide.value?.content || '').split(/\n+/).map(s => s.trim()).filter(Boolean)
)

function gshow(i) {
  if (!images.value.length) return
  const n = images.value.length
  gi.value = (i + n) % n
}

async function load() {
  if (deleting.value) return
  const run = ++generation, id = route.params.id, uid = user.value?.id
  guide.value = null; error.value = ''; actionError.value = ''; deletionUncertain.value = false
  try {
    const data = await api.get(`/api/guides/${id}`)
    if (run !== generation || id !== route.params.id || uid !== user.value?.id) return
    if (data?.id !== Number(id) || typeof data.title !== 'string' || typeof data.content !== 'string' || typeof data.is_owner !== 'boolean' || typeof data.is_draft !== 'boolean' || !Array.isArray(data.spots) || !Array.isArray(data.images)) {
      throw new Error('无法确认这篇攻略的内容，请重新加载后再试。')
    }
    guide.value = data
    gi.value = 0
  } catch (e) {
    if (run === generation) error.value = e.message
  }
}

async function removeGuide() {
  if (deleting.value || deletionUncertain.value || !guide.value?.is_owner) return
  const {id,version,revision} = guide.value, run = generation
  if (!Number.isSafeInteger(version) || version < 1 || !revision) { actionError.value = '当前服务不支持安全删除，请更新服务后重新加载。'; return }
  if (!window.confirm('删除后不可恢复，确定要删除这篇攻略吗？')) return
  deleting.value = true; actionError.value = ''
  try {
    await api.del(`/api/guides/${id}/content?version=${version}&revision=${encodeURIComponent(revision)}`)
    if (run === generation) {
      deleting.value = false; guide.value = null
      const failed = await router.push('/guides')
      if (failed && run === generation) error.value = '攻略已删除，请返回我的攻略核对。'
    }
  } catch (e) {
    if (run === generation) {
      actionError.value = e.message; deletionUncertain.value = Boolean(e.outcomeUnknown || e.code?.startsWith('SESSION_'))
      if (deletionUncertain.value) {
        await nextTick()
        if (run === generation) deletionStatus.value?.scrollIntoView({block:'center'})
      }
    }
  } finally { if (run === generation) deleting.value = false }
}

watch(() => [route.params.id,user.value?.id],() => { generation++; deleting.value = false; load() },{immediate:true,flush:'sync'})
</script>

<template>
  <div class="wrap">
    <p v-if="error" class="empty" role="alert">{{ error }} <button @click="load">重新加载攻略</button> <router-link to="/me?tab=guides">返回我的攻略</router-link></p>
    <p v-else-if="!guide" class="loading">加载中…</p>
    <template v-else>
      <div class="crumb">旅游攻略 / <b>{{ guide.title }}</b></div>

      <div class="arti">
        <h2>
          {{ guide.title }}
          <span v-if="guide.is_draft" class="draft">草稿</span>
        </h2>
        <div class="by">
          {{ guide.author }} · 发布于 {{ guide.created_at.slice(0, 10) }}
          · 👁 {{ guide.views }} · 关联景点 {{ guide.spots.length }} 个
        </div>
        <GuideToTrip :guide-id="guide.id" :guide-is-draft="guide.is_draft" :disabled="deleting || deletionUncertain" />

        <!-- 区块一：图片画廊（与正文完全分离） -->
        <div class="gallery">
          <div class="glabel">🖼️ 图片画廊 · 共 {{ images.length }} 图</div>
          <div v-if="images.length" class="gwrap">
            <img class="gmain" :src="images[gi]" :alt="`图 ${gi + 1}`" />
            <button class="gbtn l" type="button" aria-label="上一张攻略图片" @click="gshow(gi - 1)">‹</button>
            <button class="gbtn r" type="button" aria-label="下一张攻略图片" @click="gshow(gi + 1)">›</button>
          </div>
          <div v-else class="gwrap">
            <div class="gmain ph">暂无图片 · 图占位</div>
          </div>
          <div v-if="images.length > 1" class="gthumbs">
            <button
              v-for="(u, i) in images" :key="i"
              type="button" class="thumb-button" :aria-label="`查看攻略图片 ${i + 1}`" :aria-pressed="i === gi"
              @click="gshow(i)"
            ><img class="gt" :class="{ on: i === gi }" :src="u" :alt="`缩略图 ${i + 1}`" /></button>
          </div>
        </div>

        <!-- 区块二：正文（纯文字） -->
        <div class="articletext">
          <div class="glabel">📝 正文</div>
          <p v-for="(p, i) in paragraphs" :key="i">{{ p }}</p>
        </div>

        <div v-if="guide.spots.length" class="sec">
          关联景点 <small>点击直达详情页</small>
        </div>
        <div v-if="guide.spots.length" class="linkspot">
          <router-link
            v-for="s in guide.spots" :key="s.id" class="lspot"
            :to="`/spots/${s.id}`"
          >📍 {{ s.name }}</router-link>
        </div>

        <div v-if="guide.is_owner" class="actions">
          <button class="btn" :disabled="deleting || deletionUncertain" @click="router.push(`/guides/${guide.id}/edit`)">✏️ 编辑</button>
          <button class="btn ghost" :disabled="deleting || deletionUncertain" @click="removeGuide">{{ deleting ? '删除中…' : '🗑 删除' }}</button>
        </div>
        <p v-if="actionError" role="alert">{{ actionError }} <button :disabled="deleting" @click="load">重新加载攻略</button></p>
        <p v-if="deletionUncertain" ref="deletionStatus" class="delete-status" role="status">删除结果尚未确认。请先重新加载核对；本页已暂停再次删除和从攻略收集地点。<router-link to="/me?tab=guides">查看我的攻略</router-link></p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.delete-status{font-size:12px;line-height:1.7;margin-top:12px;color:#8d593d}.delete-status a{color:var(--green);margin-left:8px}.thumb-button{flex-shrink:0;border:0;padding:0;background:none;border-radius:8px;cursor:pointer}.thumb-button .gt{display:block}.actions button{min-height:44px}.actions button:disabled{opacity:.5}.arti p[role=alert]{font-size:13px;line-height:1.7;color:#a33;margin-top:12px}.arti p[role=alert] button,.empty button{min-height:44px;padding:8px 12px;border:1px solid var(--green-border);border-radius:8px;background:white;color:var(--green)}
.crumb { font-size: 12.5px; color: var(--text-sub); margin-bottom: 10px; }
.crumb b { color: var(--text); }
.arti {
  background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 20px 22px 24px;
}
.arti h2 { font-size: 20px; line-height: 1.45; margin-bottom: 8px; }
.draft {
  font-size: 11px; color: #b7791f; background: #fdf3e2;
  border: 1px solid #f0dcb8; border-radius: 20px; padding: 2px 8px;
  vertical-align: middle; margin-left: 6px;
}
.by { font-size: 12.5px; color: var(--text-sub); margin-bottom: 16px; }

.glabel { font-size: 13px; font-weight: 600; color: #555; margin-bottom: 8px; }
.gwrap { position: relative; border-radius: 10px; overflow: hidden; background: #dfe8e2; }
.gmain { width: 100%; height: 360px; object-fit: cover; display: block; }
.gmain.ph {
  height: 200px; display: flex; align-items: center; justify-content: center;
  color: #8aa39a; font-size: 13px;
}
.gbtn {
  position: absolute; top: 50%; transform: translateY(-50%);
  width: 44px; height: 44px; border:0;border-radius: 50%;cursor:pointer;
  background: rgba(0,0,0,.35); color: #fff; font-size: 22px;
  display: flex; align-items: center; justify-content: center; user-select: none;
}
.gbtn:hover { background: rgba(0,0,0,.55); }
.gbtn.l { left: 10px; }
.gbtn.r { right: 10px; }
.gthumbs { display: flex; gap: 8px; margin-top: 8px; overflow-x: auto; padding-bottom: 4px; }
.gt {
  width: 84px; height: 58px; object-fit: cover; border-radius: 8px;
  cursor: pointer; opacity: .65; border: 2px solid transparent; flex-shrink: 0;
}
.gt.on { opacity: 1; border-color: var(--green); }

.articletext { margin-top: 22px; }
.articletext p { font-size: 14px; line-height: 1.9; color: #3a3a3a; margin-bottom: 12px; }

.linkspot { display: flex; flex-wrap: wrap; gap: 8px; }
.lspot {
  font-size: 12.5px; padding: 5px 12px; border-radius: 20px;
  background: var(--green-soft); border: 1px solid var(--green-border);
  color: var(--green); cursor: pointer;
}
.lspot:hover { background: #dcefe6; }

.actions { display: flex; gap: 10px; margin-top: 22px; }
.btn {
  border: none; border-radius: 9px; padding: 9px 18px;
  font-size: 13.5px; background: var(--green); color: #fff;
}
.btn.ghost { background: #fff; color: #c0392b; border: 1px solid #f0c8c2; }

@media (max-width: 640px) {
  .arti { padding: 16px 14px 20px; }
  .arti h2 { font-size: 17px; }
  .gmain { height: 210px; }
}
</style>
