<script setup>
defineProps({ attributions: { type: Array, default: () => [] } })
function safeUrl(value) {
  try { const url = new URL(value); return url.protocol === 'https:' ? url.href : null } catch { return null }
}
</script>

<template>
  <span v-if="attributions.length" class="weather-attribution">
    数据来源：和风天气
    <template v-for="(item, index) in attributions" :key="index">
      <a v-if="safeUrl(item)" :href="safeUrl(item)" target="_blank" rel="noopener noreferrer">来源声明 {{ index + 1 }}</a>
      <span v-else>{{ item }}</span>
    </template>
  </span>
</template>

<style scoped>
.weather-attribution { display: inline-flex; gap: 8px; flex-wrap: wrap; font-size: 12px; }
.weather-attribution a { color: inherit; text-decoration: underline; overflow-wrap: anywhere; }
</style>
