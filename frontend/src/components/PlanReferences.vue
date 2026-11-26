<script setup>
/** 行程页底部的「参考站内资料」出处。
 *
 *  只做展示：数据来自行程对象的 references（后端只传 id/标题/出处，不传正文）。
 *  没有检索结果时整块不渲染 —— 空着一条「本次未参考资料」只会让页面显得可疑。
 */
import { computed } from 'vue'

const props = defineProps({
  references: { type: Array, default: () => [] },
})

const refs = computed(() =>
  (props.references || []).filter((r) => r && String(r.title || '').trim())
)
/** 是否包含维基导游正文：授权要求标注来源，景点片段不属于 CC BY-SA，不能一概而论 */
const hasGuide = computed(() => refs.value.some((r) => r.source_type === 'guide'))
</script>

<template>
  <section v-if="refs.length" class="plan-refs" aria-label="参考的站内资料">
    <div class="refs-head">
      本行程参考了站内 {{ refs.length }} 篇资料
    </div>
    <ul class="refs-list">
      <li v-for="(r, i) in refs" :key="i">
        <a v-if="r.source_url" :href="r.source_url" target="_blank" rel="noopener noreferrer">《{{ r.title }}》</a>
        <span v-else>《{{ r.title }}》</span>
      </li>
    </ul>
    <p v-if="hasGuide" class="refs-license">
      资料来自中文维基导游，<a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noopener noreferrer">CC BY-SA 4.0</a>
    </p>
  </section>
</template>

<style scoped>
.plan-refs {
  margin: 16px 0;
  padding: 14px 16px;
  border: 1px solid #dce7df;
  border-radius: 10px;
  background: #fbfcfa;
}
.refs-head {
  font-size: 13px;
  font-weight: 700;
  color: #1a6e50;
}
.refs-list {
  list-style: none;
  margin: 8px 0 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px 10px;
}
.refs-list li {
  font-size: 12.5px;
  line-height: 1.6;
  max-width: 100%;
  overflow-wrap: anywhere;
}
.refs-list a {
  color: var(--green, #1a6e50);
  text-decoration: none;
  border-bottom: 1px dashed currentColor;
}
.refs-list a:hover {
  border-bottom-style: solid;
}
.refs-license {
  margin: 8px 0 0;
  font-size: 11.5px;
  color: #7b8c80;
}
.refs-license a {
  color: inherit;
}
</style>
