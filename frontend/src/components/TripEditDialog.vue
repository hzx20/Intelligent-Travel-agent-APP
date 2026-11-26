<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
const props = defineProps({ label: {type:String, required:true}, busy:Boolean })
const emit = defineEmits(['close'])
const element = ref(null)
function requestClose() { if (!props.busy) emit('close') }
function keepTabFocus(event) {
  if (event.key !== 'Tab') return
  const dialog = element.value
  const fields = [...dialog.querySelectorAll('button,input:not([type="hidden"]),select,textarea,a[href],[tabindex]:not([tabindex="-1"])')]
    .filter(field => !field.matches(':disabled') && !field.closest('[inert]') && field.getClientRects().length && getComputedStyle(field).visibility !== 'hidden')
  const first = fields[0], last = fields.at(-1), focused = document.activeElement
  if (!first) { event.preventDefault(); dialog.focus(); return }
  if (event.shiftKey && (focused === first || focused === dialog || !dialog.contains(focused))) {
    event.preventDefault(); last.focus()
  } else if (!event.shiftKey && (focused === last || focused === dialog || !dialog.contains(focused))) {
    event.preventDefault(); first.focus()
  }
}
onMounted(() => element.value.showModal())
onBeforeUnmount(() => { if (element.value?.open) element.value.close() })
</script>

<template>
  <dialog ref="element" class="trip-edit-dialog" tabindex="-1" :aria-label="label" :aria-busy="busy" @keydown="keepTabFocus" @cancel.prevent="requestClose" @click.self="requestClose"><slot /></dialog>
</template>

<style scoped>
.trip-edit-dialog{margin:auto;width:min(500px,calc(100% - 32px));max-width:none;max-height:90vh;border:0;padding:0;border-radius:15px;color:inherit;background:white;overflow:auto}
.trip-edit-dialog::backdrop{background:#13271db3}
</style>
