<script setup lang="ts">
defineProps<{ cells: { label: string; value: string | number; small?: string; warn?: boolean }[] }>()
</script>

<template>
  <div class="strip">
    <div v-for="c in cells" :key="c.label" class="cell">
      <span>{{ c.label }}</span>
      <b :style="c.warn ? 'color:var(--amber)' : ''">{{ c.value }}</b>
      <small v-if="c.small">{{ c.small }}</small>
    </div>
  </div>
</template>
