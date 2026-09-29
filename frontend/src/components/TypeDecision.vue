<script setup lang="ts">
defineProps<{ decisions: { type: string; reason: string; ok: boolean }[] }>()
</script>

<template>
  <div style="padding:6px">
    <div v-for="(d, i) in decisions" :key="i" class="item" style="cursor:default">
      <span class="tag" :class="d.ok ? 'green' : 'red'" style="flex:none">{{ d.ok ? '✓' : '✕' }}</span>
      <div><b>{{ d.type }}</b><p>{{ d.reason }}</p></div>
    </div>
    <div v-if="!decisions.length" class="empty">暂无类型判定记录——这个对象来自资料整理，资料里没有明确说明它是什么类型</div>
  </div>
</template>
