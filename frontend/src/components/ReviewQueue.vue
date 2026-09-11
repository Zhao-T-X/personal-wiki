<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, patchJson } from '../api/client'
import KpiStrip from './KpiStrip.vue'
import EmptyState from './EmptyState.vue'
import { useAppStore } from '../stores/app'

const router = useRouter()
const store = useAppStore()

type Kind = 'entities' | 'claims' | 'relations'
const kind = ref<Kind>('entities')
const data = ref<{ entities: any[]; claims: any[]; relations: any[] }>({ entities: [], claims: [], relations: [] })
const filter = ref('')
const busy = ref(false)

async function load() {
  data.value = await api('/api/review?limit=200')
}
onMounted(load)

const KIND_META: Record<Kind, { label: string; idKey: string }> = {
  entities: { label: '实体', idKey: 'entity' },
  claims: { label: 'Claims', idKey: 'claim' },
  relations: { label: '关系', idKey: 'relation' },
}

const rows = computed(() => {
  const list = data.value[kind.value] || []
  const q = filter.value.trim().toLowerCase()
  return q ? list.filter((r: any) => JSON.stringify(r).toLowerCase().includes(q)) : list
})

const label = (r: any) => {
  if (kind.value === 'entities') return r.name
  if (kind.value === 'claims') return `${r.subject_name || ''} ${r.predicate} ${r.object_name || r.object_text || ''}`
  return `${r.source_name || ''} ${r.predicate} ${r.target_name || ''}`
}
const sub = (r: any) => {
  if (kind.value === 'entities') return r.description || r.type
  if (kind.value === 'claims') return r.content || (r.source_quote || '').slice(0, 80)
  return r.source_name ? `${r.source_name} → ${r.target_name}` : ''
}

async function decide(id: string, status: 'verified' | 'rejected') {
  busy.value = true
  try {
    await patchJson(`/api/knowledge/${KIND_META[kind.value].idKey}/${id}/status`, { status })
    const list = data.value[kind.value] as any[]
    data.value = { ...data.value, [kind.value]: list.filter(r => r.id !== id) }
  } catch (e: any) { store.toast(e.message) } finally { busy.value = false }
}

async function decideAll(status: 'verified' | 'rejected') {
  const list = [...(data.value[kind.value] as any[])]
  if (!list.length) return
  if (!confirm(`将当前 ${list.length} 条候选全部标记为 ${status}？`)) return
  busy.value = true
  let ok = 0
  for (const r of list) {
    try { await patchJson(`/api/knowledge/${KIND_META[kind.value].idKey}/${r.id}/status`, { status }); ok++ } catch { /* keep going */ }
  }
  busy.value = false
  store.toast(`已处理 ${ok}/${list.length} 条`)
  await load()
}
</script>

<template>
  <div>
    <KpiStrip :cells="[
      { label: '待审实体', value: data.entities.length },
      { label: '待审 Claims', value: data.claims.length },
      { label: '待审关系', value: data.relations.length },
      { label: '合计', value: data.entities.length + data.claims.length + data.relations.length },
    ]" />

    <div class="row" style="margin:16px 0 10px;flex-wrap:wrap;gap:8px">
      <div class="seg">
        <button v-for="(meta, k) in KIND_META" :key="k" :class="{ active: kind === k }" @click="kind = k as Kind">
          {{ meta.label }} ({{ (data[k as Kind] || []).length }})
        </button>
      </div>
      <div class="grow"></div>
      <input v-model="filter" class="field wide" placeholder="搜索候选…" />
      <button class="btn" :disabled="busy || !rows.length" @click="decideAll('verified')">全部通过</button>
      <button class="btn danger" :disabled="busy || !rows.length" @click="decideAll('rejected')">全部拒绝</button>
      <button class="btn" :disabled="busy" @click="load">刷新</button>
    </div>

    <div class="panel pad" style="padding:6px">
      <div v-for="r in rows" :key="r.id" class="item" style="cursor:default">
        <div class="ico-badge" :class="kind === 'entities' ? 'ib-blue' : kind === 'claims' ? 'ib-violet' : 'ib-mint'">✓</div>
        <div class="grow">
          <b>{{ label(r) }}</b>
          <p>{{ sub(r) }}</p>
          <div style="margin-top:5px">
            <span v-if="r.confidence != null" class="tag">confidence {{ r.confidence }}</span>
            <span v-if="r.type" class="tag blue">{{ r.type }}</span>
            <span v-if="r.modality" class="tag">{{ r.modality }} · {{ r.polarity }}</span>
            <span v-if="r.source_document_id" class="tag mono">{{ r.source_document_id.slice(0, 8) }}</span>
          </div>
        </div>
        <div class="row" style="gap:6px;flex:none">
          <button class="btn sm" :disabled="busy" @click="decide(r.id, 'verified')">通过</button>
          <button class="btn sm danger" :disabled="busy" @click="decide(r.id, 'rejected')">拒绝</button>
        </div>
      </div>
      <EmptyState v-if="!rows.length" text="该分类没有待审候选——审核队列已清空" />
    </div>

    <div class="notice violet" style="margin-top:12px">
      审核通过（verified）的知识会作为可信锚点进入图谱与问答；拒绝（rejected）会被检索与图谱排除。
      <span v-if="kind === 'entities'" style="cursor:pointer;text-decoration:underline" @click="router.push('/knowledge')">在知识库中查看实体详情 →</span>
    </div>
  </div>
</template>
