<script setup lang="ts">
/** 选项可以是纯字符串（标签即值），或 { value, label }（值稳定、标签随语言变）。 */
type Opt = string | { value: string; label: string }
defineProps<{ options: Opt[]; modelValue: string }>()
defineEmits<{ (e: 'update:modelValue', v: string): void }>()
function val(o: Opt) { return typeof o === 'string' ? o : o.value }
function lab(o: Opt) { return typeof o === 'string' ? o : o.label }
</script>

<template>
  <div class="seg">
    <button v-for="o in options" :key="val(o)" :class="{ active: val(o) === modelValue }" @click="$emit('update:modelValue', val(o))">{{ lab(o) }}</button>
  </div>
</template>
