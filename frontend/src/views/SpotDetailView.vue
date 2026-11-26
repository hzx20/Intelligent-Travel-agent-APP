<script setup>
/** 景点详情：大图 + 收藏切换 + 数据来源跳转 + 评论列表与发表 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from 'vue-router'
import { api, BASE } from '../api'
import { useAuth } from '../store/auth'
import SpotToTrip from '../components/SpotToTrip.vue'
import { registerAuthChangeGuard } from '../store/authChangeGuard'
import { guardTripUnload } from '../utils/tripDraftGuard'
import { newCommentAttempt, readCommentAttempt, saveCommentAttempt, clearCommentAttempt } from '../store/commentAttempt'

const route = useRoute()
const { user, openLogin } = useAuth()
const detail = ref(null)
const error = ref('')
const favBusy = ref(false)
const commentText = ref('')
const commentRating = ref(0)
const commentBusy = ref(false)
const commentMsg = ref('')
const collectDirty = ref(false), favError = ref(''), favUncertain = ref(false), commentUncertain = ref(false), checking = ref(false), checkMsg = ref('')
const pendingFavorite = ref(null), favConflict = ref(false), pendingComment = ref(null), commentRecoveryError = ref('')
const hasDraft = computed(() => Boolean(collectDirty.value || commentText.value || commentRating.value || commentUncertain.value || favUncertain.value))
const pageBusy = computed(() => favBusy.value || commentBusy.value)
let unregisterAuthGuard = null
function confirmDeparture() { return !(hasDraft.value || pageBusy.value) || window.confirm('本页还有未保存输入、待确认结果或正在进行的操作。离开后本页内容可能丢失，确定离开吗？') }
function beforeUnload(event) { guardTripUnload(event,hasDraft.value,pageBusy.value) }
onBeforeRouteLeave(confirmDeparture); onBeforeRouteUpdate(confirmDeparture)
onMounted(() => { window.addEventListener('beforeunload',beforeUnload); unregisterAuthGuard = registerAuthChangeGuard(confirmDeparture) })
onBeforeUnmount(() => { loadSequence++; window.removeEventListener('beforeunload',beforeUnload); unregisterAuthGuard?.() })

/** 「用 AI 排进行程」：带着这个城市和景点名跳到规划页，自动发起。
 *  景点板块由此从"浏览终点"变成"规划起点"——不跟规划助手抢流量，而是给它送人。 */
const planLink = computed(() => {
  const s = detail.value?.spot
  if (!s) return '/plan'
  const q = `去${s.city}玩，想把${s.name}安排进行程`
  return { path: '/plan', query: { d: s.city, q, auto: '1' } }
})

/** 简介缺位时用来撑住信息量的主类型（高德标签的第一项，比"景点"具体） */
const mainType = computed(() => {
  const tags = detail.value?.spot?.tags || ''
  return tags.split(',').map((t) => t.trim()).filter(Boolean)[0] || '景点'
})

let loadSequence = 0
const active = (run,id,uid) => run === loadSequence && id === route.params.id && uid === user.value?.id
async function load() {
  const run = ++loadSequence, id = route.params.id, uid = user.value?.id
  detail.value = null; error.value = ''
  collectDirty.value = false; favBusy.value = false; commentBusy.value = false; checking.value = false
  commentText.value = ''; commentRating.value = 0; commentMsg.value = ''; favError.value = ''; favUncertain.value = false; commentUncertain.value = false; checkMsg.value = ''
  pendingFavorite.value = null; favConflict.value = false; pendingComment.value = null; commentRecoveryError.value = ''
  try {
    const result = await api.get(`/api/spots/${id}`)
    if (!active(run,id,uid)) return
    if (result?.spot?.id !== Number(id) || !Array.isArray(result.comments) || typeof result.favorited !== 'boolean') throw new Error('无法确认当前景点，请重新加载后再试。')
    detail.value = result
    if (uid) {
      try {
        const attempt = readCommentAttempt(uid,Number(id))
        if (attempt) { pendingComment.value = attempt; commentUncertain.value = true; commentText.value = attempt.payload.content; commentRating.value = attempt.payload.rating || 0 }
      } catch (e) { commentRecoveryError.value = e.message; commentUncertain.value = true }
    }
  } catch (e) {
    if (active(run,id,uid)) error.value = e.message
  }
}

async function toggleFav() {
  if (!user.value) { openLogin('login'); return } // 未登录 → 唤起登录弹窗
  if (favBusy.value || checking.value || favConflict.value || !detail.value) return
  if (!Number.isSafeInteger(detail.value.favorite_version) || detail.value.favorite_version < 0) { favError.value = '当前服务不支持可靠收藏，请更新后重新加载。'; return }
  const run = loadSequence, id = route.params.id, uid = user.value.id
  const body = pendingFavorite.value || Object.freeze({favorited:!detail.value.favorited,version:detail.value.favorite_version})
  favBusy.value = true; favError.value = ''
  try {
    const r = await api.put(`/api/spots/${detail.value.spot.id}/favorite-state`,body)
    if (!active(run,id,uid)) return
    if (r?.favorited !== body.favorited || !Number.isSafeInteger(r.favorite_count) || r.favorite_count < 0 || !Number.isSafeInteger(r.favorite_version) || r.favorite_version < Math.max(body.version,detail.value.favorite_version)) { const e = new Error('收藏结果无法确认，请先核对。'); e.outcomeUnknown = true; throw e }
    detail.value.favorited = r.favorited
    detail.value.favorite_count = r.favorite_count
    detail.value.favorite_version = r.favorite_version; pendingFavorite.value = null; favUncertain.value = false; favConflict.value = false
  } catch (e) {
    if (active(run,id,uid)) {
      favError.value = [404,405,428].includes(e.status) ? '当前服务无法可靠保存收藏，请更新服务或核对景点状态。' : e.message
      if (e.outcomeUnknown || e.code?.startsWith('SESSION_') || e.status === 409) pendingFavorite.value = body
      favUncertain.value = Boolean(pendingFavorite.value); favConflict.value = e.status === 409
    }
  } finally {
    if (active(run,id,uid)) favBusy.value = false
  }
}

function setRating(n) {
  if (commentBusy.value || commentUncertain.value) return
  commentRating.value = commentRating.value === n ? 0 : n
}

async function submitComment() {
  if (commentBusy.value || commentRecoveryError.value || checking.value || !detail.value) return
  if (!user.value) { openLogin('login'); return }
  if (!commentText.value.trim()) { commentMsg.value = '写点什么再发布吧'; return }
  const run = loadSequence, id = route.params.id, uid = user.value.id
  commentBusy.value = true
  commentMsg.value = ''
  try {
    const capability = await api.get('/api/spots/write-capabilities')
    if (!active(run,id,uid)) return
    if (capability?.idempotent_comments !== true) throw Error('当前服务不支持可靠评论发布，请更新后再试。')
    let attempt = pendingComment.value
    if (!attempt) {
      const next = newCommentAttempt({content:commentText.value.trim(),rating:commentRating.value || null})
      saveCommentAttempt(uid,Number(id),next); attempt = next; pendingComment.value = next; commentUncertain.value = true
    }
    const c = await api.post(`/api/spots/${id}/comments/create`, {...attempt.payload,request_id:attempt.request_id})
    if (!active(run,id,uid)) return
    if (!Number.isSafeInteger(c?.id) || c.id < 1 || c.content !== attempt.payload.content || c.rating !== attempt.payload.rating || c.request_id !== attempt.request_id || c.spot_id !== Number(id)) throw Error('评论结果无法确认，请继续本次发布以找回同一条评论。')
    clearCommentAttempt(uid,Number(id),attempt.request_id)
    if (!detail.value.comments.some(comment => comment.id === c.id)) detail.value.comments.unshift(c)
    commentText.value = ''
    commentRating.value = 0
    commentMsg.value = '发布成功'
    pendingComment.value = null; commentUncertain.value = false
  } catch (e) {
    if (active(run,id,uid)) { commentMsg.value = [404,405].includes(e.status) ? '无法完成可靠评论发布，请核对服务或景点状态；不会退回旧发布接口。' : e.message; commentUncertain.value = Boolean(pendingComment.value || commentRecoveryError.value) }
  } finally {
    if (active(run,id,uid)) commentBusy.value = false
  }
}
async function checkInteractions() {
  if (!detail.value || checking.value || pageBusy.value) return
  const run = loadSequence, id = route.params.id, uid = user.value?.id
  checking.value = true; checkMsg.value = ''
  try {
    const result = await api.get(`/api/spots/${id}`)
    if (!active(run,id,uid)) return
    if (result?.spot?.id !== Number(id) || !Array.isArray(result.comments) || typeof result.favorited !== 'boolean' || !Number.isSafeInteger(result.favorite_version) || result.favorite_version < (detail.value.favorite_version || 0)) throw new Error('无法核对当前景点状态，请稍后再试。')
    detail.value = {...detail.value,favorited:result.favorited,favorite_count:result.favorite_count,favorite_version:result.favorite_version,comments:result.comments}
    favUncertain.value = false; favError.value = ''; pendingFavorite.value = null; favConflict.value = false
    checkMsg.value = '已读取最新收藏和评论，本页评论输入及原次发布标识仍保留。'
  } catch (e) { if (active(run,id,uid)) checkMsg.value = e.message }
  finally { if (active(run,id,uid)) checking.value = false }
}
function resumeComment() {
  if (!commentUncertain.value || commentBusy.value || !user.value || !window.confirm('这次评论可能已经发布。放弃只清除本标签重试信息，不删除评论；请先核对评论列表，确定放弃吗？')) return
  try { clearCommentAttempt(user.value.id,Number(route.params.id),pendingComment.value?.request_id); pendingComment.value = null; commentRecoveryError.value = ''; commentUncertain.value = false; commentMsg.value = '' }
  catch { commentMsg.value = '无法清除本次发布信息，请检查浏览器存储后重试。' }
}
watch(() => [route.params.id,user.value?.id], load, {immediate:true,flush:'sync'})
</script>

<template>
  <div class="wrap">
    <p v-if="error" class="empty" role="alert">{{ error }} <button @click="load">重新加载景点</button></p>
    <p v-else-if="!detail" class="loading">加载中…</p>
    <template v-else>
      <div class="crumb">当前位置：旅游景点 / {{ detail.spot.city }} / <b>{{ detail.spot.name }}</b></div>

      <div class="hero" :style="detail.spot.image_url ? { backgroundImage: `url(${detail.spot.image_url})` } : {}">
        <div class="hero-cap">
          <h2>{{ detail.spot.name }}</h2>
          <p>{{ detail.spot.city }} · {{ detail.spot.district || '未知区域' }} · {{ detail.address || '地址待补' }}</p>
        </div>
      </div>

      <div class="panel">
        <div class="meta">
          <span v-if="detail.spot.is_free" class="badge-free">免费</span>
          <span v-else-if="detail.spot.price" class="badge-price">¥{{ detail.spot.price }} 起</span>
          <span v-else class="badge-free">票价以景区公示为准</span>
          <router-link class="plan-btn" :to="planLink">用 AI 排进行程 →</router-link>
          <SpotToTrip :detail="detail" @dirty="collectDirty = $event" />
          <button class="fav-btn" :class="{ on: detail.favorited }" :disabled="favBusy || checking || favUncertain" @click="toggleFav">
            {{ detail.favorited ? '❤ 已收藏' : '♡ 收藏' }} · {{ detail.favorite_count }} 人
          </button>
          <a v-if="detail.source_url" class="src-link" :href="detail.source_url" target="_blank" rel="noopener">
            ↗ 在{{ detail.source }}查看
          </a>
        </div>
        <p v-if="favError" role="alert">{{ favError }}</p>
        <button v-if="pendingFavorite" class="interaction-retry" type="button" :disabled="favBusy || checking || favConflict" @click="toggleFav">继续这次收藏操作</button>
        <div class="interaction-check"><button type="button" :disabled="checking || pageBusy" @click="checkInteractions">核对收藏和评论</button><p v-if="checkMsg" role="status">{{ checkMsg }}</p></div>
        <p class="tags" v-if="detail.spot.tags">标签：{{ detail.spot.tags.split(',').join(' / ') }}</p>
        <p class="desc" v-if="detail.description">{{ detail.description }}</p>

        <!-- 简介没收录时不再甩一句"待补充（后台可编辑）"给用户看：
             那是写给我们自己的备注，不是内容。改成把手里确实有的真实字段摊开，
             并诚实说明简介缺什么、去哪儿能看原始资料。 -->
        <div v-else class="facts">
          <div class="fact">
            <span>所在区域</span>
            <b>{{ detail.spot.city }}{{ detail.spot.district ? ' · ' + detail.spot.district : '' }}</b>
          </div>
          <div class="fact" v-if="mainType">
            <span>景点类型</span>
            <b>{{ mainType }}</b>
          </div>
          <div class="fact" v-if="detail.address || detail.spot.address">
            <span>详细地址</span>
            <b>{{ detail.address || detail.spot.address }}</b>
          </div>
          <div class="fact" v-if="detail.spot.lng && detail.spot.lat">
            <span>地理坐标</span>
            <b>{{ detail.spot.lng.toFixed(4) }}, {{ detail.spot.lat.toFixed(4) }}</b>
          </div>
          <p class="fact-note">
            该景点的文字简介尚未收录。以上地址、类型与坐标取自
            {{ detail.source || '高德地图开放平台' }}，点右上角「在{{ detail.source }}查看」可阅读原始 POI 资料。
          </p>
        </div>
      </div>

      <!-- 位置地图（静态图，后端代理生成，key 不外泄） -->
      <div v-if="detail.spot.lng && detail.spot.lat" class="panel">
        <div class="pos-title">📍 位置 <small>静态地图 · 红点为景区位置</small></div>
        <a
          :href="`https://uri.amap.com/marker?position=${detail.spot.lng},${detail.spot.lat}&name=${encodeURIComponent(detail.spot.name)}&src=travel-planner&coordinate=gaode`"
          target="_blank" rel="noopener"
        >
          <img
            class="pos-map" alt="景区位置图"
            :src="`${BASE}/api/map/static?points=${detail.spot.lng},${detail.spot.lat}&size=640*240&zoom=14&label=0&path=0`"
            @error="$event.target.closest('.panel').style.display = 'none'"
          />
        </a>
      </div>

      <div class="sec">游客评论 <small>{{ detail.comments.length }} 条</small></div>

      <!-- 发表评论 -->
      <div class="panel editor">
        <p v-if="!user" class="hint">想留下一句？<span class="lk" @click="openLogin('login')">登录</span> 后即可评论与收藏。</p>
        <template v-else>
          <textarea v-model="commentText" aria-label="游览评论" :readonly="commentUncertain" :disabled="commentBusy" rows="3" maxlength="500" placeholder="分享你的游览体验（1-500 字）…"></textarea>
          <div class="editor-row">
            <span class="stars">
              <button v-for="n in 5" :key="n" type="button" :aria-label="`评分 ${n} 星`" :aria-pressed="n === commentRating" :disabled="commentBusy || commentUncertain" :class="{ on: n <= commentRating }" @click="setRating(n)">★</button>
            </span>
            <span class="msg" role="status">{{ commentMsg }}</span>
            <button class="btn-send" :disabled="commentBusy || checking || Boolean(commentRecoveryError)" @click="submitComment">
              {{ commentBusy ? '发布中…' : pendingComment ? '继续这次发布' : '发布评论' }}
            </button>
          </div>
          <p v-if="commentUncertain" class="comment-pending">本次发布的信息已按账号和景点保留在本标签页，刷新后可继续找回同一条评论；未提交输入仍只在本页。核对列表不会自动再次发布。{{ commentRecoveryError }}<button type="button" :disabled="commentBusy" @click="resumeComment">放弃本次发布</button></p>
        </template>
      </div>

      <div class="panel comments">
        <p v-if="!detail.comments.length" class="empty">还没有评论，来做第一个分享的人</p>
        <div v-for="cm in detail.comments" :key="cm.id" class="comment">
          <div class="c-head">
            <b>{{ cm.username }}</b>
            <span class="c-time">{{ cm.created_at }}</span>
            <span v-if="cm.rating" class="c-rate">{{ '★'.repeat(cm.rating) }}</span>
          </div>
          <p>{{ cm.content }}</p>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.interaction-check,.comment-pending{font-size:12px;line-height:1.7;margin:12px 0;color:var(--text-sub)}.interaction-retry,.interaction-check button,.comment-pending button,.empty button{min-height:44px;border:1px solid var(--green-border);border-radius:8px;background:white;color:var(--green);padding:8px 12px;font:inherit}.interaction-retry:disabled,.interaction-check button:disabled,.comment-pending button:disabled{opacity:.5}.panel p[role=alert]{font-size:12px;color:#a33;line-height:1.7}.stars{display:inline-flex}.stars button{min-width:36px;min-height:44px;border:0;background:none;font:inherit}.editor-row{flex-wrap:wrap}
.crumb { font-size: 12px; color: #888; margin-bottom: 12px; }
.hero {
  height: 260px; border-radius: 12px; overflow: hidden;
  background: linear-gradient(135deg, #2a8a67, #1a6e50); background-size: cover; background-position: center;
  display: flex; align-items: flex-end;
}
.hero-cap { width: 100%; padding: 30px 20px 16px; color: #fff; background: linear-gradient(transparent, rgba(0,0,0,.55)); }
.hero-cap h2 { font-size: 21px; margin-bottom: 4px; }
.hero-cap p { font-size: 13px; opacity: .9; }
.panel { background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; margin-top: 14px; }
.meta { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
/* 主行动：景点页唯一一个实心按钮，指向规划助手 */
.plan-btn {
  background: var(--green); color: #fff;
  border-radius: 20px; padding: 6px 15px; font-size: 13px; font-weight: 600;
  transition: background .15s;
}
.plan-btn:hover { background: #14563e; }
.fav-btn {
  border: 1.5px solid var(--green-border); background: var(--green-soft); color: var(--green);
  border-radius: 20px; padding: 5px 14px; font-size: 13px;
}
.fav-btn.on { background: #fdeeee; border-color: #e5b4b4; color: #c0392b; }
.fav-btn:disabled { opacity: .6; }
.src-link {
  margin-left: auto; font-size: 12.5px; color: var(--green);
  border: 1px solid var(--green-border); background: var(--green-soft);
  padding: 5px 12px; border-radius: 20px;
}
.tags { font-size: 12.5px; color: #888; margin-top: 10px; }
.desc { font-size: 13.5px; line-height: 2; margin-top: 8px; color: #333; }
/* 简介缺位时的替代信息区：两列字段，行高紧凑，视觉上仍是"内容"而不是"占位" */
.facts {
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 22px; margin-top: 10px;
}
.fact { display: flex; gap: 8px; font-size: 13px; line-height: 1.7; min-width: 0; }
.fact span { flex: 0 0 60px; color: #8b968f; }
.fact b { font-weight: 600; color: #333; word-break: break-all; }
.fact-note {
  grid-column: 1 / -1; font-size: 11.5px; color: #93a09a;
  line-height: 1.7; margin-top: 2px; padding-top: 8px; border-top: 1px dashed var(--line);
}
.pos-title { font-weight: 700; font-size: 13.5px; margin-bottom: 8px; }
.pos-title small { font-weight: 400; font-size: 11.5px; color: var(--text-sub); margin-left: 6px; }
.pos-map { width: 100%; border-radius: 8px; display: block; border: 1px solid var(--line); }
.editor textarea {
  width: 100%; border: 1.5px solid #ccc; border-radius: 10px;
  padding: 10px 12px; font-size: 13.5px; resize: vertical; font-family: inherit;
}
.editor textarea:focus { outline: none; border-color: var(--green); }
.editor-row { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.stars button { font-size: 20px; color: #999; cursor: pointer; }
.stars button.on { color: #a96b12; }
.msg { font-size: 12px; color: var(--green); flex: 1; }
.btn-send {
  background: var(--green); color: #fff; border: none;
  border-radius: 8px; padding: 8px 18px; font-size: 13px;
}
.btn-send:disabled { opacity: .6; }
.hint { font-size: 13px; color: #666; }
.hint .lk { color: var(--green); cursor: pointer; font-weight: 600; }
.comment { border-bottom: 1px dashed var(--line); padding: 10px 0; }
.comment:last-child { border: none; }
.c-head { display: flex; gap: 10px; font-size: 12.5px; margin-bottom: 4px; align-items: baseline; }
.c-time { color: #aaa; font-size: 11.5px; }
.c-rate { color: #e8a33d; }
.comment p { font-size: 13px; color: #444; }
@media (max-width: 640px) {
  .hero { height: 180px; }
  .facts { grid-template-columns: 1fr; gap: 6px; }   /* 窄屏两列会把地址挤成竖条 */
}
</style>
