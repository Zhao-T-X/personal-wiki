<script setup lang="ts">
defineProps<{ open: boolean; title: string }>()
defineEmits<{ (e: 'close'): void }>()
</script>

<template>
  <div class="drawer" :class="{ show: open }">
    <div class="drawerhead">
      <b>{{ title }}</b>
      <button class="close" @click="$emit('close')">×</button>
    </div>
    <div style="margin-top:14px"><slot /></div>
  </div>
</template>
