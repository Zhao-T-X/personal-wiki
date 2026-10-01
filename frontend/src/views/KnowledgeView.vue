<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Search, Sparkles, Zap } from 'lucide-vue-next'
import cytoscape from 'cytoscape'
import { api, post, del } from '../api/client'
import { TYPE_COLORS, DEFAULT_NODE_COLOR } from '../utils/graph'
import type { DocumentRow, Chunk, Entity, EventRow } from '../api/types'
import ImportPanel from '../components/ImportPanel.vue'
import KnowledgeCard from '../components/KnowledgeCard.vue'
import SegTabs from '../components/SegTabs.vue'
import StatusTag from '../components/StatusTag.vue'
import MarkdownView from '../components/MarkdownView.vue'
import AppDrawer from '../components/AppDrawer.vue'
import AppModal from '../components/AppModal.vue'
import EmptyState from '../components/EmptyState.vue'
import IntegrityPanel from '../components/IntegrityPanel.vue'
import LoadBoundary from '../components/LoadBoundary.vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useAppStore } from '../stores/app'
import { toCard } from '../utils/claim'
import { safeErrorText } from '../utils/dataState'
import { useAsyncState } from '../utils/useAsyncState'
import { fmtDateTime } from '../utils/time'
import { ENTITY_TYPES, entityTypeLabel } from '../utils/entityType'

const route = useRoute()
const router = useRouter()
const store = useAppStore()
const { t } = useI18n()

/** tab 的内部值是稳定键，标签走 i18n（SegTabs 支持 {value,label}）。 */
const tab = ref(route.query.tab === 'graph' ? 'graph' : route.query.tab === 'timeline' ? 'timeline' : 'docs')
const TABS = computed(() => [
  { value: 'docs', label: t('knowledge.tab.docs') },
  { value: 'graph', label: t('knowledge.tab.graph') },
  { value: 'timeline', label: t('knowledge.tab.timeline') },
])
watch(() => route.query.tab, t => {
  if (t === 'graph') tab.value = 'graph'
  else if (t === 'timeline') tab.value = 'timeline'
})
watch(tab, t => { if (t === 'graph') setTimeout(drawGraph, 50) })

/* ---------- 搜索：先给知识，再给原文 ----------
 *
 * 召回没有变（FTS5 + 语义 + RRF 仍然找 chunk），变的是呈现顺序：`/api/search/knowledge`
 * 在同一批召回之上投影出「知识库里现在记着的事」，并带上它的状态与依据。原文仍然
 * 完整返回在 results 里，只是退到第二层——用户搜到的东西不该是一条片段，而是答案。
 */
const searchQ = ref((route.query.q as string) || '')
const searching = ref(false)
const searchResults = ref<any[]>([])
/** Projected claims for the same query — the knowledge layer. */
const searchKnowledge = ref<any[]>([])
/** 搜索失败必须说出来：一次失败的检索和「库里没有」看起来完全一样。 */
const searchError = ref('')
let searchTimer: number | undefined
/** Why a result matched — key into `knowledge.match.*`, translated in the template. */
const MATCH_KEYS: Record<string, string> = {
  title: 'knowledge.match.title',
  content: 'knowledge.match.content',
  semantic: 'knowledge.match.semantic',
}
function matchLabel(m: string) {
  const key = MATCH_KEYS[m]
  return key ? t(key) : m
}

watch(searchQ, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(runSearch, 300)
})

async function runSearch() {
  const q = searchQ.value.trim()
  searchError.value = ''
  if (!q) { searchResults.value = []; searchKnowledge.value = []; return }
  searching.value = true
  try {
    const body = await api<{ knowledge: any[]; results: any[] }>(
      `/api/search/knowledge?q=${encodeURIComponent(q)}&limit=20&semantic=true`)
    searchKnowledge.value = body.knowledge || []
    searchResults.value = body.results || []
  } catch (e: any) {
    searchKnowledge.value = []
    searchResults.value = []
    searchError.value = t('knowledge.searchError')
    void e
  } finally { searching.value = false }
}

function openResult(r: any) {
  const doc = docs.value.find(d => d.id === r.document_id)
  if (doc) openSource(r.document_id, r.id)
  else router.push('/knowledge/object/' + r.document_id)
}

/* ---------- documents ----------
   三个数据面各有各的状态：文档读不到不该让实体列表也变成错误页，但两者都不许
   把失败说成"空"。 */
const entityTotal = ref(0)
const docTotal = ref(0)
const docLimit = ref(50)

const docsRes = useAsyncState(async () => {
  const ds = await api<DocumentRow[]>(`/api/documents?limit=${docLimit.value}`)
  if (ds.length) docTotal.value = (ds[0] as any).total ?? ds.length
  return ds
}, [] as DocumentRow[])
const entitiesRes = useAsyncState(async () => {
  const es = await api<Entity[]>('/api/entities?limit=60&offset=0')
  if (es.length) entityTotal.value = (es[0] as any).total ?? es.length
  return es
}, [] as Entity[])
const eventLimit = ref(100)
const eventsRes = useAsyncState(
  () => api<EventRow[]>('/api/events?limit=' + eventLimit.value), [] as EventRow[])

const docs = docsRes.data
const entities = entitiesRes.data
const events = eventsRes.data
const loadDocs = docsRes.reload
const loadEntities = entitiesRes.reload
const loadEvents = eventsRes.reload

/** 追加一页是动作，不是页面加载：失败要单独说，而不是把已读到的列表也算废。 */
async function moreEntities() {
  try {
    const es = await api<Entity[]>(`/api/entities?limit=60&offset=${entities.value.length}`)
    if (es.length) entityTotal.value = (es[0] as any).total ?? entityTotal.value
    entitiesRes.set([...entities.value, ...es])
  } catch (e: any) { store.toast(t('knowledge.errMoreEntities', { msg: e.message })) }
}

/** 时间线同样按页加载：事件可能成千上万，一次读全既慢又淹没真正要看的那几条。 */
async function moreEvents() {
  try {
    const es = await api<EventRow[]>(`/api/events?limit=${eventLimit.value}&offset=${events.value.length}`)
    eventsRes.set([...events.value, ...es])
  } catch (e: any) { store.toast(t('knowledge.errMoreEvents', { msg: e.message || t('knowledge.retryLater') })) }
}

const drawerDoc = ref<DocumentRow | null>(null)
const drawerChunks = ref<Chunk[]>([])
const drawerKnowledge = ref<any>(null)
/** 抽屉里"读不到它产生的知识"要单独说：整篇打不开已经由 toast 说了。 */
const drawerKnowledgeError = ref('')
/** The drawer's rows go through the same normaliser every other surface uses, so
    there is one answer to "what does a claim look like in this product". */
const drawerCards = computed(() => (drawerKnowledge.value?.claims || [])
  .slice(0, 20)
  .map((c: any) => toCard(c, { documentId: drawerDoc.value?.id })))
const showNew = ref(false)
const showNewEntity = ref(false)
const newTitle = ref(''); const newBody = ref(''); const newType = ref('note')
const entName = ref(''); const entType = ref('Technology'); const entDesc = ref('')
const evType = ref('meeting'); const evDesc = ref(''); const evDate = ref(''); const showNewEvent = ref(false)

/* ---------- timeline grouping ---------- */
/** Day bucket: the date extracted from the source text when present, else the creation date. */
function eventDay(e: EventRow): string {
  const raw = (e.time?.start as string | undefined) || e.created_at || ''
  return raw.slice(0, 10) || t('knowledge.unknownDate')
}
/** Clock shown next to an event; empty for date-only events. */
function eventClock(e: EventRow): string {
  const raw = e.time?.start as string | undefined
  if (raw && raw.length > 10) return raw.slice(11, 19)
  if (raw) return ''
  return fmtDateTime(e.created_at).slice(11)
}
const groupedEvents = computed(() => {
  const days = new Map<string, EventRow[]>()
  for (const e of events.value) {
    const d = eventDay(e)
    days.set(d, [...(days.get(d) || []), e])
  }
  return [...days.entries()]
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([date, items]) => ({ date, items }))
})

async function loadAll() { await Promise.all([loadDocs(), loadEntities(), loadEvents()]) }
onMounted(async () => {
  void store.loadPendingReview()
  await loadAll()
  if (searchQ.value) runSearch()
  if (tab.value === 'graph') setTimeout(drawGraph, 50)
  if (route.query.doc) await openSource(route.query.doc as string, route.query.chunk as string | undefined)
})
watch(() => route.query.doc, d => { if (d) openSource(d as string, route.query.chunk as string | undefined) })

/** 打开抽屉。返回是否打开成功——调用方（`openSource`）要据此决定还要不要滚动定位。
 *  打不开时抽屉没有可承载错误的地方，所以走 toast（原因已消毒，不给人看堆栈）。 */
async function openDoc(d: DocumentRow): Promise<boolean> {
  drawerKnowledgeError.value = ''
  drawerKnowledge.value = null
  try {
    drawerDoc.value = await api<DocumentRow>('/api/documents/' + d.id)
    drawerChunks.value = await api<Chunk[]>('/api/documents/' + d.id + '/chunks')
  } catch (e: any) {
    drawerDoc.value = null
    store.toast(t('knowledge.openDocFailed', { msg: safeErrorText(e) || t('knowledge.openDocFailedDefault') }))
    return false
  }
  await loadDrawerKnowledge()
  return true
}

/** 「这篇产生了什么知识」比原文更该先看到——抽屉的默认内容不该是 raw markdown。
 *  它读不到时要说出来：一个空白的"知识"区看起来就像这篇什么都没抽出来。 */
async function loadDrawerKnowledge() {
  if (!drawerDoc.value) return
  drawerKnowledgeError.value = ''
  try {
    drawerKnowledge.value = await api<any>('/api/documents/' + drawerDoc.value.id + '/knowledge')
  } catch {
    drawerKnowledge.value = null
    drawerKnowledgeError.value = t('knowledge.noKnowledgeYet')
  }
}

/** 卡片被就地改过（自带纠正）之后重读一次，否则卡片还显示旧值。 */
const refreshDrawerKnowledge = loadDrawerKnowledge

/* ---------- source jump (?doc=&chunk=) ---------- */
const chunksBox = ref<HTMLDetailsElement | null>(null)
const highlightChunk = ref<string | null>(null)

/** Open a document drawer and, when given, reveal + highlight the originating chunk. */
async function openSource(docId: string, chunkId?: string) {
  tab.value = 'docs'
  highlightChunk.value = chunkId || null
  if (!await openDoc({ id: docId } as DocumentRow)) return
  await nextTick()
  if (chunksBox.value) chunksBox.value.open = true
  await nextTick()
  document.querySelector('.chunk.hl')?.scrollIntoView({ block: 'center' })
}

/* ---------- extraction ----------
   进度由全局 store 持有（RunProgress 常驻），抽取期间切换页面不会丢失状态。 */
async function actDoc(d: DocumentRow, action: 'index' | 'local' | 'embed' | 'delete') {
  try {
    if (action === 'delete') {
      if (!confirm(t('knowledge.confirmDelete'))) return
      await del('/api/documents/' + d.id); drawerDoc.value = null; await loadAll(); return
    }
    const suffix = action === 'index' ? '/index' : action === 'local' ? '/index/local' : '/embed'
    if (action === 'index') store.beginExtraction(d.id, d.title)
    try {
      const r = await post('/api/documents/' + d.id + suffix)
      if (action === 'index') {
        store.clearExtraction()
        store.toast(t('knowledge.understood', { title: d.title }), { label: t('knowledge.goReview'), run: () => router.push('/review') })
      } else {
        store.toast(t('knowledge.done', { summary: JSON.stringify(r).slice(0, 60) }))
      }
    } catch (e: any) {
      if (action === 'index') store.clearExtraction()
      throw e
    }
    await loadAll()
    if (action === 'index' && drawerDoc.value?.id === d.id) await openDoc(drawerDoc.value)
  } catch (e: any) { store.toast(e.message) }
}

async function backfillEmbeddings() {
  embedding.value = true
  try {
    const r = await post<any>('/api/embeddings/backfill')
    store.toast(t('knowledge.embedded', { docs: r.embedded_documents, chunks: r.chunks, model: r.model }))
    await loadAll()
  } catch (e: any) { store.toast(e.message) } finally { embedding.value = false }
}
const embedding = ref(false)

async function saveNote() {
  try {
    await post('/api/documents', { title: newTitle.value, content: newBody.value, source_type: newType.value })
    showNew.value = false; newTitle.value = ''; newBody.value = ''
    store.toast(t('knowledge.created')); await loadAll()
  } catch (e: any) { store.toast(e.message) }
}
async function saveEntity() {
  try {
    await post('/api/entities', { name: entName.value, type: entType.value, description: entDesc.value || null })
    showNewEntity.value = false; entName.value = ''; entDesc.value = ''
    store.toast(t('knowledge.entityCreated')); await loadEntities()
  } catch (e: any) { store.toast(e.message) }
}
async function saveEvent() {
  try {
    await post('/api/events', { event_type: evType.value, description: evDesc.value, time: evDate.value ? { start: evDate.value, precision: 'day' } : {} })
    showNewEvent.value = false; evDesc.value = ''; evDate.value = ''
    store.toast(t('knowledge.eventCreated')); await loadEvents()
  } catch (e: any) { store.toast(e.message) }
}
/* ---------- import pipeline ----------
   导入是产品第一印象，所以它由 ImportPanel 提供：首页和这里用的是同一个组件、
   同一条流水线（import → parse → chunk → analyze，串行）。
   同一件事不能有两套实现、两种行为——这里只负责「抽完之后这一页要跟着更新」。 */
const importer = ref<any>(null)
async function onImported(documentId: string) {
  await loadAll()
  if (drawerDoc.value?.id === documentId) await openDoc(drawerDoc.value)
}
function openDocById(documentId: string) {
  void openDoc({ id: documentId } as DocumentRow)
}

/* ---------- graph ---------- */
const graphBox = ref<HTMLElement | null>(null)
/** 图谱只采样部分关系网络——把"显示了多少"说出来，否则用户以为看到的就全是关系。 */
const graphInfo = ref<{ nodes: number; edges: number }>({ nodes: 0, edges: 0 })
let cy: cytoscape.Core | null = null
async function drawGraph() {
  if (!graphBox.value) return
  const g = await api<any>('/api/graph?limit=200')
  graphInfo.value = { nodes: g.nodes?.length || 0, edges: g.edges?.length || 0 }
  const elements = [
    ...g.nodes.map((n: any) => ({ data: { id: n.id, label: n.name, color: TYPE_COLORS[n.type] || DEFAULT_NODE_COLOR, verified: n.status === 'verified' } })),
    ...g.edges.map((e: any) => ({ data: { id: e.id, source: e.source_id, target: e.target_id, label: e.predicate } })),
  ]
  cy?.destroy()
  cy = cytoscape({
    container: graphBox.value, elements,
    layout: { name: 'cose', animate: false, padding: 30 },
    style: [
      { selector: 'node', style: { 'background-color': 'data(color)', label: 'data(label)', color: '#fff', 'font-size': 9, width: 30, height: 30, 'text-valign': 'center', 'text-halign': 'center', opacity: 0.72 } },
      { selector: 'node[?verified]', style: { opacity: 1, width: 38, height: 38, 'border-width': 2, 'border-color': '#16203a' } },
      { selector: 'edge', style: { width: 1.5, 'line-color': '#c2cde3', 'target-arrow-color': '#c2cde3', 'target-arrow-shape': 'triangle', label: 'data(label)', 'font-size': 7, color: '#8595b0', 'curve-style': 'bezier' } },
    ],
  })
  cy.on('tap', 'node', (evt) => router.push('/knowledge/object/' + evt.target.id()))
  cy.fit(undefined, 40)
}

const showAllDocs = computed(() => docLimit.value >= docTotal.value)

/* ---------- v3 文档列表：过滤 + 分组 + 相对时间 ----------
 * 过滤是客户端的（数据已在本页），分组只回答一个问题：最近动过的在哪。 */
const DAY = 86400e3
/** 过滤器内部值是稳定键，标签走 i18n。 */
const DOC_FILTERS = ['all', 'recent', 'verified', 'pending'] as const
const docFilter = ref<string>('all')
const docFilterOptions = computed(() => DOC_FILTERS.map(f => ({ value: f, label: t(`knowledge.filter.${f}`) })))
function docTime(d: DocumentRow): number {
  const t = new Date(String(d.updated_at || '').replace(' ', 'T')).getTime()
  return isNaN(t) ? 0 : t
}
function isRecentDoc(d: DocumentRow) { return docTime(d) > Date.now() - 7 * DAY }
const filteredDocs = computed(() => {
  if (docFilter.value === 'pending') return docs.value.filter(d => !d.last_run_status)
  if (docFilter.value === 'verified') return docs.value.filter(d => !!d.last_run_status)
  if (docFilter.value === 'recent') return docs.value.filter(isRecentDoc)
  return docs.value
})
const recentDocs = computed(() => filteredDocs.value.filter(isRecentDoc))
const olderDocs = computed(() => filteredDocs.value.filter(d => !isRecentDoc(d)))
function relTime(ts?: string): string {
  if (!ts) return ''
  const ts0 = docTime({ updated_at: ts } as DocumentRow)
  if (!ts0) return String(ts).slice(0, 10)
  const days = Math.floor((Date.now() - ts0) / DAY)
  if (days <= 0) return t('knowledge.today')
  if (days === 1) return t('knowledge.yesterday')
  if (days < 7) return t('knowledge.daysAgo', { n: days })
  if (days < 30) return t('knowledge.weeksAgo', { n: Math.floor(days / 7) })
  return String(ts).slice(0, 10)
}
function docIcon(d: DocumentRow): { label: string; cls: string } {
  const src = (d.source_type || '').toLowerCase()
  if (src.includes('pdf')) return { label: 'PDF', cls: 'pdf' }
  if (src.includes('url') || src.includes('web') || src.includes('link')) return { label: 'URL', cls: 'url' }
  if (src.includes('md') || src.includes('html')) return { label: 'MD', cls: 'md' }
  return { label: 'TXT', cls: '' }
}
</script>

<template>
  <div class="page">
    <!-- v3 页头：标题 + 轻导入入口。导入不再是一张大拖拽框 -->
    <div class="khead">
      <div>
        <div class="eyebrow">KNOWLEDGE</div>
        <h1 class="ktitle">{{ t('knowledge.title') }}</h1>
        <p class="ksub">{{ t('knowledge.sub') }}</p>
      </div>
      <div class="row" style="gap:8px">
        <button class="btn" @click="importer?.pick()">{{ t('knowledge.importBtn') }}</button>
      </div>
    </div>

    <!-- 全页只有一个搜索框：找知识、找正文、找对象、找文档都从它进 -->
    <div class="ksearch">
      <input v-model="searchQ" :placeholder="t('knowledge.searchPlaceholder')" />
      <span v-if="searching" class="tag">{{ t('knowledge.searching') }}</span>
    </div>

    <!-- 搜索结果：知识是第一层，原文是它的依据 -->
    <div v-if="searchQ.trim()" class="panel pad" style="margin-bottom:16px">
      <template v-if="searchKnowledge.length">
        <div class="sechead" style="margin:0 0 10px">
          <h3>{{ t('knowledge.hitsTitle') }}</h3>
          <span class="faint" style="font-size:9px">{{ t('knowledge.hitsMeta', { n: searchKnowledge.length }) }}</span>
        </div>
        <div class="khits">
          <KnowledgeCard v-for="k in searchKnowledge" :key="k.claim_id" :claim="toCard(k)"
                         :evidence-count="k.sources" compact @changed="runSearch" />
        </div>
      </template>

      <div class="sechead" :style="searchKnowledge.length ? 'margin:16px 0 8px' : 'margin:0 0 8px'">
        <h3>{{ searchKnowledge.length ? t('knowledge.sourcesTitle') : t('knowledge.resultsTitle') }}</h3>
        <span class="faint" style="font-size:9px">{{ t('knowledge.resultsMeta', { n: searchResults.length }) }}</span>
      </div>
      <div v-for="(r, i) in searchResults" :key="i" class="item" @click="openResult(r)">
        <div class="ico-badge ib-blue"><Search :size="14" /></div>
        <div class="grow">
          <b>{{ r.title }}</b>
          <p>{{ (r.content || '').slice(0, 140) }}</p>
          <div style="margin-top:4px">
            <span v-for="m in (r.matched_in || [])" :key="m" class="tag blue">{{ matchLabel(m) }}</span>
            <span class="tag">{{ t('knowledge.chunkNo', { n: r.chunk_index ?? '—' }) }}</span>
            <span class="tag">{{ t('knowledge.openSource') }}</span>
          </div>
        </div>
      </div>
      <!-- 搜索失败时明确说是失败：此时空结果不代表"库里没有" -->
      <div v-if="searchError" class="notice red" style="margin:0">{{ searchError }}</div>
      <EmptyState v-else-if="!searchResults.length && !searching" :text="t('knowledge.noResults')" />
    </div>

    <SegTabs v-model="tab" :options="TABS" style="margin:0 0 14px" />

    <!-- 文档 -->
    <div v-if="tab === 'docs'">
      <!-- 导入口与首页是同一个组件、同一份实现；这里只保留进度队列，入口在页头 -->
      <ImportPanel ref="importer" hide-dropzone @imported="onImported" @open-document="openDocById" />

      <div class="filter-tabs">
        <button v-for="f in docFilterOptions" :key="f.value" class="ftab" :class="{ active: docFilter === f.value }" @click="docFilter = f.value">{{ f.label }}</button>
        <div class="grow"></div>
        <button class="btn sm" :disabled="embedding" @click="backfillEmbeddings">
          <Zap :size="11" style="vertical-align:-1px" /> {{ embedding ? t('knowledge.generating') : t('knowledge.reindex') }}
        </button>
        <button class="btn sm primary" @click="showNew = true">{{ t('knowledge.newDoc') }}</button>
      </div>
      <!-- 读不到文档时说"读不到"，而不是"还没有文档" -->
      <LoadBoundary :state="docsRes.state.value" :loading-text="t('knowledge.loadingDocs')"
                    :error-text="t('knowledge.errDocs')"
                    :reload="loadDocs">
        <template v-for="(group, gi) in [{ title: t('knowledge.group.recent'), meta: t('knowledge.group.recentMeta'), items: recentDocs },
                                        { title: t('knowledge.group.all'), meta: t('knowledge.group.allMeta', { n: docTotal }), items: olderDocs }]" :key="gi">
          <div v-if="group.items.length" class="docgroup">
            <div class="dghead"><b>{{ group.title }}</b><span class="faint">{{ group.meta }}</span></div>
            <div class="doclist">
              <div v-for="d in group.items" :key="d.id" class="docrow" @click="openDoc(d)">
                <div class="docicon" :class="docIcon(d).cls">{{ docIcon(d).label }}</div>
                <div class="docmain">
                  <b class="docname">{{ d.title }}</b>
                  <div class="docmeta">
                    {{ t('knowledge.chunksN', { n: d.chunk_count ?? 0 }) }} · {{ d.source_type }}
                    <span v-if="!d.last_run_status" class="docattn">{{ t('knowledge.unindexed') }}</span>
                  </div>
                </div>
                <div class="docright"><span class="doctime">{{ relTime(d.updated_at) }}</span></div>
              </div>
            </div>
          </div>
        </template>
        <EmptyState v-if="!filteredDocs.length" :text="t('knowledge.noDocs')" />
        <div v-if="!showAllDocs" style="text-align:center;margin-top:10px">
          <button class="btn" @click="docLimit += 50; loadDocs()">{{ t('knowledge.loadMoreDocs', { n: docTotal - docs.length }) }}</button>
        </div>
      </LoadBoundary>

      <div class="sechead"><h3>{{ t('knowledge.objects') }} <span class="faint" style="font-weight:400;font-size:9px">· {{ entities.length }}/{{ entityTotal }}</span></h3>
        <button class="btn" @click="showNewEntity = true">{{ t('knowledge.newEntity') }}</button>
      </div>
      <LoadBoundary :state="entitiesRes.state.value" :loading-text="t('knowledge.loadingEntities')"
                    :error-text="t('knowledge.errEntities')"
                    :reload="loadEntities">
        <div class="grid g3">
          <div v-for="e in entities" :key="e.id" class="panel pad item" style="display:block" @click="router.push('/knowledge/object/' + e.id)">
            <div class="row"><div class="ico-badge" :class="e.status === 'verified' ? 'ib-mint' : 'ib-blue'"><Sparkles :size="14" /></div>
              <div><b>{{ e.name }}</b><div class="faint" style="font-size:8.5px">{{ entityTypeLabel(e.type) }}</div></div></div>
            <hr class="hairline" />
            <p style="margin:0">{{ e.description?.slice(0, 60) || '—' }}</p>
            <div style="margin-top:9px"><StatusTag :status="e.status" /></div>
          </div>
        </div>
        <div v-if="entities.length < entityTotal" style="text-align:center;margin-top:10px">
          <button class="btn" @click="moreEntities">{{ t('knowledge.loadMoreEntities', { n: entityTotal - entities.length }) }}</button>
        </div>
      </LoadBoundary>

      <!-- v3 摘要条：状态收在页尾一行里，不再用首屏横幅提醒 -->
      <div class="ksummary">
        <div class="ksitems">
          <span class="ksi"><i class="ksdot"></i>{{ t('knowledge.summaryDocs', { n: docTotal }) }}</span>
          <span v-if="store.pendingReview" class="ksi attn"><i class="ksdot attn"></i>{{ t('knowledge.summaryPending', { n: store.pendingReview }) }}</span>
        </div>
        <button v-if="store.pendingReview" class="kslink" @click="router.push('/review')">{{ t('knowledge.viewPending') }}</button>
      </div>

      <!-- 知识体检：发现 → 影响 → 确认 → 结果。合并后立即重跑搜索，
           因为「后台修好了但搜索还显示旧状态」和没修一样糟。 -->
      <IntegrityPanel @changed="runSearch" />
    </div>

    <!-- 图谱 -->
    <div v-show="tab === 'graph'">
      <div class="row" style="margin-bottom:12px">
        <span class="faint" style="font-size:9px">
          <template v-if="graphInfo.nodes">{{ t('knowledge.graphInfo', { nodes: graphInfo.nodes, edges: graphInfo.edges }) }}</template>
          <template v-else>{{ t('knowledge.graphHint') }}</template>
        </span>
        <div class="grow"></div>
        <button class="btn sm" @click="drawGraph">{{ t('knowledge.relayout') }}</button>
      </div>
      <div ref="graphBox" class="gcanvas"></div>
    </div>

    <!-- 时间线 -->
    <div v-if="tab === 'timeline'">
      <div class="sechead"><h3>{{ t('knowledge.tab.timeline') }}</h3><button class="btn" @click="showNewEvent = true">{{ t('knowledge.newEvent') }}</button></div>
      <LoadBoundary :state="eventsRes.state.value" :loading-text="t('knowledge.loadingEvents')"
                    :error-text="t('knowledge.errEvents')"
                    :reload="loadEvents">
        <div class="panel pad">
          <div class="timeline">
            <template v-for="g in groupedEvents" :key="g.date">
              <div class="tlday">{{ g.date }}</div>
              <div v-for="e in g.items" :key="e.id" class="tle">
                <b>{{ e.description }}</b>
                <div>
                  <StatusTag :status="e.status" />
                  <span class="tag">{{ e.event_type }}</span>
                  <span v-if="e.location" class="tag">{{ e.location }}</span>
                  <span v-if="eventClock(e)" class="when">{{ eventClock(e) }}</span>
                </div>
              </div>
            </template>
            <EmptyState v-if="!events.length" :text="t('knowledge.noEvents')" />
          </div>
        </div>
      </LoadBoundary>
      <div v-if="events.length > 0 && events.length % eventLimit === 0" style="text-align:center;margin-top:10px">
        <button class="btn" @click="moreEvents">{{ t('knowledge.loadMoreEvents') }}</button>
      </div>
    </div>

    <AppDrawer :open="!!drawerDoc" :title="drawerDoc?.title || ''" @close="drawerDoc = null">
      <template v-if="drawerDoc">
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
          <StatusTag v-if="drawerDoc.last_run_status" :status="drawerDoc.last_run_status" />
          <span class="tag blue">{{ drawerDoc.source_type }}</span>
          <span class="tag">{{ t('knowledge.chunksN', { n: drawerDoc.chunk_count }) }}</span>
        </div>
        <div class="row" style="flex-wrap:wrap;gap:8px;margin-bottom:12px">
          <button class="btn primary" @click="actDoc(drawerDoc, 'index')">{{ t('knowledge.docActions.understand') }}</button>
          <button class="btn" @click="actDoc(drawerDoc, 'local')">{{ t('knowledge.docActions.chunkOnly') }}</button>
          <button class="btn" @click="actDoc(drawerDoc, 'embed')">{{ t('knowledge.docActions.embed') }}</button>
          <button class="btn danger" @click="actDoc(drawerDoc, 'delete')">{{ t('knowledge.docActions.delete') }}</button>
        </div>
        <!-- 这篇产生的知识。文档抽屉 = 该文档作用域的视图，不是"知识库首页"。
             读不到它时说明"读不到"——空白的知识区看起来就像这篇什么都没抽出来。 -->
        <div v-if="drawerKnowledgeError" class="notice violet" style="margin:14px 0 4px">{{ drawerKnowledgeError }}</div>
        <div v-else-if="drawerKnowledge?.claims?.length" style="margin:14px 0 4px">
          <div class="sechead" style="margin-top:0">
            <h3>{{ t('knowledge.docKnowledge') }}</h3>
            <span class="tag" style="margin:0">{{ t('knowledge.factsN', { n: drawerKnowledge.counts.claims }) }}</span>
          </div>
          <div class="dkl">
            <KnowledgeCard v-for="c in drawerCards" :key="c.id" :claim="c" compact
                           @changed="refreshDrawerKnowledge" />
          </div>
          <p v-if="drawerKnowledge.counts.claims > drawerCards.length" class="faint" style="font-size:9.5px;margin:7px 0 0">
            {{ t('knowledge.moreClaims', { n: drawerKnowledge.counts.claims - drawerCards.length }) }}
          </p>
        </div>

        <details ref="chunksBox" style="margin:10px 0">
          <summary style="cursor:pointer;font-size:10px;color:var(--sub)">{{ t('knowledge.chunksSummary', { n: drawerChunks.length }) }}</summary>
          <div v-for="c in drawerChunks" :key="c.id" class="listitem chunk" :class="{ hl: c.id === highlightChunk }" style="margin-top:6px">
            <b>#{{ c.chunk_index }}</b><span v-if="c.id === highlightChunk" class="tag blue" style="margin-left:6px">{{ t('knowledge.sourceChunk') }}</span><p>{{ c.content.slice(0, 160) }}…</p>
          </div>
        </details>
        <MarkdownView :content="drawerDoc.content || ''" />
      </template>
    </AppDrawer>

    <AppModal :open="showNew" :title="t('knowledge.modal.newDocTitle')" :subtitle="t('knowledge.modal.newDocSub')" @close="showNew = false">
      <div class="grid gap-3">
        <Input v-model="newTitle" :placeholder="t('knowledge.modal.docTitle')" />
        <Select v-model="newType">
          <SelectTrigger class="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="note">note</SelectItem>
            <SelectItem value="markdown">markdown</SelectItem>
            <SelectItem value="text">text</SelectItem>
          </SelectContent>
        </Select>
        <Textarea v-model="newBody" class="min-h-[120px]" :placeholder="t('knowledge.modal.docBody')" />
      </div>
      <div class="mt-4 flex justify-end gap-2">
        <Button variant="outline" size="sm" @click="showNew = false">{{ t('knowledge.modal.cancel') }}</Button>
        <Button size="sm" @click="saveNote">{{ t('knowledge.modal.save') }}</Button>
      </div>
    </AppModal>

    <AppModal :open="showNewEntity" :title="t('knowledge.modal.newEntityTitle')" :subtitle="t('knowledge.modal.newEntitySub')" @close="showNewEntity = false">
      <div class="grid gap-3">
        <Input v-model="entName" :placeholder="t('knowledge.modal.entityName')" />
        <Select v-model="entType">
          <SelectTrigger class="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem v-for="t in ENTITY_TYPES" :key="t.id" :value="t.id">{{ entityTypeLabel(t.id) }}</SelectItem>
          </SelectContent>
        </Select>
        <Textarea v-model="entDesc" class="min-h-[80px]" :placeholder="t('knowledge.modal.entityDesc')" />
      </div>
      <div class="mt-4 flex justify-end gap-2">
        <Button variant="outline" size="sm" @click="showNewEntity = false">{{ t('knowledge.modal.cancel') }}</Button>
        <Button size="sm" @click="saveEntity">{{ t('knowledge.modal.create') }}</Button>
      </div>
    </AppModal>

    <AppModal :open="showNewEvent" :title="t('knowledge.modal.newEventTitle')" :subtitle="t('knowledge.modal.newEventSub')" @close="showNewEvent = false">
      <div class="grid gap-3">
        <Select v-model="evType">
          <SelectTrigger class="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem v-for="t in ['meeting','release','update','decision','announcement','evaluation','experiment','incident','other']" :key="t" :value="t">{{ t }}</SelectItem>
          </SelectContent>
        </Select>
        <Input v-model="evDesc" :placeholder="t('knowledge.modal.eventDesc')" />
        <Input v-model="evDate" type="date" />
      </div>
      <div class="mt-4 flex justify-end gap-2">
        <Button variant="outline" size="sm" @click="showNewEvent = false">{{ t('knowledge.modal.cancel') }}</Button>
        <Button size="sm" @click="saveEvent">{{ t('knowledge.modal.create') }}</Button>
      </div>
    </AppModal>
  </div>
</template>

<style scoped>
/* 知识层：搜索结果的第二层（原文）是它的依据，所以两张卡之间留出呼吸感 */
.khits{display:grid;gap:10px}
/* v3 页头 */
.khead{display:flex;align-items:flex-end;justify-content:space-between;gap:20px}
.ktitle{font-size:26px;letter-spacing:-.03em;margin:8px 0 6px;font-weight:690}
.ksub{margin:0;color:var(--sub);font-size:12px;line-height:1.7}
/* 全页唯一的搜索框 */
.ksearch{position:relative;display:flex;margin:22px 0 14px}
.ksearch input{width:100%;height:44px;border:1px solid var(--line);background:#fff;border-radius:10px;padding:0 15px;font-size:13px;color:var(--text);outline:none;box-shadow:0 2px 10px rgba(36,31,25,.02)}
.ksearch input:focus{border-color:#c9a08e}
.ksearch .tag{position:absolute;right:12px;top:50%;transform:translateY(-50%)}
/* 过滤行 */
.filter-tabs{display:flex;gap:4px;align-items:center;margin:0 0 20px;flex-wrap:wrap}
.filter-tabs button.ftab{border:0;background:transparent;color:var(--muted);padding:7px 10px;border-radius:7px;font-size:11px;cursor:pointer}
.filter-tabs button.ftab.active{background:#ebe8e2;color:var(--text);font-weight:650}
/* 文档分组 */
.docgroup{margin-bottom:22px}
.dghead{display:flex;align-items:center;justify-content:space-between;margin-bottom:9px}
.dghead b{font-size:12px}
.dghead .faint{font-size:10px}
.doclist{background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden}
.docrow{display:grid;grid-template-columns:38px minmax(0,1fr) auto;gap:13px;align-items:center;padding:15px 18px;border-bottom:1px solid var(--hair);cursor:pointer;transition:.15s}
.docrow:last-child{border-bottom:0}
.docrow:hover{background:#fdfcf9}
.docicon{width:32px;height:32px;border:1px solid var(--line);background:var(--surface2);border-radius:8px;display:grid;place-items:center;color:var(--sub);font-size:9px;font-weight:750}
.docicon.pdf{color:var(--accent)}
.docicon.url{color:var(--mint)}
.docicon.md{color:#6b6670}
.docmain{min-width:0}
.docname{font-size:13px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}
.docmeta{font-size:10px;color:var(--muted);margin-top:5px}
.docattn{color:var(--accent)}
.docright{display:flex;align-items:center;gap:13px}
.doctime{font-size:10px;color:var(--muted);white-space:nowrap}
/* 摘要条 */
.ksummary{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-top:18px;padding:12px 14px;border:1px solid var(--hair);background:var(--surface2);border-radius:10px;color:var(--sub);font-size:10px}
.ksitems{display:flex;gap:18px;flex-wrap:wrap}
.ksi{display:inline-flex;gap:6px;align-items:center}
.ksdot{width:6px;height:6px;border-radius:50%;background:var(--muted)}
.ksdot.attn{background:var(--accent)}
.kslink{border:0;background:none;color:var(--sub);font-size:10px;cursor:pointer}
.kslink:hover{color:var(--accent)}
/* Chunk that a question/answer pointed at via ?doc=&chunk= */
.chunk.hl{border-color:#d9c4b8;background:var(--tint-blue);box-shadow:0 0 0 3px rgba(156,90,67,.10)}
/* 抽屉里的知识卡：竖排、留白收紧，一屏能扫过多条 */
.dkl{display:grid;gap:8px}
/* 导入管道相关的样式随组件一起搬到了 ImportPanel.vue（一份实现，一份样式） */
</style>
