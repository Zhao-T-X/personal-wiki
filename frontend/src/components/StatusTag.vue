<script setup lang="ts">
import { computed } from 'vue'
import { statusStyle } from '../utils/status'

/** ``label`` overrides only the *word*, never the tone: a projection that names a
    state ("当前" for a not-superseded claim) still inherits the colour the stored
    lifecycle already earned. Keeping the wording upstream is what stops the UI from
    growing a second status vocabulary. */
const props = defineProps<{ status: string; label?: string }>()
const style = computed(() => {
  const base = statusStyle(props.status)
  return props.label ? { ...base, label: props.label } : base
})
</script>

<template>
  <span class="tag" :class="style.tone">{{ style.label }}</span>
</template>
