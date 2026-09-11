<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import cytoscape from 'cytoscape'
import { api, post, postForm, del } from '../api/client'
import type { DocumentRow, Chunk, Entity, EventRow } from '../api/types'
import SegTabs from '../components/SegTabs.vue'
import StatusTag from '../components/StatusTag.vue'
import MarkdownView from '../components/MarkdownView.vue'
import AppDrawer from '../components/AppDrawer.vue'
import AppModal from '../components/AppModal.vue'
import EmptyState from '../components/EmptyState.vue'
import { useAppStore } from '../stores/app'
import { fmtDateTime } from '../utils/time'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

const tab = ref(route.query.tab === 'graph' ? '图谱' : route.query.tab === 'timeline' ? '时间线' : '文档')
const TABS = ['文档', '图谱', '时间线']
watch(() => route.query.tab, t => {
  if (t === 'graph') tab.value = '图谱'
  else if (t === 'timeline') tab.value = '时间线'
})
watch(tab, t => { if (t === '图谱') setTimeout(drawGraph, 50) })

/* ---------- global search（接回 /api/search） ---------- */
const searchQ = ref((route.query.q as string) || '')
const searching = ref(false)
const searchResults = ref<any[]>([])
let searchTimer: number | undefined

watch(searchQ, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(runSearch, 300)
})

async function runSearch() {
  const q = searchQ.value.trim()
  if (!q) { searchResults.value = []; return }
  searching.value = true
  try {
    searchResults.value = await api<any[]>(`/api/search?q=${encodeURIComponent(q)}&limit=20&semantic=true`)
  } catch (e: any) { store.toast(e.message) } finally { searching.value = false }
}

function openResult(r: any) {
  const doc = docs.value.find(d => d.id === r.document_id)
  if (doc) openSource(r.document_id, r.id)
  else router.push('/knowledge/object/' + r.document_id)
}

/* ---------- documents ---------- */
const docs = ref<DocumentRow[]>([])
const entities = ref<Entity[]>([])
const events = ref<EventRow[]>([])
const entityTotal = ref(0)
const docTotal = ref(0)
const docLimit = ref(50)
const entityOffset = ref(0)

const drawerDoc = ref<DocumentRow | null>(null)
const drawerChunks = ref<Chunk[]>([])
const showNew = ref(false)
const showNewEntity = ref(false)
const newTitle = ref(''); const newBody = ref(''); const newType = ref('note')
const entName = ref(''); const entType = ref('Technology'); const entDesc = ref('')
const evType = ref('meeting'); const evDesc = ref(''); const evDate = ref(''); const showNewEvent = ref(false)

async function loadDocs() {
  const ds = await api<DocumentRow[]>(`/api/documents?limit=${docLimit.value}`)
  docs.value = ds
  if (ds.length) docTotal.value = (ds[0] as any).total ?? ds.length
}
async function loadEntities(append = false) {
  const es = await api<Entity[]>(`/api/entities?limit=60&offset=${append ? entityOffset.value : 0}`)
  entities.value = append ? [...entities.value, ...es] : es
  entityOffset.value = entities.value.length
  if (es.length) entityTotal.value = (es[0] as any).total ?? es.length
}
async function loadEvents() { events.value = await api<EventRow[]>('/api/events?limit=100') }

/* ---------- timeline grouping ---------- */
/** Day bucket: the date extracted from the source text when present, else the creation date. */
function eventDay(e: EventRow): string {
  const raw = (e.time?.start as string | undefined) || e.created_at || ''
  return raw.slice(0, 10) || '未知日期'
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
  await loadAll()
  if (searchQ.value) runSearch()
  if (tab.value === '图谱') setTimeout(drawGraph, 50)
  if (route.query.doc) await openSource(route.query.doc as string, route.query.chunk as string | undefined)
})
watch(() => route.query.doc, d => { if (d) openSource(d as string, route.query.chunk as string | undefined) })

async function openDoc(d: DocumentRow) {
  drawerDoc.value = await api<DocumentRow>('/api/documents/' + d.id)
  drawerChunks.value = await api<Chunk[]>('/api/documents/' + d.id + '/chunks')
}

/* ---------- source jump (?doc=&chunk=) ---------- */
const chunksBox = ref<HTMLDetailsElement | null>(null)
const highlightChunk = ref<string | null>(null)

/** Open a document drawer and, when given, reveal + highlight the originating chunk. */
async function openSource(docId: string, chunkId?: string) {
  tab.value = '文档'
  highlightChunk.value = chunkId || null
  try {
    await openDoc({ id: docId } as DocumentRow)
  } catch (e: any) { store.toast('打开来源失败：' + e.message); return }
  await nextTick()
  if (chunksBox.value) chunksBox.value.open = true
  await nextTick()
  document.querySelector('.chunk.hl')?.scrollIntoView({ block: 'center' })
}

/* ---------- extraction progress + cancel ---------- */
const activeRun = ref<{ id: string; status: string; steps: number; startedAt: number } | null>(null)
let pollTimer: number | undefined
const elapsed = ref(0)
let tickTimer: number | undefined

function stopPolling() {
  window.clearInterval(pollTimer); window.clearInterval(tickTimer)
  pollTimer = undefined; tickTimer = undefined
}

async function watchRun(documentId: string) {
  stopPolling()
  activeRun.value = null
  const t0 = Date.now()
  pollTimer = window.setInterval(async () => {
    try {
      const runs = await api<any[]>('/api/runs?task_type=extract&limit=5')
      const run = runs.find(r => r.document_id === documentId)
      if (run) {
        activeRun.value = { id: run.id, status: run.status, steps: run.step_count, startedAt: t0 }
        if (run.status !== 'started') {
          stopPolling()
          store.toast(`抽取${run.status === 'success' ? '完成' : '结束'}：${run.step_count} steps · ${Math.round((run.duration_ms || 0) / 1000)}s`)
          await loadAll()
          if (drawerDoc.value?.id === documentId) await openDoc(drawerDoc.value)
        }
      }
    } catch { /* keep polling */ }
  }, 2500)
  tickTimer = window.setInterval(() => { elapsed.value = Math.round((Date.now() - t0) / 1000) }, 1000)
}
onBeforeUnmount(stopPolling)

async function actDoc(d: DocumentRow, action: 'index' | 'local' | 'embed' | 'delete') {
  try {
    if (action === 'delete') {
      if (!confirm('删除该文档及其全部派生数据？')) return
      await del('/api/documents/' + d.id); drawerDoc.value = null; await loadAll(); return
    }
    const suffix = action === 'index' ? '/index' : action === 'local' ? '/index/local' : '/embed'
    if (action === 'index') { elapsed.value = 0; watchRun(d.id); store.toast('已触发抽取，可在此查看进度') }
    const r = await post('/api/documents/' + d.id + suffix)
    if (action !== 'index') store.toast('完成：' + JSON.stringify(r).slice(0, 60))
    await loadAll()
  } catch (e: any) { store.toast(e.message); stopPolling() }
}

async function cancelRun() {
  if (!activeRun.value) return
  try { await post(`/api/runs/${activeRun.value.id}/cancel`); store.toast('已请求取消（将在当前批次结束后生效）') } catch (e: any) { store.toast(e.message) }
}

async function backfillEmbeddings() {
  embedding.value = true
  try {
    const r = await post<any>('/api/embeddings/backfill')
    store.toast(`已嵌入 ${r.embedded_documents} 篇文档 · ${r.chunks} chunks · ${r.model}`)
    await loadAll()
  } catch (e: any) { store.toast(e.message) } finally { embedding.value = false }
}
const embedding = ref(false)

async function saveNote() {
  try {
    await post('/api/documents', { title: newTitle.value, content: newBody.value, source_type: newType.value })
    showNew.value = false; newTitle.value = ''; newBody.value = ''
    store.toast('已创建'); await loadAll()
  } catch (e: any) { store.toast(e.message) }
}
async function saveEntity() {
  try {
    await post('/api/entities', { name: entName.value, type: entType.value, description: entDesc.value || null })
    showNewEntity.value = false; entName.value = ''; entDesc.value = ''
    store.toast('实体已创建（candidate）'); await loadEntities()
  } catch (e: any) { store.toast(e.message) }
}
async function saveEvent() {
  try {
    await post('/api/events', { event_type: evType.value, description: evDesc.value, time: evDate.value ? { start: evDate.value, precision: 'day' } : {} })
    showNewEvent.value = false; evDesc.value = ''; evDate.value = ''
    store.toast('事件已创建'); await loadEvents()
  } catch (e: any) { store.toast(e.message) }
}
async function importFile(ev: Event) {
  const f = (ev.target as HTMLInputElement).files?.[0]; if (!f) return
  const fd = new FormData(); fd.append('file', f)
  try { await postForm('/api/documents/import', fd); store.toast('已导入'); await loadAll() } catch (e: any) { store.toast(e.message) }
}

/* ---------- graph ---------- */
const graphBox = ref<HTMLElement | null>(null)
let cy: cytoscape.Core | null = null
const TYPE_COLORS: Record<string, string> = {
  Technology: '#5b7cff', Concept: '#8a69e9', Dataset: '#45c49a', Software: '#62c6e4',
  Method: '#8b67f7', Model: '#f1a456', Person: '#ef7f9c', Organization: '#5ac7e8',
}
async function drawGraph() {
  if (!graphBox.value) return
  const g = await api<any>('/api/graph?limit=120')
  const elements = [
    ...g.nodes.map((n: any) => ({ data: { id: n.id, label: n.name, color: TYPE_COLORS[n.type] || '#f1a456', verified: n.status === 'verified' } })),
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
</script>

<template>
  <div class="page">
    <div class="askbox" style="margin-bottom:16px">
      <input v-model="searchQ" placeholder="全库搜索：正文 / 实体 / 文档（FTS5 + 语义 + RRF）…" />
      <span v-if="searching" class="tag blue" style="margin-right:6px">搜索中…</span>
      <button class="go" title="转为提问" @click="router.push({ path: '/qa', query: { q: searchQ } })">◎</button>
    </div>

    <!-- search results -->
    <div v-if="searchQ.trim()" class="panel pad" style="padding:6px;margin-bottom:16px">
      <div class="sechead" style="margin:4px 6px 8px"><h3>搜索结果</h3><span class="faint" style="font-size:9px">{{ searchResults.length }} 条 · 点击查看来源文档</span></div>
      <div v-for="(r, i) in searchResults" :key="i" class="item" @click="openResult(r)">
        <div class="ico-badge ib-blue">⌕</div>
        <div class="grow">
          <b>{{ r.title }}</b>
          <p>{{ (r.content || '').slice(0, 140) }}</p>
          <div style="margin-top:4px">
            <span class="tag">{{ r.method }}</span>
            <span class="tag">score {{ Number(r.score || 0).toFixed(4) }}</span>
            <span class="tag mono">chunk {{ (r.id || '').slice(0, 8) }}</span>
          </div>
        </div>
      </div>
      <EmptyState v-if="!searchResults.length && !searching" text="没有匹配结果——试试更短的关键词，或先为文档生成嵌入" />
    </div>

    <SegTabs v-model="tab" :options="TABS" style="margin:0 0 14px" />

    <!-- 文档 -->
    <div v-if="tab === '文档'">
      <div class="sechead"><h3>Documents <span class="faint" style="font-weight:400;font-size:9px">· {{ docs.length }}/{{ docTotal }}</span></h3>
        <span class="row" style="gap:8px">
          <button class="btn" :disabled="embedding" @click="backfillEmbeddings">{{ embedding ? '嵌入中…' : '⚡ 生成全部嵌入' }}</button>
          <label class="btn">＋ 导入<input type="file" accept=".md,.markdown,.txt,.html,.htm" style="display:none" @change="importFile" /></label>
          <button class="btn primary" @click="showNew = true">＋ 新建文档</button>
        </span>
      </div>
      <div class="panel pad" style="padding:6px">
        <div v-for="d in docs" :key="d.id" class="item" @click="openDoc(d)">
          <div class="ico-badge ib-blue">▤</div>
          <div class="grow"><b>{{ d.title }}</b><p>{{ d.source_type }} · {{ d.chunk_count }} chunks · {{ fmtDateTime(d.updated_at) }}</p></div>
          <StatusTag v-if="d.last_run_status" :status="d.last_run_status" /><span v-else class="tag amber">未索引</span>
        </div>
        <EmptyState v-if="!docs.length" text="还没有文档——导入或新建一篇" />
      </div>
      <div v-if="!showAllDocs" style="text-align:center;margin-top:10px">
        <button class="btn" @click="docLimit += 50; loadDocs()">加载更多（{{ docTotal - docs.length }} 条）</button>
      </div>

      <div class="sechead"><h3>Knowledge Objects <span class="faint" style="font-weight:400;font-size:9px">· {{ entities.length }}/{{ entityTotal }}</span></h3>
        <button class="btn" @click="showNewEntity = true">＋ 新建实体</button>
      </div>
      <div class="grid g3">
        <div v-for="e in entities" :key="e.id" class="panel pad item" style="display:block" @click="router.push('/knowledge/object/' + e.id)">
          <div class="row"><div class="ico-badge" :class="e.status === 'verified' ? 'ib-mint' : 'ib-blue'">✦</div>
            <div><b>{{ e.name }}</b><div class="faint" style="font-size:8.5px">{{ e.type }}</div></div></div>
          <hr class="hairline" />
          <p style="margin:0">{{ e.description?.slice(0, 60) || '—' }}</p>
          <div style="margin-top:9px"><StatusTag :status="e.status" /></div>
        </div>
      </div>
      <div v-if="entities.length < entityTotal" style="text-align:center;margin-top:10px">
        <button class="btn" @click="loadEntities(true)">加载更多（{{ entityTotal - entities.length }} 个）</button>
      </div>
    </div>

    <!-- 图谱 -->
    <div v-show="tab === '图谱'">
      <div class="row" style="margin-bottom:12px">
        <span class="faint" style="font-size:9px">点击节点进入对象详情 · 深色描边为已验证实体 · 拖拽/滚轮缩放</span>
        <div class="grow"></div>
        <button class="btn sm" @click="drawGraph">重新布局</button>
      </div>
      <div ref="graphBox" class="gcanvas"></div>
    </div>

    <!-- 时间线 -->
    <div v-if="tab === '时间线'">
      <div class="sechead"><h3>时间线</h3><button class="btn" @click="showNewEvent = true">＋ 新建事件</button></div>
      <div class="panel pad">
        <div class="timeline">
          <template v-for="g in groupedEvents" :key="g.date">
            <div class="tlday">{{ g.date }}</div>
            <div v-for="e in g.items" :key="e.id" class="tle">
              <b>{{ e.description }}</b>
              <div>
                <StatusTag :status="e.status" />
                <span class="tag">{{ e.event_type }}</span>
                <span v-if="e.location" class="tag">📍 {{ e.location }}</span>
                <span v-if="eventClock(e)" class="when">{{ eventClock(e) }}</span>
              </div>
            </div>
          </template>
          <EmptyState v-if="!events.length" text="还没有事件" />
        </div>
      </div>
    </div>

    <AppDrawer :open="!!drawerDoc" :title="drawerDoc?.title || ''" @close="drawerDoc = null">
      <template v-if="drawerDoc">
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px">
          <StatusTag v-if="drawerDoc.last_run_status" :status="drawerDoc.last_run_status" />
          <span class="tag blue">{{ drawerDoc.source_type }}</span>
          <span class="tag">{{ drawerDoc.chunk_count }} chunks</span>
        </div>
        <div class="row" style="flex-wrap:wrap;gap:8px;margin-bottom:12px">
          <button class="btn primary" @click="actDoc(drawerDoc, 'index')">Index with LLM</button>
          <button class="btn" @click="actDoc(drawerDoc, 'local')">Chunks only</button>
          <button class="btn" @click="actDoc(drawerDoc, 'embed')">Embeddings</button>
          <button class="btn danger" @click="actDoc(drawerDoc, 'delete')">删除</button>
        </div>
        <div v-if="activeRun" class="notice violet" style="margin-bottom:12px">
          <div class="row"><span>抽取中 · {{ activeRun.status }} · {{ activeRun.steps }} steps · {{ elapsed }}s</span>
            <div class="grow"></div>
            <button class="btn sm" @click="cancelRun">取消</button>
          </div>
        </div>
        <details ref="chunksBox" style="margin:10px 0">
          <summary style="cursor:pointer;font-size:10px;color:var(--sub)">Chunks ({{ drawerChunks.length }})</summary>
          <div v-for="c in drawerChunks" :key="c.id" class="listitem chunk" :class="{ hl: c.id === highlightChunk }" style="margin-top:6px">
            <b>#{{ c.chunk_index }}</b><span v-if="c.id === highlightChunk" class="tag blue" style="margin-left:6px">来源片段</span><p>{{ c.content.slice(0, 160) }}…</p>
          </div>
        </details>
        <MarkdownView :content="drawerDoc.content || ''" />
      </template>
    </AppDrawer>

    <AppModal :open="showNew" title="新建文档" subtitle="记录一条 Markdown 笔记，或导入文件作为来源。" @close="showNew = false">
      <input v-model="newTitle" class="field" style="width:100%" placeholder="标题" />
      <select v-model="newType" class="field" style="width:100%;margin-top:9px"><option>note</option><option>markdown</option><option>text</option></select>
      <textarea v-model="newBody" class="field" style="width:100%;height:120px;margin-top:9px" placeholder="内容（支持 Markdown）…"></textarea>
      <div style="display:flex;justify-content:flex-end;gap:7px;margin-top:12px">
        <button class="btn" @click="showNew = false">取消</button>
        <button class="btn primary" @click="saveNote">保存</button>
      </div>
    </AppModal>

    <AppModal :open="showNewEntity" title="新建实体" subtitle="手工创建的实体默认为 candidate，可在研究 → 审核中确认。" @close="showNewEntity = false">
      <input v-model="entName" class="field" style="width:100%" placeholder="名称（如 Retrieval-Augmented Generation）" />
      <select v-model="entType" class="field" style="width:100%;margin-top:9px">
        <option v-for="t in ['Person','Organization','Product','Software','Technology','Method','Concept','Theory','Dataset','Model','Standard','Protocol','Resource','Location']" :key="t">{{ t }}</option>
      </select>
      <textarea v-model="entDesc" class="field" style="width:100%;height:80px;margin-top:9px" placeholder="描述…"></textarea>
      <div style="display:flex;justify-content:flex-end;gap:7px;margin-top:12px">
        <button class="btn" @click="showNewEntity = false">取消</button>
        <button class="btn primary" @click="saveEntity">创建</button>
      </div>
    </AppModal>

    <AppModal :open="showNewEvent" title="新建事件" subtitle="手工记录一条事件（可留空日期）。" @close="showNewEvent = false">
      <select v-model="evType" class="field" style="width:100%">
        <option v-for="t in ['meeting','release','update','decision','announcement','evaluation','experiment','incident','other']" :key="t">{{ t }}</option>
      </select>
      <input v-model="evDesc" class="field" style="width:100%;margin-top:9px" placeholder="事件描述" />
      <input v-model="evDate" type="date" class="field" style="width:100%;margin-top:9px" />
      <div style="display:flex;justify-content:flex-end;gap:7px;margin-top:12px">
        <button class="btn" @click="showNewEvent = false">取消</button>
        <button class="btn primary" @click="saveEvent">创建</button>
      </div>
    </AppModal>
  </div>
</template>

<style scoped>
/* Chunk that a question/answer pointed at via ?doc=&chunk= */
.chunk.hl{border-color:#bcd0ff;background:var(--tint-blue);box-shadow:0 0 0 3px rgba(91,124,255,.12)}
</style>
