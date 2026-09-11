<script setup lang="ts">
defineProps<{ options: string[]; modelValue: string }>()
defineEmits<{ (e: 'update:modelValue', v: string): void }>()
</script>

<template>
  <div class="seg">
    <button v-for="o in options" :key="o" :class="{ active: o === modelValue }" @click="$emit('update:modelValue', o)">{{ o }}</button>
  </div>
</template>
