<script setup lang="ts">
import { computed } from 'vue'
import { statusStyle } from '../utils/status'

const props = defineProps<{ status: string }>()
const style = computed(() => statusStyle(props.status))
</script>

<template>
  <span class="tag" :class="style.tone">{{ style.label }}</span>
</template>
