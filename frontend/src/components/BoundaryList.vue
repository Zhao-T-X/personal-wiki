<script setup lang="ts">
defineProps<{ items: { level: 'ok' | 'cond' | 'open'; text: string }[] }>()
const MARKS: Record<string, { cls: string; glyph: string }> = {
  ok: { cls: 'b-ok', glyph: '✓' },
  cond: { cls: 'b-cond', glyph: '△' },
  open: { cls: 'b-open', glyph: '?' },
}
</script>

<template>
  <div>
    <div v-for="(it, i) in items" :key="i" class="brow">
      <span class="bmark" :class="MARKS[it.level].cls">{{ MARKS[it.level].glyph }}</span>
      <span>{{ it.text }}</span>
    </div>
  </div>
</template>
