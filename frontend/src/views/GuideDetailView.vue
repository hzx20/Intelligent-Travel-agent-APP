<script setup>
/** 攻略详情（v0.8）：图片画廊 + 纯正文两区块 + 关联景点胶囊（对齐原型第 6 页） */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'

const route = useRoute()
const router = useRouter()
const guide = ref(null)
const error = ref('')
const gi = ref(0) // 当前画廊图片下标

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
  try {
    guide.value = await api.get(`/api/guides/${route.params.id}`)
    gi.value = 0
  } catch (e) {
    error.value = e.message
  }
}

async function removeGuide() {
  if (!confirm('删除后不可恢复，确定要删除这篇攻略吗？')) return
  try {
    await api.del(`/api/guides/${guide.value.id}`)
    router.push('/guides')
  } catch (e) {
    alert(e.message)
  }
}

onMounted(load)
</script>

<template>
  <div class="wrap">
    <p v-if="error" class="empty">{{ error }}</p>
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

        <!-- 区块一：图片画廊（与正文完全分离） -->
        <div class="gallery">
          <div class="glabel">🖼️ 图片画廊 · 共 {{ images.length }} 图</div>
          <div v-if="images.length" class="gwrap">
            <img class="gmain" :src="images[gi]" :alt="`图 ${gi + 1}`" />
            <span class="gbtn l" @click="gshow(gi - 1)">‹</span>
            <span class="gbtn r" @click="gshow(gi + 1)">›</span>
          </div>
          <div v-else class="gwrap">
            <div class="gmain ph">暂无图片 · 图占位</div>
          </div>
          <div v-if="images.length > 1" class="gthumbs">
            <img
              v-for="(u, i) in images" :key="i"
              class="gt" :class="{ on: i === gi }" :src="u" :alt="`缩略图 ${i + 1}`"
              @click="gshow(i)"
            />
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
          <span
            v-for="s in guide.spots" :key="s.id" class="lspot"
            @click="router.push(`/spots/${s.id}`)"
          >📍 {{ s.name }}</span>
        </div>

        <div v-if="guide.is_owner" class="actions">
          <button class="btn" @click="router.push(`/guides/${guide.id}/edit`)">✏️ 编辑</button>
          <button class="btn ghost" @click="removeGuide">🗑 删除</button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
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
  width: 34px; height: 34px; border-radius: 50%;
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
