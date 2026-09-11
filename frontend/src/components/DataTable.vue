<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  columns: { key: string; label: string }[]
  rows: any[]
  filter?: string
  clickable?: boolean
}>()
const emit = defineEmits<{ (e: 'row-click', row: any): void }>()

const filtered = computed(() => {
  const q = (props.filter || '').toLowerCase()
  if (!q) return props.rows
  return props.rows.filter(r => JSON.stringify(r).toLowerCase().includes(q))
})
</script>

<template>
  <table class="table">
    <thead>
      <tr><th v-for="c in columns" :key="c.key">{{ c.label }}</th></tr>
    </thead>
    <tbody>
      <tr v-for="(row, i) in filtered" :key="i" :class="{ clickable }" @click="clickable && emit('row-click', row)">
        <td v-for="c in columns" :key="c.key">
          <slot :name="'cell-' + c.key" :row="row">{{ row[c.key] }}</slot>
        </td>
      </tr>
      <tr v-if="!filtered.length"><td :colspan="columns.length"><div class="empty">暂无数据</div></td></tr>
    </tbody>
  </table>
</template>
