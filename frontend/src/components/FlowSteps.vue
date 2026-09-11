<script setup lang="ts">
defineProps<{ steps: { title: string; sub?: string }[] }>()
</script>

<template>
  <div>
    <div class="flowrow">
      <template v-for="(s, i) in steps" :key="i">
        <span v-if="i > 0" class="farrow">↓</span>
        <div class="fstep"><b>{{ s.title }}</b><div v-if="s.sub" class="faint" style="font-size:8.5px">{{ s.sub }}</div></div>
      </template>
    </div>
  </div>
</template>
