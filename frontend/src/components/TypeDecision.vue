<script setup lang="ts">
defineProps<{ decisions: { type: string; reason: string; ok: boolean }[] }>()
</script>

<template>
  <div style="padding:6px">
    <div v-for="(d, i) in decisions" :key="i" class="item" style="cursor:default">
      <span class="tag" :class="d.ok ? 'green' : 'red'" style="flex:none">{{ d.ok ? '✓' : '✕' }}</span>
      <div><b>{{ d.type }}</b><p>{{ d.reason }}</p></div>
    </div>
    <div v-if="!decisions.length" class="empty">暂无类型判定记录——该实体由抽取产生，未包含显式的 defined_as 声明</div>
  </div>
</template>
