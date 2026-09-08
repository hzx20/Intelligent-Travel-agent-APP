<script setup>
/** 攻略编写/编辑（v0.8）：标题 + 正文 + 图集（首张为封面）+ 关联景点 + 发布/存草稿 */
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../store/auth'

const CITIES = ['', '成都', '杭州', '西安', '北京', '三亚']
const route = useRoute()
const router = useRouter()
const { user, openLogin } = useAuth()

const isEdit = !!route.params.id
const form = ref({ title: '', content: '', city: '', is_draft: false })
const images = ref([])       // 图片 URL 列表，第 1 张自动作封面
const spots = ref([])        // 已选关联景点 [{id,name,image_url}]
const spotKeyword = ref('')
const spotResults = ref([])
const imgUrl = ref('')
const busy = ref(false)
const msg = ref('')
const loading = ref(isEdit)

async function loadGuide() {
  try {
    const g = await api.get(`/api/guides/${route.params.id}`)
    if (!g.is_owner) { router.replace(`/guides/${g.id}`); return }
    form.value = { title: g.title, content: g.content, city: g.city, is_draft: g.is_draft }
    images.value = [...(g.images || [])]
    spots.value = (g.spots || []).map(s => ({ id: s.id, name: s.name, image_url: s.image_url }))
  } catch (e) {
    msg.value = e.message
  } finally {
    loading.value = false
  }
}

async function searchSpots() {
  const kw = spotKeyword.value.trim()
  if (!kw) { spotResults.value = []; return }
  const r = await api.get(`/api/spots?keyword=${encodeURIComponent(kw)}&page_size=8`)
  const picked = new Set(spots.value.map(s => s.id))
  spotResults.value = r.items.filter(s => !picked.has(s.id))
}

function pickSpot(s) {
  if (spots.value.length >= 10) { msg.value = '最多关联 10 个景点'; return }
  spots.value.push({ id: s.id, name: s.name, image_url: s.image_url })
  spotResults.value = spotResults.value.filter(x => x.id !== s.id)
  spotKeyword.value = ''
  msg.value = ''
}

function addImage() {
  const u = imgUrl.value.trim()
  if (!u) return
  if (images.value.length >= 30) { msg.value = '最多 30 张图片'; return }
  if (!images.value.includes(u)) images.value.push(u)
  imgUrl.value = ''
}

/** 从已关联的景点取图：景点库自带高德实拍图，省得用户自己找图床 */
function fillFromSpots() {
  const picked = new Set(images.value)
  for (const s of spots.value) {
    if (s.image_url && !picked.has(s.image_url) && images.value.length < 30) {
      images.value.push(s.image_url)
      picked.add(s.image_url)
    }
  }
}

function setCover(i) {
  if (i === 0) return
  const [u] = images.value.splice(i, 1)
  images.value.unshift(u)
}

async function submit(isDraft) {
  if (!user.value) { openLogin('login'); return }
  if (!form.value.title.trim()) { msg.value = '标题不能为空'; return }
  if (!form.value.content.trim()) { msg.value = '正文不能为空'; return }
  busy.value = true
  msg.value = ''
  try {
    const body = {
      title: form.value.title.trim(),
      content: form.value.content.trim(),
      city: form.value.city,
      images: images.value,
      spot_ids: spots.value.map(s => s.id),
      is_draft: isDraft,
    }
    if (isEdit) {
      await api.patch(`/api/guides/${route.params.id}`, body)
      router.push(`/guides/${route.params.id}`)
    } else {
      const r = await api.post('/api/guides', body)
      router.push(r.is_draft ? '/me' : `/guides/${r.id}`)
    }
  } catch (e) {
    msg.value = e.message
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  if (isEdit) loadGuide()
  else if (!user.value) openLogin('login')
})
</script>

<template>
  <div class="wrap">
    <div class="crumb">{{ isEdit ? '编辑攻略' : '写攻略' }} / <b>{{ isEdit ? form.title || '…' : '新的一篇' }}</b></div>

    <p v-if="loading" class="loading">加载中…</p>
    <div v-else class="editor">
      <input v-model="form.title" class="title" placeholder="标题：3 天逛吃成都，这份排队攻略让你少走弯路…" maxlength="100" />
      <div class="row">
        <select v-model="form.city" class="fsel">
          <option value="">选择城市（可留空）</option>
          <option v-for="c in CITIES.slice(1)" :key="c" :value="c">{{ c }}</option>
        </select>
        <span class="hint">攻略会按城市出现在列表筛选里</span>
      </div>
      <textarea
        v-model="form.content" class="body-text"
        placeholder="正文从这里开始写，回车分段，发布后按段落展示…"
      />

      <div class="sec">📷 图集 <small>首张自动设为封面 · 最多 30 张</small></div>
      <div class="row">
        <input v-model="imgUrl" class="finput" placeholder="粘贴图片链接（如高德景点图地址）后点添加" @keyup.enter="addImage" />
        <button class="btn-sm" @click="addImage">+ 添加</button>
        <button class="btn-sm hollow" @click="fillFromSpots">用关联景点图片填充</button>
      </div>
      <div v-if="images.length" class="thumbs">
        <div v-for="(u, i) in images" :key="u" class="thumb">
          <img :src="u" :alt="`图 ${i + 1}`" />
          <span v-if="i === 0" class="cover-tag">封面</span>
          <span v-else class="set" @click="setCover(i)">设为封面</span>
          <span class="x" @click="images.splice(i, 1)">✕</span>
        </div>
      </div>
      <p v-else class="hint">还没有图片，可粘贴链接，或先关联景点后一键取图</p>

      <div class="sec">关联景点 <small>搜索后点选，最多 10 个</small></div>
      <div class="row">
        <input
          v-model="spotKeyword" class="finput"
          placeholder="🔍 搜索景点名，如「宽窄巷子」"
          @keyup.enter="searchSpots" @input="searchSpots"
        />
        <button class="btn-sm" @click="searchSpots">搜索</button>
      </div>
      <div v-if="spotResults.length" class="pick">
        <span v-for="s in spotResults" :key="s.id" class="pick-item" @click="pickSpot(s)">+ {{ s.name }}</span>
      </div>
      <div v-if="spots.length" class="linkspot">
        <span v-for="s in spots" :key="s.id" class="lspot">{{ s.name }} <i @click="spots = spots.filter(x => x.id !== s.id)">✕</i></span>
      </div>

      <p v-if="msg" class="err">{{ msg }}</p>
      <div class="actions">
        <button class="btn" :disabled="busy" @click="submit(false)">
          {{ busy ? '提交中…' : (isEdit ? '保存修改' : '发布攻略') }}
        </button>
        <button class="btn hollow" :disabled="busy" @click="submit(true)">存草稿</button>
        <span class="hint">草稿只有自己能看到，发布后所有人都可见</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.crumb { font-size: 12.5px; color: var(--text-sub); margin-bottom: 10px; }
.editor {
  background: var(--card); border: 1px solid var(--line);
  border-radius: var(--radius); padding: 18px 20px 22px;
}
.title {
  width: 100%; border: 1px solid var(--line); border-radius: 10px;
  padding: 11px 13px; font-size: 15.5px; font-family: inherit; font-weight: 600;
}
.title:focus, .body-text:focus, .finput:focus { outline: none; border-color: var(--green); }
.body-text {
  width: 100%; min-height: 260px; margin-top: 12px;
  border: 1px solid var(--line); border-radius: 10px; padding: 12px 13px;
  font-size: 14px; line-height: 1.85; font-family: inherit; resize: vertical;
}
.row { display: flex; gap: 8px; align-items: center; margin-top: 10px; flex-wrap: wrap; }
.finput {
  flex: 1; min-width: 200px; border: 1px solid var(--line); border-radius: 10px;
  padding: 8px 11px; font-size: 13.5px; font-family: inherit;
}
.fsel {
  border: 1px solid var(--line); border-radius: 10px; padding: 8px 10px;
  font-size: 13px; font-family: inherit; background: #fff; color: #555;
}
.hint { font-size: 12px; color: var(--text-sub); }
.btn-sm {
  border: none; border-radius: 9px; padding: 8px 14px;
  font-size: 13px; background: var(--green); color: #fff; white-space: nowrap;
}
.btn-sm.hollow { background: #fff; color: var(--green); border: 1px solid var(--green-border); }

.thumbs { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.thumb { position: relative; width: 96px; }
.thumb img { width: 96px; height: 66px; object-fit: cover; border-radius: 8px; border: 1px solid var(--line); }
.thumb .x {
  position: absolute; top: -6px; right: -6px; width: 18px; height: 18px;
  background: #fff; border: 1px solid var(--line); border-radius: 50%;
  font-size: 11px; display: flex; align-items: center; justify-content: center; color: #c0392b;
}
.cover-tag, .set {
  display: block; text-align: center; font-size: 11px; margin-top: 3px; color: var(--green);
}
.set { cursor: pointer; color: var(--text-sub); }
.set:hover { color: var(--green); }

.pick { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 9px; }
.pick-item {
  font-size: 12.5px; padding: 5px 11px; border-radius: 20px; cursor: pointer;
  background: #fff; border: 1px dashed var(--green-border); color: var(--green);
}
.pick-item:hover { background: var(--green-soft); }
.linkspot { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 9px; }
.lspot {
  font-size: 12.5px; padding: 5px 12px; border-radius: 20px;
  background: var(--green-soft); border: 1px solid var(--green-border); color: var(--green);
}
.lspot i { cursor: pointer; font-style: normal; margin-left: 3px; }

.err { color: #c0392b; font-size: 12.5px; margin-top: 12px; }
.actions { display: flex; gap: 10px; align-items: center; margin-top: 18px; flex-wrap: wrap; }
.btn {
  border: none; border-radius: 9px; padding: 10px 20px;
  font-size: 14px; background: var(--green); color: #fff;
}
.btn.hollow { background: #fff; color: var(--green); border: 1px solid var(--green-border); }
.btn:disabled { opacity: .6; }

@media (max-width: 640px) {
  .editor { padding: 14px 12px 18px; }
  .body-text { min-height: 200px; }
}
</style>
