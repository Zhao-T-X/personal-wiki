<script setup lang="ts">
defineProps<{ subject: string; predicate: string; object: string }>()
</script>

<template>
  <div class="triple">
    <div class="tnode">{{ subject }}</div>
    <div class="tpred"><em>{{ predicate }}</em><div class="line"></div><span class="faint" style="font-size:8px">predicate</span></div>
    <div class="tnode obj">{{ object }}</div>
  </div>
</template>
