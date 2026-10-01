<script setup lang="ts">
import { useI18n } from 'vue-i18n'

defineProps<{ decisions: { type: string; reason: string; ok: boolean }[] }>()

const { t } = useI18n()
</script>

<template>
  <div style="padding:6px">
    <div v-for="(d, i) in decisions" :key="i" class="item" style="cursor:default">
      <span class="tag" :class="d.ok ? 'green' : 'red'" style="flex:none">{{ d.ok ? '✓' : '✕' }}</span>
      <div><b>{{ d.type }}</b><p>{{ d.reason }}</p></div>
    </div>
    <div v-if="!decisions.length" class="empty">{{ t('typeDecision.empty') }}</div>
  </div>
</template>
