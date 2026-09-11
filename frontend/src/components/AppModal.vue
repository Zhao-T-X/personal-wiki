<script setup lang="ts">
defineProps<{ open: boolean; title: string; subtitle?: string }>()
defineEmits<{ (e: 'close'): void }>()
</script>

<template>
  <div v-if="open" class="modal" @click.self="$emit('close')">
    <div class="modalbox">
      <div class="modalhead">
        <div>
          <h2 style="font-size:17px;margin:0">{{ title }}</h2>
          <p v-if="subtitle" class="muted" style="font-size:9px;margin:4px 0 0">{{ subtitle }}</p>
        </div>
        <button class="close" @click="$emit('close')">×</button>
      </div>
      <div style="margin-top:14px"><slot /></div>
    </div>
  </div>
</template>
