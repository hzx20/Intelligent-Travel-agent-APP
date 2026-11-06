<script setup>
/** 目的地联想选择框：输入一个字即实时下拉匹配的市 / 区候选。
 *  候选数据来自 /api/spots/regions（与景点数据底座自动同步）；
 *  接口失败时降级为空列表提示，不阻断聊天主流程。
 *  选中后向上抛 {city, district}，city 为纯城市名（下游地图核实按它精确匹配）。 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api } from '../api'

/** initial：外部带进来的初始城市（首页发令台 / 规划页预填时用），有值就当已选中回显
 *  placement：下拉弹出方向。'up' 向上（聊天页输入框在底部，必须向上）；'down' 向下（首页首屏上方空旷） */
const props = defineProps({
  initial: { type: String, default: '' },
  placement: { type: String, default: 'up' },
  placeholder: { type: String, default: '想去哪？输入市 / 区名，如：成都' },
})

const emit = defineEmits(['select'])

const query = ref('')        // 输入关键词 / 已选项的回显
const open = ref(false)      // 下拉开合
const activeIdx = ref(-1)    // 键盘 ↑↓ 高亮项
const regions = ref([])      // [{city, count, districts: [{name, count}]}]
const loadFailed = ref(false)
const selected = ref(null)   // 已选 {city, district, label}
const rootEl = ref(null)

/** 初始城市回显：父组件传了城市就直接显示成已选中，否则会出现
 *  "上面输入框写着成都、下面的目的地却是空的"这种自相矛盾的状态 */
function applyInitial(v) {
  if (v) {
    selected.value = { city: v, district: null, label: v }
    query.value = v
  } else if (selected.value) {
    selected.value = null
    query.value = ''
  }
}
applyInitial(props.initial)
// 首页城市卡点选时父组件才拿到城市，所以要跟着 prop 变
watch(() => props.initial, (v) => {
  if (v === (selected.value?.city || '')) return  // 同值不覆盖，避免把已选的"城市·区县"打回城市级
  applyInitial(v)
})

onMounted(async () => {
  document.addEventListener('click', onDocClick)
  try {
    const r = await api.get('/api/spots/regions')
    regions.value = r.cities || []
  } catch {
    loadFailed.value = true  // 联想降级：仍可手打目的地
  }
})
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

/** 点组件外部 → 收起下拉 */
function onDocClick(e) {
  if (rootEl.value && !rootEl.value.contains(e.target)) open.value = false
}

/** 候选列表：空输入时列城市级；有关键词时「城市名 / 区名」包含即命中 */
const candidates = computed(() => {
  const q = query.value.trim()
  const out = []
  for (const c of regions.value) {
    if (!q) {
      out.push({ city: c.city, district: null, label: c.city, sub: `全市 · ${c.count} 个景点` })
      continue
    }
    if (c.city.includes(q)) {
      out.push({ city: c.city, district: null, label: c.city, sub: `全市 · ${c.count} 个景点` })
    }
    for (const d of c.districts || []) {
      if (d.name.includes(q)) {
        out.push({ city: c.city, district: d.name, label: `${c.city} · ${d.name}`, sub: `${d.count} 个景点` })
      }
    }
  }
  return out.slice(0, 30)  // 下拉最多 30 条，避免撑爆
})

function onInput() {
  selected.value = null     // 重新输入 = 放弃已选，回到联想态
  open.value = true
  activeIdx.value = -1
}

function onKeydown(e) {
  const list = candidates.value
  if (e.key === 'ArrowDown' && list.length) {
    e.preventDefault()
    activeIdx.value = (activeIdx.value + 1) % list.length
    open.value = true
  } else if (e.key === 'ArrowUp' && list.length) {
    e.preventDefault()
    activeIdx.value = (activeIdx.value - 1 + list.length) % list.length
  } else if (e.key === 'Enter') {
    if (open.value && activeIdx.value >= 0 && list[activeIdx.value]) {
      e.preventDefault()
      pick(list[activeIdx.value])
    }
  } else if (e.key === 'Escape') {
    open.value = false
  }
}

function pick(item) {
  selected.value = item
  query.value = item.label
  open.value = false
  activeIdx.value = -1
  emit('select', { city: item.city, district: item.district })
}

function clearSel() {
  selected.value = null
  query.value = ''
  open.value = false
  emit('select', { city: '', district: null })
}
</script>

<template>
  <div class="rp" ref="rootEl">
    <span class="rp-pin">📍</span>
    <input v-model="query" class="rp-input" :placeholder="placeholder"
           @input="onInput" @focus="open = true" @keydown="onKeydown" />
    <button v-if="selected" class="rp-x" title="清除目的地" @click="clearSel">✕</button>

    <!-- 联想下拉：方向由 placement 决定，聊天页向上、首页首屏向下 -->
    <div v-if="open" class="rp-drop" :class="placement">
      <div v-if="loadFailed" class="rp-empty">地区数据加载失败，可直接在下方输入框描述目的地</div>
      <div v-else-if="!candidates.length" class="rp-empty">
        {{ query.trim() ? `没有匹配「${query.trim()}」的市 / 区，换个词试试` : '暂无候选城市' }}
      </div>
      <div v-for="(c, i) in candidates" :key="c.label" class="rp-item"
           :class="{ on: i === activeIdx }" @mousedown.prevent="pick(c)" @mouseenter="activeIdx = i">
        <span class="rp-label">{{ c.label }}</span>
        <span class="rp-sub">{{ c.sub }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rp { position: relative; flex: 1; min-width: 130px; display: flex; align-items: center; gap: 7px;
      background: #fff; border: 1.5px solid #ccc; border-radius: 10px; padding: 0 10px;
      height: var(--rp-h, 38px); }
.rp:focus-within { border-color: var(--green); }
.rp-pin { font-size: 13px; flex-shrink: 0; }
.rp-input { flex: 1; min-width: 0; border: none; outline: none; background: transparent; font-size: var(--rp-fs, 13px); }
.rp-input::placeholder { color: #aaa; }
.rp-x { border: none; background: #eee; color: #888; width: 18px; height: 18px; border-radius: 50%;
        font-size: 11px; line-height: 1; cursor: pointer; flex-shrink: 0; padding: 0; }
.rp-x:hover { background: #fde9e9; color: #c0392b; }
.rp-drop { position: absolute; bottom: calc(100% + 6px); left: 0; right: 0; background: #fff;
           border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 -4px 16px rgba(0,0,0,.1);
           max-height: 248px; overflow-y: auto; z-index: 40; }
.rp-drop.down { bottom: auto; top: calc(100% + 6px); box-shadow: 0 6px 20px rgba(0,0,0,.12); }
.rp-empty { padding: 14px 12px; font-size: 12px; color: var(--text-sub); text-align: center; line-height: 1.7; }
.rp-item { display: flex; justify-content: space-between; align-items: center; gap: 8px;
           padding: 8px 12px; cursor: pointer; }
.rp-item + .rp-item { border-top: 1px dashed #f0efe9; }
.rp-item.on, .rp-item:hover { background: var(--green-soft); }
.rp-label { font-size: 13px; }
.rp-sub { font-size: 11px; color: var(--text-sub); white-space: nowrap; }
</style>
