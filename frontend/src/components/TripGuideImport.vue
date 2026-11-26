<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import { guestTrips } from '../store/guestTrips'
import { imageSelectionError, validImageText, validGuidePreview, validGuideApply, validSiteGuide, validSiteGuides, validPublicGuidePreview } from '../utils/guideImportData'
import { validWorkspaceTrip } from '../utils/tripWorkspace'
import { readWikivoyage, wikivoyageTitle } from '../utils/wikivoyage'

const props = defineProps({ trip: { type: Object, required: true }, busy: Boolean, local: Boolean, initialGuideId: { type: Number, default: null } })
const emit = defineEmits(['updated', 'dirty'])
const text = ref(''), source = ref(''), candidates = ref(null), previewVersion = ref(null)
const loading = ref(false), applying = ref(false), error = ref(''), message = ref('')
const imageFile = ref(null), imageText = ref(''), imageLoading = ref(false), imageReady = ref(false)
const pendingApply=ref(null), applyReview=ref(null), checkingApply=ref(false)
const siteKeyword = ref(''), sitePage = ref(1), siteGuides = ref(null), siteTotal = ref(0), siteSelected = ref(null), siteLoading = ref(false)
const siteOpen = ref(false)
const publicLink = ref(''), publicGuide = ref(null)
let generation = 0, siteGeneration = 0, imageGeneration = 0
const selected = computed(() => (candidates.value || []).filter(place => place.selected))
const working = computed(() => loading.value || applying.value || imageLoading.value || siteLoading.value || props.busy || checkingApply.value || Boolean(pendingApply.value))
watch(() => [props.trip.id, props.trip.version], ([id], [oldId]) => {
  generation++
  loading.value = false; applying.value = false;checkingApply.value=false;applyReview.value=null
  if (id !== oldId) {
    imageGeneration++;imageLoading.value=false
    pendingApply.value=null;applyReview.value=null;checkingApply.value=false
    text.value = ''; source.value = ''; candidates.value = null; previewVersion.value = null
    publicLink.value = ''; publicGuide.value = null
    imageFile.value = null; imageText.value = ''; imageReady.value = false
    siteGeneration++; siteSelected.value = null; siteGuides.value = null; siteTotal.value = 0; siteLoading.value = false
    error.value = ''; message.value = ''
  } else if (imageLoading.value) error.value = '行程已更新，正在等待本次图片识别结束；本次结果不会用于新版本。'
  else if(pendingApply.value)error.value='行程已更新，请先核对本次加入结果；原次提交内容仍保留。'
})
watch([text, source, publicLink, candidates, loading, applying, imageFile, imageText, imageLoading, siteSelected, siteLoading,pendingApply,checkingApply], () => emit('dirty', Boolean(text.value.trim() || source.value.trim() || publicLink.value.trim() || candidates.value || loading.value || applying.value || imageFile.value || imageText.value || imageLoading.value || siteSelected.value || siteLoading.value || pendingApply.value || checkingApply.value)), { immediate: true, flush: 'sync' })
watch(text, () => { generation++; candidates.value = null; previewVersion.value = null; publicGuide.value = null; loading.value = false; message.value = '' }, { flush: 'sync' })
watch(siteKeyword, () => { siteGeneration++; siteGuides.value = null; siteSelected.value = null; siteTotal.value = 0; siteLoading.value = false }, { flush: 'sync' })
onBeforeUnmount(() => { generation++; siteGeneration++; imageGeneration++ })

async function searchSiteGuides(page = 1) {
  if (working.value) return
  const run = ++siteGeneration, id = props.trip.id
  siteLoading.value = true; error.value = ''; siteSelected.value = null
  try {
    const params = new URLSearchParams({ keyword: siteKeyword.value.trim(), page: String(page) })
    const base = props.local ? '/api/trips/guest-guide/site-guides' : `/api/trips/${id}/guide-import/site-guides`
    const result = await api.get(`${base}?${params}`, { timeout: 10000 })
    if (run !== siteGeneration || props.trip.id !== id) return
    if(!validSiteGuides(result,page))throw Error('攻略列表内容无法确认，请重试。')
    siteGuides.value = result.items; sitePage.value = result.page; siteTotal.value = result.total
  } catch (e) { if (run === siteGeneration) error.value = e.message }
  finally { if (run === siteGeneration) siteLoading.value = false }
}
async function openSiteGuide(guide) {
  if (working.value) return
  const run = ++siteGeneration, id = props.trip.id
  siteLoading.value = true; error.value = ''; siteSelected.value = null
  try {
    const base = props.local ? '/api/trips/guest-guide/site-guides' : `/api/trips/${id}/guide-import/site-guides`
    const result = await api.get(`${base}/${guide.id}`, { timeout: 10000 })
    if (run === siteGeneration && props.trip.id === id) {
      if(!validSiteGuide(result,guide.id))throw Error('攻略正文无法确认，请重新选择。')
      siteSelected.value = result
    }
  } catch (e) { if (run === siteGeneration) error.value = e.message }
  finally { if (run === siteGeneration) siteLoading.value = false }
}
watch(() => props.initialGuideId, id => {
  if (!id) return
  siteOpen.value = true
  openSiteGuide({ id })
}, { immediate: true })
function useSiteGuide() {
  if (!siteSelected.value || working.value) return
  if ((text.value.trim() || source.value.trim() || candidates.value) && !window.confirm('使用站内攻略将替换当前正文、来源和识别预览。确定继续？')) return
  text.value = siteSelected.value.text
  source.value = `站内攻略 #${siteSelected.value.id}：${siteSelected.value.title}`.slice(0, 150)
  candidates.value = null; previewVersion.value = null; publicGuide.value = null
  siteSelected.value = null; error.value = ''
  message.value = text.value.length > 12000 ? '正文超过 12000 字，请先自行精简；内容未被截断。' : '正文已载入，请核对后再识别地点。'
}

function setPreview(result, guide = null) {
  previewVersion.value = result.version
  publicGuide.value = guide
  candidates.value = result.places.map(place => ({ ...place, selected: false,
    spot_id: place.status === 'matched' ? place.matches[0].id : null }))
}

async function previewPublicGuide() {
  if (!publicLink.value.trim() || working.value) return
  if (candidates.value !== null && !window.confirm('预览链接会替换当前地点预览和勾选，已粘贴的正文会保留。确定继续？')) return
  const run = ++generation, version = props.trip.version, id = props.trip.id
  const active = () => run === generation && id === props.trip.id && version === props.trip.version
  loading.value = true; error.value = ''; message.value = ''
  try {
    const url = publicLink.value.trim()
    const title = wikivoyageTitle(url)
    let browser_snapshot
    try {
      browser_snapshot = await readWikivoyage(title)
    } catch {
      // The server uses the same public source when this browser cannot reach it.
    }
    if (!active()) return
    const body = browser_snapshot ? { url, version, browser_snapshot } : { url, version }
    const options = { timeout: 15000, readOnly: true }
    const result = props.local
      ? await api.post('/api/trips/guest-guide/public-link-preview', { ...body, destination: props.trip.destination || '' }, options)
      : await api.post('/api/trips/' + id + '/guide-import/public-link-preview', body, options)
    if (!active()) return
    if (!validPublicGuidePreview(result, version, props.local)) throw Error('链接预览内容无法确认，请重试。')
    setPreview(result, result.guide)
  } catch (e) { if (active()) error.value = e.message }
  finally { if (active()) loading.value = false }
}

async function preview() {
  if (!text.value.trim() || text.value.length > 12000 || working.value) return
  if(candidates.value!==null&&!window.confirm('重新识别地点会替换当前地点预览和勾选，确定继续？'))return
  const run = ++generation, version = props.trip.version, id = props.trip.id
  const active = () => run === generation && props.trip.id === id && props.trip.version === version
  loading.value = true; error.value = ''; message.value = ''
  try {
    const result = props.local
      ? await api.post('/api/trips/guest-guide-preview', {version, text:text.value, destination:props.trip.destination || ''}, {timeout:130000})
      : await api.post(`/api/trips/${id}/guide-import/preview`, { version, text: text.value }, {timeout:130000})
    if (!active()) return
    if(!validGuidePreview(result,version,props.local))throw Error('地点预览内容无法确认，请保留正文并重试。')
    setPreview(result)
  } catch (e) { if (active()) error.value = e.message }
  finally { if (active()) loading.value = false }
}
async function apply() {
  if(applying.value||loading.value||imageLoading.value||checkingApply.value||props.busy)return
  if(pendingApply.value&&applyReview.value?.version!==pendingApply.value.version)return
  if(!pendingApply.value&&(!selected.value.length||previewVersion.value!==props.trip.version))return
  const run = ++generation, id = props.trip.id, version = props.trip.version
  const active = () => run === generation && props.trip.id === id && props.trip.version === version
  applying.value = true; error.value = ''
  try {
    const guideSource = publicGuide.value ? '维基导游 · CC BY-SA 4.0 · ' + publicGuide.value.url : source.value.trim()
    const body = pendingApply.value?.body || {version: previewVersion.value, source: guideSource || '用户粘贴攻略',
      places: selected.value.map(({ name, note, spot_id, matches }) => {
        if (!props.local) return {name, note, spot_id}
        const match = spot_id ? matches.find(candidate => candidate.id === spot_id) : null
        if (spot_id && !match) throw new Error('所选地点已不在预览中，请重新识别')
        return {name, note, match}
      })}
    if(!pendingApply.value)pendingApply.value={body:JSON.parse(JSON.stringify(body)),version:body.version}
    applyReview.value=null
    const result = props.local ? await guestTrips.importGuide(id, body)
      : await api.post(`/api/trips/${id}/guide-import/apply`, body)
    if (!active()) return
    if(!validGuideApply(result,id,body.version,body.places.length))throw Error('加入回执无法确认，请核对待安排清单；本次内容仍保留。')
    pendingApply.value=null
    candidates.value = null
    if (publicGuide.value) { publicGuide.value = null; publicLink.value = '' }
    applying.value = false
    message.value = `已新增 ${result.added} 个地点，合并 ${result.merged} 条备注或来源，${result.unchanged} 条已有内容保持不变。`
    emit('updated', result.trip)
  } catch (e) { if (active()) error.value = e.message }
  finally { if (active()) applying.value = false }
}
async function checkApply(){
  if(!pendingApply.value||applying.value||checkingApply.value||props.busy)return
  const run=++generation,id=props.trip.id
  checkingApply.value=true;error.value='';applyReview.value=null
  try{
    const result=props.local?await guestTrips.get(id):await api.get(`/api/trips/${id}`)
    if(run!==generation||id!==props.trip.id)return
    if(!validWorkspaceTrip(result,id))throw Error('待安排清单无法确认，请稍后再核对。')
    applyReview.value=result
  }catch(e){if(run===generation)error.value=e.message}
  finally{if(run===generation)checkingApply.value=false}
}
function endApplyReview(){
  if(applying.value||checkingApply.value||!window.confirm('结束核对只会清除本页的本次提交记录，不会撤销可能已经加入的地点。正文和预览仍会保留。确定结束？'))return
  pendingApply.value=null;applyReview.value=null;error.value=''
}
function selectAll(value) { if (!working.value) for (const item of candidates.value || []) item.selected = value }
function chooseImage(event) {
  const file=event.target.files?.[0];event.target.value=''
  if(working.value||!file)return
  const invalid=imageSelectionError(file);if(invalid){error.value=invalid;return}
  if(imageReady.value&&!window.confirm('更换图片会清除当前图片的识别文字，正文和来源会保留。确定更换？'))return
  imageGeneration++;imageFile.value=file;imageText.value='';imageReady.value=false;error.value='';message.value=''
}
function clearImage(){if(working.value)return;imageGeneration++;imageFile.value=null;imageText.value='';imageReady.value=false;error.value='';message.value=''}
async function readImage() {
  const file = imageFile.value
  if (!file || working.value) return
  const invalid=imageSelectionError(file);if(invalid){error.value=invalid;return}
  if(imageReady.value&&!window.confirm('重新识别成功后会替换当前核对文字；识别可能再次计费。确定继续？'))return
  const run = ++imageGeneration, version = props.trip.version, id = props.trip.id
  const active = () => run === imageGeneration && props.trip.id === id
  imageLoading.value = true; error.value = '';message.value=''
  try {
    const encoded = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => {const value=String(reader.result).split(',')[1];if(value)resolve(value);else reject(new Error('读取图片失败'))}; reader.onerror = () => reject(new Error('读取图片失败'));reader.onabort=()=>reject(new Error('图片读取已中断，请重新选择')); reader.readAsDataURL(file) })
    if (!active()) return
    if(props.trip.version!==version)throw Error('行程已更新，本次图片没有发送，请核对后重新识别。')
    const path = props.local ? '/api/trips/guest-guide-image-text' : `/api/trips/${id}/guide-import/image-text`
    const result = await api.post(path, { version, image: encoded }, { timeout: 60000 })
    if (!active()) return
    if(props.trip.version!==version)throw Error('行程已更新，请核对当前内容后重新识别图片。')
    if(!validImageText(result,version))throw Error('图片识别回执无法确认，请保留原文并重试。')
    imageText.value = result.text; imageReady.value = true
    if(!result.text.trim())message.value='没有识别到文字，可以换一张清晰图片或手动粘贴正文。'
  } catch (e) { if (active()) error.value = e.message }
  finally { if (active()) imageLoading.value = false }
}
function useImageText() {
  if (working.value || !imageReady.value || !imageText.value.trim()) return
  const combined = [text.value.trim(), imageText.value.trim()].filter(Boolean).join('\n')
  if (combined.length > 12000) { error.value = '合并后的正文超过 12000 字，请先精简'; return }
  text.value = combined; imageText.value = ''; imageFile.value = null; imageReady.value = false
  if(!source.value.trim())source.value='图片识别文字（用户核对）'
  message.value='识别文字已加入正文。请核对后再识别地点；尚未向待安排加入地点。'
}
</script>

<template>
  <section id="guide-import" class="guide-import">
    <p class="eyebrow">COLLECT YOUR INSPIRATION</p>
    <h2>从攻略收集地点</h2>
    <p class="intro">{{ local ? '选择公开攻略或粘贴正文，再勾选想去的地点；确认后仅保存到本机旅行。' : '选择站内攻略、粘贴正文或识别图片，再勾选想去的地点；未匹配的地点也可以保留待确认。' }}</p>
    <details class="site-import" :open="siteOpen" @toggle="siteOpen = $event.target.open"><summary>选择站内攻略</summary><fieldset :disabled="working"><p class="intro">{{ local ? "本机旅行只可选择公开攻略，正文需主动载入和识别。" : "可选公开攻略或有权查看的草稿。选择正文不会立即识别；导入的地点及来源可被同行编辑者查看。" }}</p><div class="site-search"><input v-model="siteKeyword" maxlength="100" aria-label="搜索站内攻略" placeholder="标题或城市（可留空）" @keydown.enter.prevent="searchSiteGuides(1)"><button type="button" :disabled="siteLoading" @click="searchSiteGuides(1)">{{ siteLoading ? '加载中…' : '查找攻略' }}</button></div><p v-if="siteGuides && !siteGuides.length" class="intro">没有找到可查看的攻略。</p><div v-for="guide in siteGuides || []" :key="guide.id" class="site-row"><span><b>{{ guide.title }}</b><small>{{ guide.city || '城市未填' }}{{ guide.is_draft ? ' · 草稿' : ' · 公开' }}</small></span><button type="button" :disabled="siteLoading" @click="openSiteGuide(guide)">预览正文</button></div><div v-if="siteTotal > 10" class="site-pages"><button type="button" :disabled="siteLoading || sitePage <= 1" @click="searchSiteGuides(sitePage - 1)">上一页</button><span>第 {{ sitePage }} 页</span><button type="button" :disabled="siteLoading || sitePage * 10 >= siteTotal" @click="searchSiteGuides(sitePage + 1)">下一页</button></div><div v-if="siteSelected" class="site-preview"><b>{{ siteSelected.title }}</b><label>正文预览<textarea :value="siteSelected.text" readonly rows="5"></textarea></label><button type="button" @click="useSiteGuide">使用此攻略正文</button><button type="button" @click="siteSelected = null">关闭预览</button></div></fieldset></details>
    <div class="image-import"><fieldset :disabled="working"><label>从图片识别文字 <input type="file" accept="image/png,image/jpeg" @change="chooseImage"></label><p v-if="imageFile" class="selected-image" role="status">已选择：{{ imageFile.name || '攻略图片' }} · {{ Math.ceil(imageFile.size / 1024) }} KB</p><p class="intro">选择 PNG 或 JPEG，最大 4 MB、2000 万像素。点击识别后图片会发送至智谱 OCR 服务；识别可能产生服务费用。</p><div class="image-actions"><button type="button" :disabled="!imageFile || imageLoading || busy" @click="readImage">{{ imageLoading ? '识别图片中…' : '识别图片文字' }}</button><button v-if="imageFile || imageReady" type="button" @click="clearImage">清空图片与识别文字</button></div><div v-if="imageReady"><label>核对识别文字<textarea v-model="imageText" maxlength="12000" rows="4"></textarea></label><p class="intro">识别可能有误，请核对；确认后文字会加入下方正文，不会立即导入地点。</p><button type="button" :disabled="!imageText.trim()" @click="useImageText">加入攻略正文</button></div></fieldset></div>
    <details class="site-import public-import">
      <summary>从维基导游链接收集</summary>
      <form @submit.prevent="previewPublicGuide">
        <fieldset :disabled="working">
          <p class="intro">从中文维基导游读取已标记的地点名称。浏览器优先读取公开页面，确认后加入待安排。</p>
          <label class="detail">维基导游链接
            <input v-model="publicLink" type="url" maxlength="1024" placeholder="https://zh.wikivoyage.org/wiki/杭州" required>
          </label>
          <button type="submit" :disabled="!publicLink.trim()">预览链接地点</button>
        </fieldset>
      </form>
    </details>
    <form @submit.prevent="preview"><fieldset :disabled="working">
      <label>攻略正文<textarea v-model="text" maxlength="12000" rows="5" required placeholder="粘贴你有权使用的攻略文字"></textarea></label>
      <label>来源备注 <input v-model="source" maxlength="150" placeholder="例如：朋友的杭州攻略（可留空）"></label>
      <p v-if="text.length > 12000" class="error" role="alert">正文超过 12000 字，请先自行精简后识别；内容没有被截断。</p>
      <div class="form-bottom"><small>正文会发送给已配置的 AI 服务，识别可能产生服务费用{{ local ? "；目的地会发送至服务器用于匹配" : "" }}。外部链接不会被自动访问。</small><button :disabled="loading || applying || busy || text.length > 12000">{{ loading ? '正在识别…' : '识别攻略地点' }}</button></div></fieldset>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="message" class="success" role="status">{{ message }}</p>
    <div v-if="candidates !== null" class="review"><fieldset :disabled="working"><p v-if="previewVersion !== trip.version" class="error">行程已更新，识别结果已过期；请重新识别后导入。</p>
      <div class="review-heading"><h3>确认识别结果</h3><span>已选 {{ selected.length }} / {{ candidates.length }}</span></div>
      <p v-if="publicGuide" class="intro">
        维基导游 · <a :href="publicGuide.url" target="_blank" rel="noopener noreferrer">{{ publicGuide.title }}原文及贡献记录</a> ·
        <a :href="publicGuide.license_url" target="_blank" rel="noopener noreferrer">CC BY-SA 4.0</a>
        <span> · 已提取地点名称</span>
      </p>
      <p v-if="!candidates.length" class="intro">{{ publicGuide ? '这篇攻略没有标记可导入的地点。' : '没有识别到原文中明确的地点，请尝试补充正文。' }}</p>
      <template v-else>
        <p class="intro">请核对名称和备注。相同 POI 会合并备注与来源；未匹配地点会以无坐标状态保留。</p>
        <div class="selection"><button type="button" @click="selectAll(true)">全选</button><button type="button" @click="selectAll(false)">取消全选</button></div>
        <div v-for="(place, index) in candidates" :key="index" class="candidate">
          <label class="check"><input v-model="place.selected" type="checkbox"><b>{{ place.name }}</b><span>{{ place.status === 'matched' ? '精确匹配' : place.status === 'ambiguous' ? '请确认地点' : '待确认' }}</span></label>
          <label v-if="place.matches.length" class="detail">关联地点
            <select :value="place.spot_id ?? ''" @change="place.spot_id = $event.target.value ? Number($event.target.value) : null">
              <option value="">暂不关联，作为待确认地点</option>
              <option v-for="match in place.matches" :key="match.id" :value="match.id">{{ match.city }} {{ match.district }} {{ match.address || match.name }}</option>
            </select>
            <small v-if="place.matches_truncated">匹配结果较多，仅展示前 10 条；可暂不关联。</small>
          </label>
          <label class="detail">攻略备注<textarea v-model="place.note" maxlength="2000" rows="2" placeholder="可编辑或留空"></textarea></label>
        </div>
        <button v-if="!pendingApply" class="apply" :disabled="!selected.length || loading || applying || busy || previewVersion !== trip.version" @click="apply">{{ applying ? '正在加入…' : `加入待安排（${selected.length}）` }}</button>
      </template></fieldset>
    </div>
    <section v-if="pendingApply" class="import-check" aria-label="核对本次攻略导入"><h3>核对本次加入</h3><p>刚才提交的地点与来源已保留。先核对待安排清单，再决定是否需要继续；核对不会重复加入地点。</p><p v-if="applying" role="status">正在提交本次地点…</p><button type="button" :disabled="applying || checkingApply || busy" @click="checkApply">{{ checkingApply ? '正在核对…' : '核对待安排清单' }}</button><div v-if="applyReview" class="reviewed-places"><p role="status">已读取当前待安排清单。{{ applyReview.version === pendingApply.version ? '继续将提交刚才确认的内容。' : '行程已有更新，请核对清单并刷新旅行，再决定是否需要重新识别。' }}</p><ul v-if="applyReview.places.length"><li v-for="place in applyReview.places" :key="place.id">{{ place.name }}<small v-if="place.note"> · {{ place.note }}</small></li></ul><p v-else>当前没有待安排地点。</p><button v-if="applyReview.version === pendingApply.version" type="button" :disabled="applying || checkingApply || busy" @click="apply">继续原次加入</button></div><button type="button" :disabled="applying || checkingApply || busy" @click="endApplyReview">结束本次核对</button></section>
  </section>
</template>

<style scoped>
.guide-import fieldset{border:0;padding:0;margin:0;min-width:0}.site-import{padding:12px;background:#f5f8f4;border-radius:10px;margin:12px 0}.site-import summary{cursor:pointer;color:#1a6e50;font-weight:700}.site-search{display:flex;gap:8px}.site-search input{min-width:0;flex:1}.site-import button{border:0;border-radius:8px;background:#e7f2ea;color:#1a6e50;padding:8px 10px;font-weight:700;white-space:nowrap}.site-import button:disabled{opacity:.5}.site-row{display:flex;align-items:center;justify-content:space-between;gap:8px;border-top:1px solid #e0ebe2;padding:10px 0;font-size:12px}.site-row span{min-width:0;overflow-wrap:anywhere}.site-row small{display:block;color:#82948a;margin-top:5px}.site-pages{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px}.site-preview{border-top:1px solid #dce7df;margin-top:10px;padding-top:12px;font-size:12px}.site-preview label{display:grid;gap:6px;margin:8px 0}.site-preview button+button{margin-left:8px}.image-import{padding:12px;background:#f5f8f4;border-radius:10px;margin:12px 0}.image-import label{display:grid;gap:6px;font-size:12px;font-weight:700}.image-import button{border:0;border-radius:8px;background:#e7f2ea;color:#1a6e50;padding:9px 12px;font-weight:700}.image-import button:disabled{opacity:.5}.image-import textarea{margin-top:6px}
.guide-import{scroll-margin-top:70px;padding:24px;border-top:1px solid #e8eee9}.eyebrow{font-size:10px;color:#789487;letter-spacing:.14em;font-weight:bold}.guide-import h2{font-size:19px;margin:6px 0}.intro{font-size:12px;line-height:1.6;color:#788c80;margin:8px 0 12px}.guide-import form>label,.detail{display:grid;gap:6px;font-size:12px;font-weight:700;margin:12px 0}.guide-import textarea,.guide-import input:not([type=checkbox]),.guide-import select{width:100%;border:1px solid #dbe5de;border-radius:8px;padding:10px;font:inherit;line-height:1.5;background:#fff}.guide-import textarea{resize:vertical}.form-bottom{display:flex;justify-content:space-between;align-items:center;gap:12px}.form-bottom small,.detail small{color:#8b9c91;font-weight:normal;line-height:1.5}.form-bottom button,.apply{border:0;border-radius:8px;background:#1a6e50;color:#fff;padding:10px 14px;font-weight:bold;white-space:nowrap}.form-bottom button:disabled,.apply:disabled{opacity:.5}.review{background:#f5f8f4;border-radius:10px;padding:15px;margin-top:18px}.review-heading{display:flex;justify-content:space-between;align-items:center;gap:10px}.review-heading h3{font-size:15px}.review-heading span,.check span{font-size:11px;color:#7c9483}.selection{display:flex;gap:12px}.selection button{border:0;background:none;color:#1a6e50;font-size:12px;font-weight:bold}.candidate{padding:12px 0 2px;border-bottom:1px solid #e0e9e1}.check{display:flex;align-items:center;gap:8px;font-size:13px}.check span{margin-left:auto}.detail{color:#71867a;margin:8px 0 12px}.detail textarea,.detail select{font-weight:normal}.apply{margin-top:15px}.error,.success{font-size:12px;margin-top:10px;line-height:1.6}.error{color:#a33}.success{color:#1a6e50}@media(max-width:760px){.guide-import{padding:20px 16px}.form-bottom{align-items:start;flex-direction:column}}
</style>

<style scoped>
.image-actions{display:flex;gap:8px;flex-wrap:wrap}.selected-image{font-size:12px;line-height:1.7;overflow-wrap:anywhere;margin:8px 0;color:#315f46}.image-import input{max-width:100%;min-width:0}.import-check{margin-top:16px;padding:16px;border:1px solid #dce7df;border-radius:10px;background:#f5f8f4;overflow-wrap:anywhere}.import-check h3{font-size:16px}.import-check p,.import-check li{font-size:12px;line-height:1.7;margin:8px 0}.import-check ul{padding-left:20px}.guide-import button{min-height:44px}.import-check button{margin:6px 8px 0 0;border:1px solid #cbded2;border-radius:8px;padding:10px 12px;background:white;color:#1a6e50;cursor:pointer}.import-check button:disabled{opacity:.5;cursor:wait}.guide-import :focus-visible{outline:2px solid #4e936d;outline-offset:3px}
</style>
