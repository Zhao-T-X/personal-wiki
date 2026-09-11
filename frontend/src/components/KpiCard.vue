<script setup lang="ts">
defineProps<{ label: string; value: string | number; trend?: string; warn?: boolean }>()
</script>

<template>
  <div class="card kpi">
    <div class="klabel">{{ label }}</div>
    <div class="kn">{{ value }}</div>
    <div v-if="trend" class="ktrend" :class="{ warn }">{{ trend }}</div>
  </div>
</template>
