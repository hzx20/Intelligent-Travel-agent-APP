<script setup>
/** 攻略编写/编辑（v0.8）：标题 + 正文 + 图集（首张为封面）+ 关联景点 + 发布/存草稿 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../store/auth'
import { registerAuthChangeGuard } from '../store/authChangeGuard'
import { guardTripUnload } from '../utils/tripDraftGuard'
import { newGuideCreation, readGuideCreation, saveGuideCreation, clearGuideCreation } from '../store/guideCreationAttempt'

const CITIES = ['', '成都', '杭州', '西安', '北京', '三亚']
const route = useRoute()
const router = useRouter()
const { user, openLogin } = useAuth()

const isEdit = computed(() => Boolean(route.params.id))
const blank = () => ({ title: '', content: '', city: '', is_draft: false })
const form = ref(blank())
const images = ref([])       // 图片 URL 列表，第 1 张自动作封面
const spots = ref([])        // 已选关联景点 [{id,name,image_url}]
const spotKeyword = ref('')
const spotResults = ref([])
const imgUrl = ref('')
const busy = ref(false)
const msg = ref('')
const loading = ref(false), loadError = ref(''), uncertain = ref(false)
const pendingCreation = ref(null), recoveryError = ref(''), baseVersion = ref(null), baseRevision = ref(''), conflict = ref(false)
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
let generation = 0, searchSequence = 0, unregisterAuthGuard = null, leavingAfterSave = false
const snapshot = () => JSON.stringify({ ...form.value, images:images.value, spot_ids:spots.value.map(s => s.id) })
const baseline = ref(snapshot())
const hasDraft = computed(() => Boolean(pendingCreation.value || recoveryError.value || uncertain.value || snapshot() !== baseline.value || imgUrl.value.trim()))
const ready = computed(() => Boolean(user.value && !loading.value && !loadError.value && !recoveryError.value))
const reviewUrl = computed(() => router.resolve(isEdit.value ? `/guides/${route.params.id}` : '/me').href)
const active = (run, id, uid) => run === generation && id === route.params.id && uid === user.value?.id
function confirmDeparture() {
  return leavingAfterSave || !(hasDraft.value || busy.value) || window.confirm('攻略还有未保存的输入或待确认的提交。离开后本页内容会丢失，确定离开吗？')
}
function beforeUnload(event) { guardTripUnload(event, hasDraft.value, busy.value) }
onBeforeRouteLeave(confirmDeparture)
onBeforeRouteUpdate(confirmDeparture)
onMounted(() => {
  window.addEventListener('beforeunload', beforeUnload)
  unregisterAuthGuard = registerAuthChangeGuard(confirmDeparture)
  if (!user.value) openLogin('login')
})
onBeforeUnmount(() => {
  generation++; searchSequence++
  window.removeEventListener('beforeunload', beforeUnload)
  unregisterAuthGuard?.()
})

async function loadGuide() {
  const run = ++generation, id = route.params.id, uid = user.value?.id
  searchSequence++
  form.value = blank(); images.value = []; spots.value = []
  imgUrl.value = ''; spotKeyword.value = ''; spotResults.value = []
  busy.value = false; uncertain.value = false; msg.value = ''; loadError.value = ''
  pendingCreation.value = null; recoveryError.value = ''; baseVersion.value = null; baseRevision.value = ''; conflict.value = false; leavingAfterSave = false
  baseline.value = snapshot(); loading.value = Boolean(id && uid)
  if (!uid) return
  if (!id) {
    try {
      const attempt = readGuideCreation(uid)
      if (attempt) {
        pendingCreation.value = attempt; uncertain.value = true
        const p = attempt.payload
        form.value = {title:p.title,content:p.content,city:p.city,is_draft:p.is_draft}
        images.value = [...p.images]; spots.value = p.spot_ids.map(id => ({id,name:`景点 #${id}`}))
      }
    } catch (e) { recoveryError.value = e.message }
    return
  }
  try {
    const g = await api.get(`/api/guides/${id}`)
    if (!active(run, id, uid)) return
    if (!g.is_owner) { loadError.value = '无权编辑这篇攻略。'; return }
    if (!Number.isSafeInteger(g.version) || g.version < 1 || !uuid.test(g.revision || '')) { loadError.value = '当前服务不支持攻略版本保护，请更新服务后重新加载。'; return }
    baseVersion.value = g.version; baseRevision.value = g.revision
    form.value = { title: g.title, content: g.content, city: g.city, is_draft: g.is_draft }
    images.value = [...(g.images || [])]
    spots.value = (g.spots || []).map(s => ({ id: s.id, name: s.name, image_url: s.image_url }))
    baseline.value = snapshot()
  } catch (e) {
    if (active(run, id, uid)) loadError.value = e.message
  } finally {
    if (active(run, id, uid)) loading.value = false
  }
}
watch(() => [route.params.id, user.value?.id], loadGuide, { immediate:true, flush:'sync' })

async function searchSpots() {
  if (!ready.value || busy.value || uncertain.value) return
  const run = generation, id = route.params.id, uid = user.value?.id, search = ++searchSequence
  const kw = spotKeyword.value.trim()
  spotResults.value = []
  if (!kw) { spotResults.value = []; return }
  try {
    const r = await api.get(`/api/spots?keyword=${encodeURIComponent(kw)}&page_size=8`)
    if (!active(run,id,uid) || search !== searchSequence || spotKeyword.value.trim() !== kw) return
    const picked = new Set(spots.value.map(s => s.id))
    spotResults.value = r.items.filter(s => !picked.has(s.id))
  } catch (e) { if (active(run,id,uid) && search === searchSequence) msg.value = e.message }
}

function pickSpot(s) {
  if (!ready.value || busy.value || uncertain.value) return
  if (spots.value.length >= 10) { msg.value = '最多关联 10 个景点'; return }
  spots.value.push({ id: s.id, name: s.name, image_url: s.image_url })
  spotResults.value = spotResults.value.filter(x => x.id !== s.id)
  spotKeyword.value = ''
  searchSequence++
  msg.value = ''
}

function addImage() {
  if (!ready.value || busy.value || uncertain.value) return
  const u = imgUrl.value.trim()
  if (!u) return
  if (images.value.length >= 30) { msg.value = '最多 30 张图片'; return }
  if (!images.value.includes(u)) images.value.push(u)
  imgUrl.value = ''
}

/** 从已关联的景点取图：景点库自带高德实拍图，省得用户自己找图床 */
function fillFromSpots() {
  if (!ready.value || busy.value || uncertain.value) return
  const picked = new Set(images.value)
  for (const s of spots.value) {
    if (s.image_url && !picked.has(s.image_url) && images.value.length < 30) {
      images.value.push(s.image_url)
      picked.add(s.image_url)
    }
  }
}

function setCover(i) {
  if (!ready.value || busy.value || uncertain.value) return
  if (i === 0) return
  const [u] = images.value.splice(i, 1)
  images.value.unshift(u)
}

async function submit(isDraft) {
  if (busy.value || (uncertain.value && !pendingCreation.value)) return
  if (!user.value) { openLogin('login'); return }
  if (!ready.value) return
  if (!form.value.title.trim()) { msg.value = '标题不能为空'; return }
  if (!form.value.content.trim()) { msg.value = '正文不能为空'; return }
  if (imgUrl.value.trim()) { msg.value = '还有图片链接未添加，请先添加或清空后保存。'; return }
  const run = generation, id = route.params.id, uid = user.value.id
  busy.value = true
  msg.value = ''
  let attempt = pendingCreation.value
  try {
    const capability = await api.get('/api/guides/write-capabilities')
    if (!active(run,id,uid)) return
    if (capability?.idempotent_creation !== true || capability?.versioned_writes !== true) throw new Error('当前服务不支持可靠攻略写入，请更新服务后重试。')
    let body = {
      title: form.value.title.trim(),
      content: form.value.content.trim(),
      city: form.value.city,
      images: [...images.value],
      spot_ids: spots.value.map(s => s.id),
      is_draft: isDraft,
    }
    if (!id) {
      if (!attempt) {
        const next = newGuideCreation(body)
        saveGuideCreation(uid,next)
        attempt = next; pendingCreation.value = next; uncertain.value = true
      }
      body = {...attempt.payload,request_id:attempt.request_id}
    } else { body.version = baseVersion.value; body.revision = baseRevision.value }
    const r = id ? await api.patch(`/api/guides/${id}/content`, body) : await api.post('/api/guides/create', body)
    if (!active(run,id,uid)) return
    if (!Number.isSafeInteger(r?.id) || r.id <= 0 || (id && r.id !== Number(id)) || typeof r.is_draft !== 'boolean' || !Number.isSafeInteger(r.version) || r.version < 1 || !uuid.test(r.revision || '') || (id && r.revision !== baseRevision.value)) {
      const error = new Error('提交结果暂时无法确认，请先核对云端攻略。')
      error.outcomeUnknown = true
      throw error
    }
    leavingAfterSave = true
    const failed = await router.push(r.is_draft && !id ? '/me' : `/guides/${r.id}`)
    if (!failed && attempt) { try { clearGuideCreation(uid,attempt.request_id) } catch { /* Retaining the key safely reopens this guide. */ } }
    if (active(run,id,uid)) {
      leavingAfterSave = false
      if (failed) msg.value = '攻略已保存，请继续打开或核对云端攻略。'
      else { form.value.is_draft = r.is_draft; baseVersion.value = r.version; baseline.value = snapshot(); pendingCreation.value = null; uncertain.value = false }
    }
  } catch (e) {
    if (active(run,id,uid)) {
      leavingAfterSave = false
      msg.value = [404,405,428].includes(e.status) ? '无法完成可靠攻略写入，请核对权限、攻略状态或更新服务后重试。' : e.message
      uncertain.value = Boolean(pendingCreation.value || e.outcomeUnknown || e.code?.startsWith('SESSION_'))
      conflict.value = Boolean(id && e.status === 409)
    }
  } finally {
    if (active(run,id,uid)) busy.value = false
  }
}

function allowRetry() {
  if (busy.value || !uncertain.value || !isEdit.value || !window.confirm('再次保存仍使用原版本；若云端已更新会拒绝覆盖。确定继续编辑吗？')) return
  uncertain.value = false; msg.value = ''
}
function abandonCreation() {
  if (busy.value || !user.value || !window.confirm('这次尝试可能已经创建攻略。放弃只清除本标签的重试信息，不删除云端攻略；请先核对，确定放弃吗？')) return
  try { clearGuideCreation(user.value.id,pendingCreation.value?.request_id); pendingCreation.value = null; recoveryError.value = ''; uncertain.value = false; msg.value = '' }
  catch { msg.value = '无法清除重试信息，请检查浏览器存储后再试。' }
}
async function reloadLatest() {
  if (busy.value || !window.confirm('重新载入会用云端最新内容替换本页输入。请先复制需要保留的正文，确定继续吗？')) return
  await loadGuide()
}
</script>

<template>
  <div class="wrap">
    <div class="crumb">{{ isEdit ? '编辑攻略' : '写攻略' }} / <b>{{ isEdit ? form.title || '…' : '新的一篇' }}</b></div>

    <p v-if="!user" class="loading">登录后编写攻略。<button @click="openLogin('login')">登录</button></p>
    <p v-else-if="loading" class="loading">加载中…</p>
    <p v-else-if="loadError" role="alert" class="err">{{ loadError }} <button @click="loadGuide">重新加载攻略</button></p>
    <p v-else-if="recoveryError" role="alert" class="err">{{ recoveryError }} <a :href="reviewUrl" target="_blank" rel="noopener">核对云端攻略</a> <button @click="abandonCreation">放弃本次创建</button></p>
    <div v-else class="editor">
      <fieldset :disabled="busy || uncertain" class="editor-fields">
      <input v-model="form.title" class="title" aria-label="攻略标题" placeholder="标题：3 天逛吃成都，这份排队攻略让你少走弯路…" maxlength="100" />
      <div class="row">
        <select v-model="form.city" class="fsel" aria-label="攻略城市">
          <option value="">选择城市（可留空）</option>
          <option v-for="c in CITIES.slice(1)" :key="c" :value="c">{{ c }}</option>
        </select>
        <span class="hint">攻略会按城市出现在列表筛选里</span>
      </div>
      <textarea
        v-model="form.content" class="body-text" aria-label="攻略正文"
        placeholder="正文从这里开始写，回车分段，发布后按段落展示…"
      />

      <div class="sec">📷 图集 <small>首张自动设为封面 · 最多 30 张</small></div>
      <div class="row">
        <input v-model="imgUrl" class="finput" aria-label="待添加图片链接" placeholder="粘贴图片链接（如高德景点图地址）后点添加" @keyup.enter="addImage" />
        <button class="btn-sm" @click="addImage">+ 添加</button>
        <button class="btn-sm hollow" @click="fillFromSpots">用关联景点图片填充</button>
      </div>
      <div v-if="images.length" class="thumbs">
        <div v-for="(u, i) in images" :key="u" class="thumb">
          <img :src="u" :alt="`图 ${i + 1}`" />
          <span v-if="i === 0" class="cover-tag">封面</span>
          <button v-else type="button" class="set" @click="setCover(i)">设为封面</button>
          <button type="button" class="x" :aria-label="`移除图片 ${i + 1}`" @click="images.splice(i, 1)">✕</button>
        </div>
      </div>
      <p v-else class="hint">还没有图片，可粘贴链接，或先关联景点后一键取图</p>

      <div class="sec">关联景点 <small>搜索后点选，最多 10 个</small></div>
      <div class="row">
        <input
          v-model="spotKeyword" class="finput" aria-label="搜索关联景点"
          placeholder="🔍 搜索景点名，如「宽窄巷子」"
          @keyup.enter="searchSpots" @input="searchSpots"
        />
        <button class="btn-sm" @click="searchSpots">搜索</button>
      </div>
      <div v-if="spotResults.length" class="pick">
        <button v-for="s in spotResults" :key="s.id" type="button" class="pick-item" @click="pickSpot(s)">+ {{ s.name }}</button>
      </div>
      <div v-if="spots.length" class="linkspot">
        <span v-for="s in spots" :key="s.id" class="lspot">{{ s.name }} <button type="button" :aria-label="`移除关联景点 ${s.name}`" @click="spots = spots.filter(x => x.id !== s.id)">✕</button></span>
      </div>

      <div class="actions">
        <button class="btn" :disabled="busy" @click="submit(false)">
          {{ busy ? '提交中…' : (isEdit ? '保存修改' : '发布攻略') }}
        </button>
        <button class="btn hollow" :disabled="busy" @click="submit(true)">存草稿</button>
        <span class="hint">草稿只有自己能看到，发布后所有人都可见</span>
      </div>
      </fieldset>
      <p v-if="msg" class="err" role="alert">{{ msg }}</p>
      <div v-if="pendingCreation" class="review"><p role="status">本次创建的信息已按账号保留在此标签页，刷新后可继续。继续会找回已创建的攻略或完成原创建，不会重复发布；其他未提交输入仍只在本页保留。</p><button type="button" :disabled="busy" @click="submit(pendingCreation.payload.is_draft)">继续这次创建</button><button type="button" :disabled="busy" @click="abandonCreation">放弃本次创建</button></div>
      <div v-if="uncertain || conflict" class="review"><p v-if="isEdit" role="status">保存结果需核对，输入仍保留。本页继续编辑会使用原版本，云端已更新时拒绝覆盖。刷新或离开会丢失未保存输入。</p><a :href="reviewUrl" target="_blank" rel="noopener">在新标签页核对云端攻略</a> <button v-if="isEdit && uncertain" type="button" :disabled="busy" @click="allowRetry">继续编辑原版本</button><button v-if="isEdit" type="button" :disabled="busy" @click="reloadLatest">重新载入最新攻略</button></div>
    </div>
  </div>
</template>

<style scoped>
.editor-fields{border:0;padding:0;margin:0;min-width:0}.editor-fields:disabled{opacity:.65}.review{font-size:12px;line-height:1.7;margin-top:12px;padding:14px;border:1px solid var(--green-border);border-radius:10px;background:var(--green-soft)}.review a{display:inline-block;color:var(--green);margin:8px 10px 0 0;text-decoration:underline}.review button{min-height:44px;margin:8px 10px 0 0;padding:9px 13px;border:1px solid var(--green-border);border-radius:8px;background:#fff;color:var(--green);font:inherit;cursor:pointer}.review button:disabled{opacity:.55;cursor:default}.lspot button,.set{border:0;background:none;font:inherit;color:inherit;cursor:pointer}.set{width:100%}
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
