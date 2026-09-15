<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, patchJson } from '../api/client'
import KpiStrip from './KpiStrip.vue'
import EmptyState from './EmptyState.vue'
import { useAppStore } from '../stores/app'
import { statusStyle } from '../utils/status'

const router = useRouter()
const store = useAppStore()

type Kind = 'entities' | 'claims' | 'relations'
type Decision = 'verified' | 'rejected'

const kind = ref<Kind>('entities')
const data = ref<{ entities: any[]; claims: any[]; relations: any[] }>({ entities: [], claims: [], relations: [] })
const filter = ref('')
const busy = ref(false)
const expanded = ref<Set<string>>(new Set())

/** Above this, a batch accept is a safe bulk operation; below it, ask the human. */
const HIGH_CONFIDENCE = 0.9

const KIND_META: Record<Kind, { label: string; idKey: string }> = {
  entities: { label: '实体', idKey: 'entity' },
  claims: { label: 'Claims', idKey: 'claim' },
  relations: { label: '关系', idKey: 'relation' },
}

async function load() {
  /* 不传 limit：窗口由后端默认值决定，Review Inbox 数的是同一个窗口——
     角标和它打开的这一页必须描述同一批东西。 */
  data.value = await api('/api/review')
  expanded.value = new Set()
}
onMounted(load)

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

/** Confidence may arrive as 0–1 or 0–100 depending on the extractor; normalise once. */
function confidenceOf(r: any): number | null {
  if (r.confidence == null) return null
  const n = Number(r.confidence)
  if (!Number.isFinite(n)) return null
  return n > 1 ? n / 100 : n
}
function fmtConfidence(r: any) {
  const n = confidenceOf(r)
  return n == null ? '' : `${Math.round(n * 100)}%`
}

const isHigh = (r: any) => {
  const n = confidenceOf(r)
  return n != null && n >= HIGH_CONFIDENCE
}

const highConfidence = computed(() => rows.value.filter(isHigh))

const kindHint = computed(() => {
  if (kind.value === 'entities') return '实体候选没有置信度，需要逐个判断。'
  return `置信度 ≥ ${Math.round(HIGH_CONFIDENCE * 100)}% 的建议可以批量接受，其余请逐条确认。`
})

/* ---------- entity candidate detail（为什么创建 / 是否重复） ----------
   展开时才按需拉取：200 条候选不该在列表加载时打 200 个请求。 */
interface EntityDetail {
  loading: boolean
  evidence: { quote: string; documentId: string; chunkId: string } | null
  duplicates: { id: string; name: string; type: string; status: string; similarity: number }[]
}
const detailCache = ref<Record<string, EntityDetail>>({})

const detailOf = (id: string) => detailCache.value[id] || null
const duplicatesOf = (id: string) => detailCache.value[id]?.duplicates || []
const evidenceOf = (id: string) => detailCache.value[id]?.evidence || null

async function loadEntityDetail(id: string) {
  if (detailCache.value[id]) return
  detailCache.value = { ...detailCache.value, [id]: { loading: true, evidence: null, duplicates: [] } }
  const [obj, dup] = await Promise.all([
    api<any>('/api/entities/' + id + '/object').catch(() => null),
    api<any>('/api/entities/' + id + '/duplicates').catch(() => null),
  ])
  // An entity has no document of its own; its first claim quote is the closest
  // thing to "the text that produced this".
  const sample = obj?.evidence?.[0] || obj?.claims?.[0] || null
  detailCache.value = {
    ...detailCache.value,
    [id]: {
      loading: false,
      evidence: sample ? {
        quote: sample.source_quote || sample.content || '',
        documentId: sample.source_document_id || '',
        chunkId: sample.source_chunk_id || '',
      } : null,
      duplicates: dup?.duplicates || [],
    },
  }
}

function openEntitySource(ev: { documentId: string; chunkId: string } | null) {
  if (!ev?.documentId) return
  router.push({
    path: '/knowledge',
    query: { doc: ev.documentId, ...(ev.chunkId ? { chunk: ev.chunkId } : {}) },
  })
}

function isOpen(id: string) { return expanded.value.has(id) }
function toggle(id: string) {
  const next = new Set(expanded.value)
  const opening = !next.has(id)
  opening ? next.add(id) : next.delete(id)
  expanded.value = next
  if (opening && kind.value === 'entities') loadEntityDetail(id)
}
function switchKind(k: Kind) { kind.value = k; expanded.value = new Set() }

function openSource(r: any) {
  if (!r.source_document_id) { store.toast('这条候选没有关联的来源文档'); return }
  router.push({
    path: '/knowledge',
    query: { doc: r.source_document_id, ...(r.source_chunk_id ? { chunk: r.source_chunk_id } : {}) },
  })
}

/**
 * Every decision is reversible: the status endpoint accepts 'candidate', so Undo
 * needs no new API — just a toast action that writes the rows back.
 */
async function applyStatus(items: any[], status: Decision) {
  if (!items.length) return
  const k = kind.value
  const meta = KIND_META[k]
  busy.value = true
  const done: any[] = []
  try {
    for (const r of items) {
      try { await patchJson(`/api/knowledge/${meta.idKey}/${r.id}/status`, { status }); done.push(r) }
      catch { /* keep going — report partial success below */ }
    }
  } finally { busy.value = false }

  if (!done.length) { store.toast('操作失败，候选保持不变'); return }

  const ids = new Set(done.map(r => r.id))
  data.value = { ...data.value, [k]: (data.value[k] as any[]).filter(r => !ids.has(r.id)) }
  expanded.value = new Set()

  const verb = status === 'verified' ? '通过' : '拒绝'
  store.toast(`已${verb} ${done.length} 条候选`, { label: '撤销', run: () => undo(done, k, verb) })
}

async function undo(items: any[], k: Kind, verb: string) {
  const meta = KIND_META[k]
  let ok = 0
  for (const r of items) {
    try { await patchJson(`/api/knowledge/${meta.idKey}/${r.id}/status`, { status: 'candidate' }); ok++ }
    catch { /* keep going */ }
  }
  await load()
  store.toast(ok === items.length ? `已撤销${verb}，候选已回到待审` : `已撤销 ${ok}/${items.length} 条`)
}

const decide = (r: any, status: Decision) => applyStatus([r], status)
const acceptHighConfidence = () => applyStatus(highConfidence.value, 'verified')

function rejectAll() {
  const list = [...rows.value]
  if (!list.length) return
  if (!confirm(`将当前 ${list.length} 条候选全部标记为「拒绝」？拒绝后可以撤销。`)) return
  applyStatus(list, 'rejected')
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
        <button v-for="(meta, k) in KIND_META" :key="k" :class="{ active: kind === k }" @click="switchKind(k as Kind)">
          {{ meta.label }} ({{ (data[k as Kind] || []).length }})
        </button>
      </div>
      <div class="grow"></div>
      <input v-model="filter" class="field wide" placeholder="搜索候选…" />
      <button class="btn" :disabled="busy" @click="load">刷新</button>
    </div>

    <div v-if="rows.length" class="panel pad bulbar">
      <span class="muted" style="font-size:9.5px">系统提出建议，由你做最终判断。{{ kindHint }}</span>
      <div class="grow"></div>
      <button class="btn primary" :disabled="busy || !highConfidence.length" @click="acceptHighConfidence">
        接受高置信（{{ highConfidence.length }}）
      </button>
      <button class="btn danger" :disabled="busy" @click="rejectAll">全部拒绝…</button>
    </div>

    <div class="panel pad" style="padding:6px">
      <div v-for="r in rows" :key="r.id" class="cand">
        <div class="item" style="cursor:default">
          <div class="ico-badge" :class="kind === 'entities' ? 'ib-blue' : kind === 'claims' ? 'ib-violet' : 'ib-mint'">✓</div>
          <div class="grow">
            <b>{{ label(r) }}</b>
            <p>{{ sub(r) }}</p>
            <div style="margin-top:5px">
              <span v-if="confidenceOf(r) != null" class="tag" :class="isHigh(r) ? 'green' : ''">
                confidence {{ fmtConfidence(r) }}
              </span>
              <span v-if="r.type" class="tag blue">{{ r.type }}</span>
              <span v-if="r.modality" class="tag">{{ r.modality }} · {{ r.polarity }}</span>
              <span v-if="r.claim_type" class="tag">{{ r.claim_type }}</span>
            </div>
          </div>
          <div class="row" style="gap:6px;flex:none">
            <button class="btn sm" @click="toggle(r.id)">{{ isOpen(r.id) ? '收起' : '详情' }}</button>
            <button class="btn sm" :disabled="busy" @click="decide(r, 'verified')">通过</button>
            <button class="btn sm danger" :disabled="busy" @click="decide(r, 'rejected')">拒绝</button>
          </div>
        </div>

        <div v-if="isOpen(r.id)" class="canddetail">
          <!-- 实体候选：先讲清楚「为什么会有它」和「是否已经有一个」 -->
          <template v-if="kind === 'entities'">
            <div v-if="detailOf(r.id)?.loading" class="faint" style="font-size:9px">正在查找来源与相似实体…</div>
            <template v-else>
              <div v-if="duplicatesOf(r.id).length">
                <div class="sechead" style="margin-top:0"><h3>可能的重复</h3></div>
                <div v-for="d in duplicatesOf(r.id)" :key="d.id" class="item" style="cursor:default">
                  <div class="grow">
                    <b>{{ d.name }}</b>
                    <p>{{ d.type }} · 相似度 {{ Math.round(d.similarity * 100) }}% · {{ statusStyle(d.status).label }}</p>
                  </div>
                  <button class="btn sm" @click="router.push('/knowledge/object/' + d.id)">查看</button>
                </div>
                <p class="muted" style="font-size:9px;margin:8px 0 0">
                  相似度 ≥93% 会被自动合并；以下低于该阈值，需要你判断是否为同一个实体。
                </p>
              </div>
              <div v-if="evidenceOf(r.id)" :style="duplicatesOf(r.id).length ? 'margin-top:14px' : ''">
                <div class="sechead" style="margin-top:0"><h3>来自这段原文</h3></div>
                <div class="evidence">“{{ evidenceOf(r.id)?.quote }}”</div>
                <button class="btn sm" style="margin-top:8px" @click="openEntitySource(evidenceOf(r.id))">打开原文并定位 →</button>
              </div>
              <div v-if="!duplicatesOf(r.id).length && !evidenceOf(r.id)">
                <div class="sechead" style="margin-top:0"><h3>为什么会有这条候选</h3></div>
                <div class="notice violet">
                  抽取时从文档里识别出一个 <b>{{ r.type }}</b> 类实体，暂存为候选等待你确认。尚未找到关联的原文引用。
                </div>
              </div>
            </template>
          </template>

          <!-- Claim：来源原文 -->
          <template v-else-if="r.source_quote">
            <div class="sechead" style="margin-top:0"><h3>来源原文</h3></div>
            <div class="evidence">“{{ r.source_quote }}”
              <div v-if="r.source_start_offset != null" class="src">offset [{{ r.source_start_offset }}, {{ r.source_end_offset }})</div>
            </div>
            <button class="btn sm" style="margin-top:8px" @click="openSource(r)">打开原文并定位 →</button>
          </template>

          <!-- 关系：说明为什么没有原文 -->
          <template v-else>
            <div class="sechead" style="margin-top:0"><h3>为什么会有这条候选</h3></div>
            <div class="notice violet">
              <template v-if="kind === 'relations'">
                这条关系由实体间的高确定性断言派生，确认后进入图谱。该候选没有可定位的原文引用。
              </template>
              <template v-else>该候选没有附带的原文引用。</template>
            </div>
            <button v-if="r.source_document_id" class="btn sm" style="margin-top:8px" @click="openSource(r)">打开来源文档 →</button>
          </template>
        </div>
      </div>

      <EmptyState
        v-if="!rows.length"
        :title="filter ? '没有匹配的候选' : '这里已经清空了'"
        :text="filter ? '换个关键词，或清除搜索查看全部候选。' : '没有等待你判断的候选知识——导入并抽取文档后，新候选会出现在这里。'"
      >
        <template #action>
          <button v-if="filter" class="btn" @click="filter = ''">清除搜索</button>
          <button v-else class="btn primary" @click="router.push('/knowledge')">去导入文档</button>
        </template>
      </EmptyState>
    </div>

    <div class="notice violet" style="margin-top:12px">
      通过（verified）的知识会成为可信锚点进入图谱与问答；拒绝（rejected）会被检索与图谱排除。两种操作都可以在提示条里「撤销」。
      <span v-if="kind === 'entities'" style="cursor:pointer;text-decoration:underline" @click="router.push('/knowledge')">在知识库中查看实体详情 →</span>
    </div>
  </div>
</template>

<style scoped>
.bulbar{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.cand + .cand{border-top:1px solid var(--hair)}
.canddetail{margin:6px 0 8px 42px;padding:12px 14px;background:var(--surface2);border-radius:12px}
</style>
