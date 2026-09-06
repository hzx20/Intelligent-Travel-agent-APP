<script setup>
/** 景点详情：大图 + 收藏人数 + 数据来源跳转 + 评论列表 */
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'

const route = useRoute()
const detail = ref(null)
const error = ref('')

onMounted(async () => {
  try {
    detail.value = await api.get(`/api/spots/${route.params.id}`)
  } catch (e) {
    error.value = e.message
  }
})
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
          <span class="fav-num">❤ <b>{{ detail.favorite_count }}</b> 人收藏</span>
          <a v-if="detail.source_url" class="src-link" :href="detail.source_url" target="_blank" rel="noopener">
            ↗ 在{{ detail.source }}查看
          </a>
        </div>
        <p class="tags" v-if="detail.spot.tags">标签：{{ detail.spot.tags.split(',').join(' / ') }}</p>
        <p class="desc">{{ detail.description || '景区简介待补充（后台可编辑）。' }}</p>
      </div>

      <div class="sec">游客评论 <small>{{ detail.comments.length }} 条</small></div>
      <div class="panel comments">
        <p v-if="!detail.comments.length" class="empty">还没有评论，来做第一个分享的人（登录后可评论，下一版开放）</p>
        <div v-for="cm in detail.comments" :key="cm.id" class="comment">
          <div class="c-head"><b>{{ cm.username }}</b><span class="c-time">{{ cm.created_at }}</span><span v-if="cm.rating" class="c-rate">{{ '★'.repeat(cm.rating) }}</span></div>
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
.fav-num { font-size: 13px; color: #c0392b; }
.fav-num b { font-size: 16px; }
.src-link {
  margin-left: auto; font-size: 12.5px; color: var(--green);
  border: 1px solid var(--green-border); background: var(--green-soft);
  padding: 5px 12px; border-radius: 20px;
}
.tags { font-size: 12.5px; color: #888; margin-top: 10px; }
.desc { font-size: 13.5px; line-height: 2; margin-top: 8px; color: #333; }
.comment { border-bottom: 1px dashed var(--line); padding: 10px 0; }
.comment:last-child { border: none; }
.c-head { display: flex; gap: 10px; font-size: 12.5px; margin-bottom: 4px; align-items: baseline; }
.c-time { color: #aaa; font-size: 11.5px; }
.c-rate { color: #e8a33d; }
.comment p { font-size: 13px; color: #444; }
@media (max-width: 640px) { .hero { height: 180px; } }
</style>
