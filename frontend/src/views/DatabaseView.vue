<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import PageHead from '../components/PageHead.vue'
import KpiCard from '../components/KpiCard.vue'
import DataTable from '../components/DataTable.vue'
import { useAppStore } from '../stores/app'

const { t } = useI18n()
const store = useAppStore()
const router = useRouter()
const stats = ref<Record<string, number>>({})
const integrity = ref<{ integrity: string; size_mb: number; page_count: number } | null>(null)
const checking = ref(false)

async function load() {
  stats.value = await api('/api/stats')
  integrity.value = await api('/api/database/integrity')
}
onMounted(load)

async function check() {
  checking.value = true
  try {
    const r = await api<{ integrity: string; size_mb: number; page_count: number }>('/api/database/integrity')
    integrity.value = r
    store.toast(t('database.integrityCheck') + '：' + r.integrity)
  } finally { checking.value = false }
}

const rows = computed(() => Object.entries(stats.value).map(([table, count]) => ({
  table, count, status: 'OK',
})))
const totalRows = computed(() => Object.values(stats.value).reduce((a, b) => a + b, 0))
const columns = [
  { key: 'table', label: 'Table' }, { key: 'count', label: 'Rows' }, { key: 'status', label: 'Status' },
]
</script>

<template>
  <div class="page">
    <div class="eyebrow" style="cursor:pointer;margin-bottom:8px" @click="router.push('/settings')">← {{ t('database.back') }}</div>
    <PageHead title="Database" :subtitle="t('database.subtitle')">
      <template #actions>
        <button class="btn" :disabled="checking" @click="check">{{ t('database.integrityCheck') }}</button>
      </template>
    </PageHead>
    <div class="grid g4">
      <KpiCard label="Database Size" :value="integrity ? integrity.size_mb + ' MB' : '—'" />
      <KpiCard label="Tables" :value="Object.keys(stats).length" />
      <KpiCard label="Total Rows" :value="totalRows" />
      <KpiCard label="Integrity" :value="integrity?.integrity === 'ok' ? '100%' : (integrity?.integrity || '—')"
               :trend="integrity?.integrity === 'ok' ? t('database.checkPassed') : ''" :warn="integrity?.integrity !== 'ok'" />
    </div>
    <div class="card pad" style="margin-top:14px">
      <div class="cardhead"><h3>Schema Overview</h3><span class="muted" style="font-size:8px">page_count {{ integrity?.page_count ?? '—' }}</span></div>
      <DataTable :columns="columns" :rows="rows">
        <template #cell-table="{ row }"><span class="mono">{{ row.table }}</span></template>
        <template #cell-status="{ row }"><span class="tag green">{{ row.status }}</span></template>
      </DataTable>
    </div>
  </div>
</template>
