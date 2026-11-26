<script setup>
import { onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import { useAuth } from '../store/auth'
const props = defineProps({ tripId: {type: Number, required: true}, footprintId: {type: Number, required: true}, photoId: {type: Number, required: true} })
const { user } = useAuth()
const src = ref(''), failed = ref(false), attempt = ref(0)
let sequence = 0
function clear() { if (src.value) URL.revokeObjectURL(src.value); src.value = '' }
function decodeError(event) {
  if (event.currentTarget.src !== src.value) return
  clear(); failed.value = true
}
watch(() => [props.tripId, props.footprintId, props.photoId, user.value?.id, attempt.value], async ([trip, footprint, photo, uid]) => {
  const run = ++sequence
  clear(); failed.value = false
  if (!uid) return
  try {
    const blob = await api.photo(`/api/trips/${trip}/footprints/${footprint}/photos/${photo}`)
    if (run === sequence) src.value = URL.createObjectURL(blob)
  } catch { if (run === sequence) failed.value = true }
}, { immediate: true })
onBeforeUnmount(() => { sequence++; clear() })
</script>
<template><img v-if="src" :src="src" alt="足迹照片" loading="lazy" @error="decodeError"><button v-else-if="failed" type="button" class="photo-state" @click="attempt++">照片无法加载<br>点击重试</button><span v-else class="photo-state">照片加载中…</span></template>
<style scoped>img,.photo-state{display:block;width:96px;height:96px;border:0;border-radius:8px;object-fit:cover;background:#eef3ee}.photo-state{display:grid;place-items:center;color:#788c80;font-size:11px}button.photo-state{cursor:pointer}</style>
