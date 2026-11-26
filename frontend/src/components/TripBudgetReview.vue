<script setup>
import { computed, ref, watch } from 'vue'
import { budgetCategories, formatCny as money, parseCny, summarizeBudgetReview } from '../utils/tripBudgetReview'

const props = defineProps({
  trip: { type: Object, required: true },
  disabled: Boolean,
  saveChecklist: { type: Function, required: true },
})
const emit = defineEmits(['dirty'])
const summary = computed(() => summarizeBudgetReview(props.trip))
const reference = computed(() => props.trip.budget_review?.reference || {})
const form = ref(null), error = ref(''), editVersion = ref(null), saving = ref(false)
const amountInput = minor => minor == null ? '' : (minor / 100).toFixed(2)
watch(form, value => emit('dirty', value !== null), { immediate: true })

function start() {
  const { limit, entries } = summary.value
  editVersion.value = props.trip.version
  form.value = {
    limit: amountInput(limit),
    entries: Object.fromEntries(budgetCategories.map(([key]) => {
      const row = entries[key] || {}
      return [key, { status: row.status || 'pending', amount: amountInput(row.amount_minor),
                     source: row.source || '', checked_on: row.checked_on || '' }]
    })),
  }
  error.value = ''
}
function cancel() {
  form.value = null
  error.value = ''
}
async function save() {
  if (!form.value || props.disabled || saving.value) return
  error.value = ''
  try {
    const entries = {}
    const limit_minor = parseCny(form.value.limit)
    for (const [key, label] of budgetCategories) {
      const row = form.value.entries[key]
      if (row.status === 'pending') continue
      const source = row.source.trim()
      if (!source) throw new Error(`${label}需要填写${row.status === 'quoted' ? '来源和覆盖范围' : '不计入的原因'}。`)
      if (row.status === 'excluded') entries[key] = { status: 'excluded', source }
      else {
        const amount_minor = parseCny(row.amount)
        if (amount_minor === null || !row.checked_on) throw new Error(`${label}需要填写报价金额和核对日期。`)
        entries[key] = { status: 'quoted', amount_minor, source, checked_on: row.checked_on }
      }
    }
    saving.value = true
    if (await props.saveChecklist({ limit_minor, entries }, editVersion.value)) cancel()
    else error.value = '本次保存未确认。输入已保留，请核对当前版本后再保存。'
  } catch (err) { error.value = err.message }
  finally { saving.value = false }
}
</script>

<template>
  <section class="review" aria-label="预算核对">
    <div class="heading"><div><p class="eyebrow">TRIP BUDGET</p><h2>预算核对</h2></div>
      <button v-if="!form" type="button" :disabled="disabled" @click="start">编辑费用清单</button>
    </div>
    <div v-if="Object.keys(reference).length" class="reference">
      <b>原规划参考</b>
      <p v-if="reference.limit_minor != null">原预算上限 {{ money(reference.limit_minor) }}</p>
      <p v-if="reference.subtotal_minor != null">已列估算 {{ money(reference.subtotal_minor) }}<span v-if="reference.unpriced_items"> · {{ reference.unpriced_items }} 项待确认</span></p>
      <p v-if="reference.unlisted_categories?.length">原规划未列：{{ reference.unlisted_categories.join('、') }}</p>
    </div>
    <p class="intro">按全队总价填写已问到的金额，备注来源和所含人数、日期、税费。这里不产生订单或支出记录。</p>
    <dl class="totals">
      <div><dt>当前预算上限</dt><dd>{{ summary.limit == null ? '未设' : money(summary.limit) }}</dd></div>
      <div><dt>已录报价小计</dt><dd>{{ summary.quoted ? money(summary.subtotal) : '暂无报价' }}</dd></div>
      <div><dt>费用类别待核对</dt><dd>{{ summary.remaining }} 项</dd></div>
    </dl>
    <p v-if="summary.state === 'over'" class="alert" role="status">已录金额已超过上限，未核对的费用还未包含。</p>
    <p v-else-if="summary.state === 'within'" class="intro" role="status">按已填金额和纳入范围，当前未超过上限。请在下单前再核对实际金额。</p>
    <p v-else-if="summary.state === 'stale'" class="alert" role="status">旅行日期、天数或目的地已变化，请重新核对已有报价与范围。</p>
    <p v-else-if="summary.state === 'no_quotes'" class="intro">当前各类均不计入，暂无可核对报价。</p>
    <p v-else-if="summary.state === 'no_limit'" class="intro">还没有预算上限，无法对照。</p>
    <p v-else class="intro">还有费用待核对，暂不能判断总预算是否达标。</p>
    <ul v-if="!form" class="rows"><li v-for="[key,label] in budgetCategories" :key="key">
      <div><b>{{ label }}</b><small v-if="summary.entries[key]">{{ summary.entries[key].checked_on ? summary.entries[key].checked_on + ' · ' : '' }}{{ summary.entries[key].source }}</small></div>
      <span>{{ summary.entries[key]?.status === 'excluded' ? '不计入' : money(summary.entries[key]?.amount_minor) }}</span>
    </li></ul>
    <form v-else class="budget-form" @submit.prevent="save">
      <fieldset :disabled="disabled || saving">
        <label>预算上限（元）<input v-model.trim="form.limit" inputmode="decimal" maxlength="14" placeholder="不设置可留空"></label>
        <div v-for="[key,label] in budgetCategories" :key="key" class="cost-form">
          <label>{{ label }}<select v-model="form.entries[key].status"><option value="pending">待核对</option><option value="quoted">已录报价</option><option value="excluded">不计入预算</option></select></label>
          <template v-if="form.entries[key].status === 'quoted'">
            <label>全队总金额（元）<input v-model.trim="form.entries[key].amount" inputmode="decimal" maxlength="14" required></label>
            <label>核对日期<input v-model="form.entries[key].checked_on" type="date" required></label>
          </template>
          <label v-if="form.entries[key].status !== 'pending'" class="source">{{ form.entries[key].status === 'quoted' ? '来源与覆盖范围' : '不计入的原因' }}<input v-model.trim="form.entries[key].source" maxlength="200" required></label>
        </div>
      </fieldset>
      <p v-if="error" class="alert" role="alert">{{ error }}</p>
      <div class="actions"><button :disabled="disabled || saving">保存费用清单</button><button type="button" :disabled="saving" @click="cancel">取消编辑</button></div>
    </form>
  </section>
</template>

<style scoped>
.review{border-top:1px solid #e8eee9;padding:24px;color:#294a3b}
.heading,.rows li,.actions{display:flex;justify-content:space-between;gap:10px;align-items:center}
.eyebrow{font-size:10px;letter-spacing:.14em;color:#789487;font-weight:700}.review h2{font-size:19px;margin:6px 0}
.review button{border:0;border-radius:7px;background:#e7f1e9;color:#176447;padding:9px 12px;font-weight:700}.review button:disabled{opacity:.5}
.reference,.totals,.budget-form{background:#f5f8f4;border-radius:10px;padding:12px;margin:12px 0;font-size:13px}
.reference p,.intro,.alert{margin:6px 0;font-size:12px;line-height:1.6}.intro{color:#657c70}.alert{color:#925b13;font-weight:600}
.totals{display:flex;gap:18px;flex-wrap:wrap}.totals dt{font-size:11px}.totals dd{font-weight:700;margin:3px 0 0}
.rows{list-style:none;padding:0;margin:12px 0 0}.rows li{border-bottom:1px solid #edf1ed;padding:10px 0;font-size:13px}.rows li div{min-width:0}.rows small{display:block;color:#687e70;overflow-wrap:anywhere}.rows li span{white-space:nowrap}
.budget-form label{display:grid;gap:4px;font-size:12px;font-weight:600;min-width:0}.budget-form fieldset{border:0;padding:0;margin:0;min-width:0}
.budget-form input,.budget-form select{min-width:0;width:100%;font:inherit;font-weight:400;color:#294a3b;border:1px solid #dbe5de;border-radius:7px;padding:8px;background:white}
.cost-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,128px),1fr));gap:7px;border-top:1px solid #dfe8df;margin-top:10px;padding-top:10px}.source{grid-column:1/-1}
.actions{justify-content:flex-start;margin-top:12px}
@media(max-width:760px){.review{padding:18px 14px}.totals{gap:12px}}
</style>
