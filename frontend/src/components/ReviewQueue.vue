<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { api, patchJson } from '../api/client'
import KpiStrip from './KpiStrip.vue'
import EmptyState from './EmptyState.vue'
import LoadBoundary from './LoadBoundary.vue'
import { useAppStore } from '../stores/app'
import { statusLabel } from '../utils/status'
import { useAsyncState } from '../utils/useAsyncState'

const { t } = useI18n()
const router = useRouter()
const store = useAppStore()

type Kind = 'entities' | 'claims' | 'relations'
type Decision = 'verified' | 'rejected'

const kind = ref<Kind>('entities')
const filter = ref('')
const busy = ref(false)
const expanded = ref<Set<string>>(new Set())

/** Above this, a batch accept is a safe bulk operation; below it, ask the human. */
const HIGH_CONFIDENCE = 0.9

/* 不传 limit：窗口由后端默认值决定，Review Inbox 数的是同一个窗口——
   角标和它打开的这一页必须描述同一批东西。

   读不到时**绝不能说「已经清空」**：那是这个界面能说出的最危险的一句话，它会让用户
   放弃本来必须做的确认，并且以为候选知识已经被处理过了。所以成功、为空、读不到
   是三件不同的事，由 LoadBoundary 分别渲染。 */
const review = useAsyncState(
  () => api<{ entities: any[]; claims: any[]; relations: any[] }>('/api/review'),
  { entities: [], claims: [], relations: [] },
)
const data = review.data

/** 后端对每类候选默认上限 200、且不支持 offset：列表触顶时不许假装这就是全部。 */
const listCapped = computed(() => (data.value[kind.value]?.length || 0) >= 200)

async function load() {
  expanded.value = new Set()
  await review.reload()
}
onMounted(load)

const kindLabel = (k: Kind) => t('rq.kind.' + k)

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
  if (kind.value === 'entities') return t('rq.kindHintEntities')
  return t('rq.kindHintOther', { n: Math.round(HIGH_CONFIDENCE * 100) })
})

/* ---------- entity candidate detail（为什么创建 / 是否重复） ----------
   展开时才按需拉取：200 条候选不该在列表加载时打 200 个请求。 */
interface EntityDetail {
  loading: boolean
  /** 读不到详情时必须这么说：空的详情区会被读成「系统没有找到理由」。 */
  error: string
  evidence: { quote: string; documentId: string; chunkId: string } | null
  duplicates: { id: string; name: string; type: string; status: string; similarity: number }[]
}
const detailCache = ref<Record<string, EntityDetail>>({})

const detailOf = (id: string) => detailCache.value[id] || null
const duplicatesOf = (id: string) => detailCache.value[id]?.duplicates || []
const evidenceOf = (id: string) => detailCache.value[id]?.evidence || null

async function loadEntityDetail(id: string, force = false) {
  if (detailCache.value[id] && !force) return
  detailCache.value = { ...detailCache.value, [id]: { loading: true, error: '', evidence: null, duplicates: [] } }
  let obj: any = null, dup: any = null
  try {
    ;[obj, dup] = await Promise.all([
      api<any>('/api/entities/' + id + '/object'),
      api<any>('/api/entities/' + id + '/duplicates'),
    ])
  } catch (e: any) {
    detailCache.value = {
      ...detailCache.value,
      [id]: { loading: false, error: t('rq.entityError'), evidence: null, duplicates: [] },
    }
    void e
    return
  }
  // An entity has no document of its own; its first claim quote is the closest
  // thing to "the text that produced this".
  const sample = obj?.evidence?.[0] || obj?.claims?.[0] || null
  detailCache.value = {
    ...detailCache.value,
    [id]: {
      loading: false,
      error: '',
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
  if (!r.source_document_id) { store.toast(t('rq.noSource')); return }
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
  busy.value = true
  const done: any[] = []
  try {
    for (const r of items) {
      try { await patchJson(`/api/knowledge/${k}/${r.id}/status`, { status }); done.push(r) }
      catch { /* keep going — report partial success below */ }
    }
  } finally { busy.value = false }

  if (!done.length) { store.toast(t('rq.failed')); return }

  const ids = new Set(done.map(r => r.id))
  review.set({ ...data.value, [k]: (data.value[k] as any[]).filter(r => !ids.has(r.id)) })
  expanded.value = new Set()

  const verb = status === 'verified' ? t('rq.accept') : t('rq.reject')
  store.toast(t('rq.decided', { verb, n: done.length }), { label: t('common.undo'), run: () => undo(done, k, verb) })
}

async function undo(items: any[], k: Kind, verb: string) {
  let ok = 0
  for (const r of items) {
    try { await patchJson(`/api/knowledge/${k}/${r.id}/status`, { status: 'candidate' }); ok++ }
    catch { /* keep going */ }
  }
  await load()
  store.toast(ok === items.length ? t('rq.reverted', { verb }) : t('rq.revertedPart', { ok, total: items.length }))
}

const decide = (r: any, status: Decision) => applyStatus([r], status)
const acceptHighConfidence = () => applyStatus(highConfidence.value, 'verified')

function rejectAll() {
  const list = [...rows.value]
  if (!list.length) return
  if (!confirm(t('rq.rejectAllConfirm', { n: list.length }))) return
  applyStatus(list, 'rejected')
}
</script>

<template>
  <div>
    <!-- 读不到的时候，连"还有 0 件待审"都不许说。
         统计、分类计数、批量操作、列表全部在成功分支里——它们在读不到时全是谎话。 -->
    <LoadBoundary :state="review.state.value"
                  :loading-text="t('rq.loading')"
                  :error-title="t('rq.errorTitle')"
                  :error-text="t('rq.errorText')"
                  :reload="load">
    <KpiStrip :cells="[
      { label: t('rq.kpi.entities'), value: data.entities.length },
      { label: t('rq.kpi.claims'), value: data.claims.length },
      { label: t('rq.kpi.relations'), value: data.relations.length },
      { label: t('rq.kpi.total'), value: data.entities.length + data.claims.length + data.relations.length },
    ]" />

    <div class="row" style="margin:16px 0 10px;flex-wrap:wrap;gap:8px">
      <div class="seg">
        <button v-for="k in (['entities', 'claims', 'relations'] as Kind[])" :key="k" :class="{ active: kind === k }" @click="switchKind(k)">
          {{ kindLabel(k) }} ({{ (data[k] || []).length }})
        </button>
      </div>
      <div class="grow"></div>
      <input v-model="filter" class="field wide" :placeholder="t('rq.searchPlaceholder')" />
      <button class="btn" :disabled="busy" @click="load">{{ t('rq.refresh') }}</button>
    </div>

    <div v-if="rows.length" class="panel pad bulbar">
      <span class="muted" style="font-size:9.5px">{{ t('rq.hint', { extra: kindHint }) }}</span>
      <div class="grow"></div>
      <button class="btn primary" :disabled="busy || !highConfidence.length" @click="acceptHighConfidence">
        {{ t('rq.acceptHigh', { n: highConfidence.length }) }}
      </button>
      <button class="btn danger" :disabled="busy" @click="rejectAll">{{ t('rq.rejectAll') }}</button>
    </div>

    <div v-if="listCapped" class="notice violet" style="margin:0 0 12px">
      {{ t('rq.capped') }}
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
            <button class="btn sm" @click="toggle(r.id)">{{ isOpen(r.id) ? t('rq.collapse') : t('rq.detail') }}</button>
            <button class="btn sm" :disabled="busy" @click="decide(r, 'verified')">{{ t('rq.accept') }}</button>
            <button class="btn sm danger" :disabled="busy" @click="decide(r, 'rejected')">{{ t('rq.reject') }}</button>
          </div>
        </div>

        <div v-if="isOpen(r.id)" class="canddetail">
          <!-- 实体候选：先讲清楚「为什么会有它」和「是否已经有一个」 -->
          <template v-if="kind === 'entities'">
            <div v-if="detailOf(r.id)?.loading" class="faint" style="font-size:9px">{{ t('rq.entityLoading') }}</div>
            <!-- 详情读不到时说"读不到"，而不是摆出一个空的"为什么会有这条候选" -->
            <div v-else-if="detailOf(r.id)?.error" class="row" style="gap:10px">
              <span class="muted" style="font-size:10px">{{ detailOf(r.id)?.error }}</span>
              <button class="btn sm" @click="loadEntityDetail(r.id, true)">{{ t('rq.entityErrorReload') }}</button>
            </div>
            <template v-else>
              <div v-if="duplicatesOf(r.id).length">
                <div class="sechead" style="margin-top:0"><h3>{{ t('rq.possibleDup') }}</h3></div>
                <div v-for="d in duplicatesOf(r.id)" :key="d.id" class="item" style="cursor:default">
                  <div class="grow">
                    <b>{{ d.name }}</b>
                    <p>{{ d.type }} · 相似度 {{ Math.round(d.similarity * 100) }}% · {{ statusLabel(d.status) }}</p>
                  </div>
                  <button class="btn sm" @click="router.push('/knowledge/object/' + d.id)">{{ t('rq.detail') }}</button>
                </div>
                <p class="muted" style="font-size:9px;margin:8px 0 0">
                  {{ t('rq.dupHint') }}
                </p>
              </div>
              <div v-if="evidenceOf(r.id)" :style="duplicatesOf(r.id).length ? 'margin-top:14px' : ''">
                <div class="sechead" style="margin-top:0"><h3>{{ t('rq.fromQuote') }}</h3></div>
                <div class="evidence">“{{ evidenceOf(r.id)?.quote }}”</div>
                <button class="btn sm" style="margin-top:8px" @click="openEntitySource(evidenceOf(r.id))">{{ t('rq.openSource') }}</button>
              </div>
              <div v-if="!duplicatesOf(r.id).length && !evidenceOf(r.id)">
                <div class="sechead" style="margin-top:0"><h3>{{ t('rq.whyCandidate') }}</h3></div>
                <div class="notice violet">
                  {{ t('rq.whyEntity', { type: r.type }) }}
                </div>
              </div>
            </template>
          </template>

          <!-- Claim：来源原文 -->
          <template v-else-if="r.source_quote">
            <div class="sechead" style="margin-top:0"><h3>{{ t('rq.sourceQuote') }}</h3></div>
            <div class="evidence">“{{ r.source_quote }}”
              <div v-if="r.source_start_offset != null" class="src">offset [{{ r.source_start_offset }}, {{ r.source_end_offset }})</div>
            </div>
            <button class="btn sm" style="margin-top:8px" @click="openSource(r)">{{ t('rq.openSource') }}</button>
          </template>

          <!-- 关系：说明为什么没有原文 -->
          <template v-else>
            <div class="sechead" style="margin-top:0"><h3>{{ t('rq.whyCandidate') }}</h3></div>
            <div class="notice violet">
              <template v-if="kind === 'relations'">
                {{ t('rq.whyRelation') }}
              </template>
              <template v-else>{{ t('rq.noQuote') }}</template>
            </div>
            <button v-if="r.source_document_id" class="btn sm" style="margin-top:8px" @click="openSource(r)">{{ t('rq.openDoc') }}</button>
          </template>
        </div>
      </div>

      <!-- 这句话只在"确实读到了、而且确实没有"时才出现 -->
      <EmptyState
        v-if="!rows.length"
        :title="filter ? t('rq.emptyFilter') : t('rq.emptyTitle')"
        :text="filter ? t('rq.emptyFilterText') : t('rq.emptyText')"
      >
        <template #action>
          <button v-if="filter" class="btn" @click="filter = ''">{{ t('rq.clearSearch') }}</button>
          <button v-else class="btn primary" @click="router.push('/knowledge')">{{ t('rq.goImport') }}</button>
        </template>
      </EmptyState>
    </div>
    </LoadBoundary>

    <div class="notice violet" style="margin-top:12px">
      {{ t('rq.verdict') }}
      <span v-if="kind === 'entities'" style="cursor:pointer;text-decoration:underline" @click="router.push('/knowledge')">{{ t('rq.viewEntity') }}</span>
    </div>
  </div>
</template>

<style scoped>
.bulbar{display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.cand + .cand{border-top:1px solid var(--hair)}
.canddetail{margin:6px 0 8px 42px;padding:12px 14px;background:var(--surface2);border-radius:12px}
</style>
