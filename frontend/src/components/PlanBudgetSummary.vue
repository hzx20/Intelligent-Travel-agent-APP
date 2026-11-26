<script setup>
import { computed } from 'vue'
import { planBudgetView } from '../utils/planBudget'
const props = defineProps({itinerary: {type: Object, required: true}})
const budget = computed(() => planBudgetView(props.itinerary))
</script>

<template>
  <section class="budget-estimate" aria-label="预算估算">
    <div class="budget-heading"><h3>预算估算</h3><strong>{{ budget.totalLabel }} {{ budget.total }}</strong></div>
    <p class="estimate-source">金额为 AI 行程估算，非实时报价；实际费用及可订情况以供应商确认信息为准。</p>
    <dl><div v-for="row in budget.rows" :key="row.name"><dt>{{ row.name }}</dt><dd>{{ row.amount }}</dd></div></dl>
    <details v-if="budget.additions.length" class="additional-costs"><summary>每日安排以外的费用估算</summary><ul><li v-for="(row, index) in budget.additions" :key="index"><span>{{ row.name }} · {{ row.category }}</span><b>{{ row.amount }}</b></li></ul></details>
    <p v-for="(warning, index) in budget.warnings" :key="index" class="budget-warning">{{ warning }}</p>
  </section>
</template>

<style scoped>
.budget-estimate{padding:16px;margin-bottom:16px;border:1px solid #dce7df;border-radius:10px;background:#f7faf6}
.budget-heading{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}.budget-heading h3{margin:0;font-size:15px}.budget-heading strong{color:#1a6e50}
.estimate-source,.budget-warning{font-size:12px;line-height:1.6;margin:8px 0 0;overflow-wrap:anywhere}.estimate-source{color:#63786b}.budget-warning{color:#805715}
dl{display:flex;flex-wrap:wrap;gap:12px 24px;margin:12px 0 0}dl>div{min-width:64px}dt{font-size:11px;color:#718476}dd{margin:4px 0 0;font-size:14px;font-weight:600}
.additional-costs{margin-top:12px;font-size:12px}.additional-costs summary{cursor:pointer;min-height:32px;line-height:32px}.additional-costs ul{list-style:none;padding:0;margin:6px 0}.additional-costs li{display:flex;justify-content:space-between;gap:12px;padding:5px 0}.additional-costs span{overflow-wrap:anywhere}.additional-costs b{white-space:nowrap}
</style>
