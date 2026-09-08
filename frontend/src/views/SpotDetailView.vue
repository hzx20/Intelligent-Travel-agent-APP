<script setup>
/** 景点详情：大图 + 收藏切换 + 数据来源跳转 + 评论列表与发表 */
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../store/auth'

const route = useRoute()
const { user, openLogin } = useAuth()
const detail = ref(null)
const error = ref('')
const favBusy = ref(false)
const commentText = ref('')
const commentRating = ref(0)
const commentBusy = ref(false)
const commentMsg = ref('')

async function load() {
  try {
    detail.value = await api.get(`/api/spots/${route.params.id}`)
  } catch (e) {
    error.value = e.message
  }
}

async function toggleFav() {
  if (!user.value) { openLogin('login'); return } // 未登录 → 唤起登录弹窗
  if (favBusy.value) return
  favBusy.value = true
  try {
    const r = await api.post(`/api/spots/${detail.value.spot.id}/favorite`)
    detail.value.favorited = r.favorited
    detail.value.favorite_count = r.favorite_count
  } catch (e) {
    alert(e.message)
  } finally {
    favBusy.value = false
  }
}

function setRating(n) {
  commentRating.value = commentRating.value === n ? 0 : n
}

async function submitComment() {
  if (!user.value) { openLogin('login'); return }
  if (!commentText.value.trim()) { commentMsg.value = '写点什么再发布吧'; return }
  commentBusy.value = true
  commentMsg.value = ''
  try {
    const body = { content: commentText.value.trim() }
    if (commentRating.value) body.rating = commentRating.value
    const c = await api.post(`/api/spots/${detail.value.spot.id}/comments`, body)
    detail.value.comments.unshift(c) // 新评论插到最前
    commentText.value = ''
    commentRating.value = 0
    commentMsg.value = '发布成功'
  } catch (e) {
    commentMsg.value = e.message
  } finally {
    commentBusy.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="wrap">
    <p v-if="error" class="empty">{{ error }}</p>
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
          <button class="fav-btn" :class="{ on: detail.favorited }" :disabled="favBusy" @click="toggleFav">
            {{ detail.favorited ? '❤ 已收藏' : '♡ 收藏' }} · {{ detail.favorite_count }} 人
          </button>
          <a v-if="detail.source_url" class="src-link" :href="detail.source_url" target="_blank" rel="noopener">
            ↗ 在{{ detail.source }}查看
          </a>
        </div>
        <p class="tags" v-if="detail.spot.tags">标签：{{ detail.spot.tags.split(',').join(' / ') }}</p>
        <p class="desc">{{ detail.description || '景区简介待补充（后台可编辑）。' }}</p>
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
            :src="`/api/map/static?points=${detail.spot.lng},${detail.spot.lat}&size=640*240&zoom=14&label=0&path=0`"
            @error="$event.target.closest('.panel').style.display = 'none'"
          />
        </a>
      </div>

      <div class="sec">游客评论 <small>{{ detail.comments.length }} 条</small></div>

      <!-- 发表评论 -->
      <div class="panel editor">
        <p v-if="!user" class="hint">想留下一句？<span class="lk" @click="openLogin('login')">登录</span> 后即可评论与收藏。</p>
        <template v-else>
          <textarea v-model="commentText" rows="3" maxlength="500" placeholder="分享你的游览体验（1-500 字）…"></textarea>
          <div class="editor-row">
            <span class="stars">
              <span v-for="n in 5" :key="n" :class="{ on: n <= commentRating }" @click="setRating(n)">★</span>
            </span>
            <span class="msg">{{ commentMsg }}</span>
            <button class="btn-send" :disabled="commentBusy" @click="submitComment">
              {{ commentBusy ? '发布中…' : '发布评论' }}
            </button>
          </div>
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
.pos-title { font-weight: 700; font-size: 13.5px; margin-bottom: 8px; }
.pos-title small { font-weight: 400; font-size: 11.5px; color: var(--text-sub); margin-left: 6px; }
.pos-map { width: 100%; border-radius: 8px; display: block; border: 1px solid var(--line); }
.editor textarea {
  width: 100%; border: 1.5px solid #ccc; border-radius: 10px;
  padding: 10px 12px; font-size: 13.5px; resize: vertical; font-family: inherit;
}
.editor textarea:focus { outline: none; border-color: var(--green); }
.editor-row { display: flex; align-items: center; gap: 12px; margin-top: 8px; }
.stars span { font-size: 20px; color: #ddd; cursor: pointer; }
.stars span.on { color: #e8a33d; }
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
@media (max-width: 640px) { .hero { height: 180px; } }
</style>
