<script setup lang="ts">
/**
 * Empty states are onboarding: the one-line `text` form stays valid, but callers
 * should add a `title` (what this space is) and a #action slot (what to do next).
 */
defineProps<{ text: string; title?: string }>()
</script>

<template>
  <div class="empty">
    <h4 v-if="title" class="emptitle">{{ title }}</h4>
    <p class="emptext">{{ text }}</p>
    <div v-if="$slots.action" class="emptyactions"><slot name="action" /></div>
  </div>
</template>

<style scoped>
.emptitle{margin:0 0 7px;font-size:11.5px;color:#4b5b74;letter-spacing:-.01em}
.emptext{margin:0;line-height:1.65}
.emptyactions{margin-top:13px;display:flex;justify-content:center;gap:8px;flex-wrap:wrap}
</style>
