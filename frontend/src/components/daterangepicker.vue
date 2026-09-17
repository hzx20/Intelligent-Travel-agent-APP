<script setup>
/** 「你想去多久」日期范围选择模块。
 *  点击模块 → 向上弹出类日历面板；先点开始日期、再点结束日期（点早于开始则重开一段），
 *  悬停可预览区间。选完面板内用「开始日期 / 结束日期」两枚标签展示起止，
 *  并向父组件抛 {start, end, days}（ISO 日期串 + 天数）。 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const emit = defineEmits(['select', 'clear'])

const open = ref(false)
const start = ref(null)      // Date | null
const end = ref(null)        // Date | null
const hover = ref(null)      // 选了开始还没选结束时，鼠标悬停预览
const rootEl = ref(null)

const now = new Date()
now.setHours(0, 0, 0, 0)
const today = new Date(now)                    // 今天 0 点（含今天可选）
const view = ref({ y: now.getFullYear(), m: now.getMonth() })  // 当前展示的年月
const MAX_MONTHS_AHEAD = 12                    // 最多往后翻 12 个月

/* ---- 工具 ---- */
const pad = (n) => String(n).padStart(2, '0')
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const cn = (d) => `${d.getMonth() + 1}月${d.getDate()}日`
const sameDay = (a, b) => a && b && iso(a) === iso(b)
const dayDiff = (a, b) => Math.round((b - a) / 86400000)

/** 当前面板标题年月能否往前 / 往后翻 */
const canPrev = computed(() => view.value.y > today.getFullYear() || view.value.m > today.getMonth())
const canNext = computed(() => {
  const limit = new Date(today.getFullYear(), today.getMonth() + MAX_MONTHS_AHEAD, 1)
  return new Date(view.value.y, view.value.m + 1, 1) <= limit
})

function shiftMonth(n) {
  const d = new Date(view.value.y, view.value.m + n, 1)
  view.value = { y: d.getFullYear(), m: d.getMonth() }
}

/** 日历格子：周一开头，前置补空位 */
const grid = computed(() => {
  const { y, m } = view.value
  const first = new Date(y, m, 1)
  const lead = (first.getDay() + 6) % 7                 // 周一=0
  const days = new Date(y, m + 1, 0).getDate()
  const cells = Array.from({ length: lead }, () => ({ date: null }))
  for (let d = 1; d <= days; d++) cells.push({ date: new Date(y, m, d) })
  return cells
})

/** 区间预览/已选的起止（用于高亮） */
const rangeEdge = computed(() => {
  if (start.value && end.value) return { from: start.value, to: end.value }
  if (start.value && hover.value && hover.value >= start.value) return { from: start.value, to: hover.value }
  return null
})

/** 模块上展示的摘要 */
const summary = computed(() => {
  if (!start.value || !end.value) return null
  const n = dayDiff(start.value, end.value)
  return { start: cn(start.value), end: cn(end.value), days: n + 1, nights: n }
})

/* ---- 交互 ---- */
function toggle() {
  open.value = !open.value
  if (!open.value) hover.value = null
}

function onDocClick(e) {
  if (rootEl.value && !rootEl.value.contains(e.target)) { open.value = false; hover.value = null }
}
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))

function cellClass(c) {
  if (!c.date) return 'pad'
  const past = c.date < today
  const edge = rangeEdge.value
  const inRange = edge && c.date > edge.from && c.date < edge.to
  return {
    day: true,
    past,
    today: sameDay(c.date, today),
    edge: sameDay(c.date, start.value) || sameDay(c.date, end.value),
    mid: !!inRange,
  }
}

function pick(c) {
  if (!c.date || c.date < today) return
  hover.value = null
  if (!start.value || (start.value && end.value)) {
    // 没开始 / 已选完一段 → 重开新一段
    start.value = c.date
    end.value = null
    return
  }
  if (c.date >= start.value) {
    end.value = c.date                      // 补上结束日期 → 完成选择
    emit('select', { start: iso(start.value), end: iso(end.value), days: dayDiff(start.value, end.value) + 1 })
  } else {
    start.value = c.date                    // 点在开始之前 → 以此为新开始
  }
}

function reset() {
  start.value = null
  end.value = null
  hover.value = null
  emit('clear')
}

function done() {
  open.value = false
  hover.value = null
}
</script>

<template>
  <div class="drp" ref="rootEl">
    <!-- 模块本体：点击弹日历 -->
    <button class="drp-btn" :class="{ filled: !!summary }" @click="toggle">
      <span class="drp-ico">📅</span>
      <template v-if="summary">
        <span class="drp-txt">
          <b>你想去多久</b>
          <small>{{ summary.start }} ~ {{ summary.end }} · {{ summary.days }}天{{ summary.nights }}晚</small>
        </span>
      </template>
      <template v-else>
        <span class="drp-txt">
          <b>你想去多久</b>
          <small>点击选择出行日期</small>
        </span>
      </template>
      <span v-if="summary" class="drp-x" title="清除日期" @click.stop="reset">✕</span>
    </button>

    <!-- 向上弹出的日历面板 -->
    <div v-if="open" class="drp-panel">
      <!-- 起止提示行：清晰标注开始/结束日期 -->
      <div class="drp-hint">
        <span class="drp-chip" :class="{ set: start }">
          <em>开始日期</em>
          <b>{{ start ? cn(start) : '未选' }}</b>
        </span>
        <span class="drp-arrow">→</span>
        <span class="drp-chip" :class="{ set: end }">
          <em>结束日期</em>
          <b>{{ end ? cn(end) : '未选' }}</b>
        </span>
      </div>

      <!-- 月份导航 -->
      <div class="drp-nav">
        <button class="drp-mv" :disabled="!canPrev" @click="shiftMonth(-1)">‹</button>
        <b>{{ view.y }}年{{ view.m + 1 }}月</b>
        <button class="drp-mv" :disabled="!canNext" @click="shiftMonth(1)">›</button>
      </div>

      <!-- 星期表头 -->
      <div class="drp-week">
        <span v-for="w in ['一','二','三','四','五','六','日']" :key="w">{{ w }}</span>
      </div>

      <!-- 日期格 -->
      <div class="drp-grid">
        <span v-for="(c, i) in grid" :key="i" :class="cellClass(c)"
              @click="pick(c)" @mouseenter="c.date && !end && (hover = c.date)">
          <template v-if="c.date">{{ c.date.getDate() }}</template>
        </span>
      </div>

      <p class="drp-tip">先点开始，再点结束；悬停可预览区间</p>

      <!-- 底部操作 -->
      <div class="drp-acts">
        <button class="drp-sub" @click="reset">重置</button>
        <button class="drp-ok" :disabled="!summary" @click="done">完成</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.drp { position: relative; flex-shrink: 0; }
.drp-btn { display: flex; align-items: center; gap: 8px; background: #fff; border: 1.5px solid #ccc;
           border-radius: 10px; padding: 0 10px; height: 38px; cursor: pointer; min-width: 0; max-width: 250px; }
.drp-btn:hover { border-color: var(--green); }
.drp-btn.filled { border-color: var(--green); background: var(--green-soft); }
.drp-ico { font-size: 13px; }
.drp-txt { display: flex; flex-direction: column; align-items: flex-start; line-height: 1.3; min-width: 0; }
.drp-txt b { font-size: 12px; color: var(--text); }
.drp-txt small { font-size: 11px; color: var(--text-sub); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 170px; }
.drp-x { border: none; background: #e0e0e0; color: #888; width: 17px; height: 17px; border-radius: 50%;
         font-size: 10px; line-height: 1; cursor: pointer; padding: 0; flex-shrink: 0; }
.drp-x:hover { background: #fde9e9; color: #c0392b; }

.drp-panel { position: absolute; bottom: calc(100% + 6px); left: 0; width: 272px; background: #fff;
             border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 -4px 16px rgba(0,0,0,.1);
             padding: 12px; z-index: 41; }
.drp-hint { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
.drp-chip { flex: 1; display: flex; flex-direction: column; gap: 1px; border: 1px dashed #d5d5cd;
            border-radius: 8px; padding: 4px 8px; background: #fafaf7; }
.drp-chip em { font-style: normal; font-size: 10px; color: var(--text-sub); }
.drp-chip b { font-size: 12.5px; color: #bbb; }
.drp-chip.set { border-style: solid; border-color: var(--green-border, #cfe4da); background: var(--green-soft); }
.drp-chip.set b { color: var(--green); }
.drp-arrow { color: var(--text-sub); font-size: 13px; }
.drp-nav { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; }
.drp-nav b { font-size: 13px; }
.drp-mv { border: 1px solid var(--line); background: #fff; border-radius: 6px; width: 26px; height: 24px;
          cursor: pointer; font-size: 14px; line-height: 1; color: #555; }
.drp-mv:disabled { opacity: .3; cursor: default; }
.drp-week { display: grid; grid-template-columns: repeat(7, 1fr); margin-bottom: 2px; }
.drp-week span { text-align: center; font-size: 11px; color: var(--text-sub); padding: 3px 0; }
.drp-grid { display: grid; grid-template-columns: repeat(7, 1fr); row-gap: 2px; }
.drp-grid .day { position: relative; text-align: center; font-size: 12.5px; padding: 6px 0;
                 border-radius: 8px; cursor: pointer; }
.drp-grid .day:hover { background: #f2f5f2; }
.drp-grid .day.past { color: #ccc; cursor: default; background: none; }
.drp-grid .day.today::after { content: ''; position: absolute; left: 50%; transform: translateX(-50%);
                              bottom: 2px; width: 4px; height: 4px; border-radius: 50%; background: #b7950b; }
.drp-grid .day.mid { background: var(--green-soft); border-radius: 0; }
.drp-grid .day.edge { background: var(--green); color: #fff; border-radius: 8px; font-weight: 700; }
.drp-grid .pad { }
.drp-tip { font-size: 10.5px; color: var(--text-sub); margin: 7px 0 8px; text-align: center; }
.drp-acts { display: flex; gap: 8px; }
.drp-sub { flex: 1; border: 1px solid var(--line); background: #fff; border-radius: 8px; padding: 6px 0;
           font-size: 12px; cursor: pointer; color: #666; }
.drp-sub:hover { border-color: #c0392b; color: #c0392b; }
.drp-ok { flex: 1; border: none; background: var(--green); color: #fff; border-radius: 8px; padding: 6px 0;
          font-size: 12px; cursor: pointer; }
.drp-ok:disabled { opacity: .45; cursor: default; }
</style>
