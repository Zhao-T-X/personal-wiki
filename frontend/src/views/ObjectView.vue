<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import cytoscape from 'cytoscape'
import { ArrowRight, CircleHelp, Sparkles } from 'lucide-vue-next'
import { api, patchJson, post } from '../api/client'
import type { Claim, Entity, EventRow, IdeaRow, QuestionRow } from '../api/types'
import PageHead from '../components/PageHead.vue'
import StatusTag from '../components/StatusTag.vue'
import TypeDecision from '../components/TypeDecision.vue'
import EmptyState from '../components/EmptyState.vue'
import AppModal from '../components/AppModal.vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import KnowledgeCard from '../components/KnowledgeCard.vue'
import { useAppStore } from '../stores/app'
import { fmtDateTime } from '../utils/time'
import { toCard, labelOf } from '../utils/claim'
import { statusLabel } from '../utils/status'
import { ENTITY_TYPES, entityTypeLabel } from '../utils/entityType'

const { t } = useI18n()
import { TYPE_COLORS, DEFAULT_NODE_COLOR, edgeEndpoints } from '../utils/graph'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

const showEdit = ref(false)
const editName = ref(''); const editType = ref('Concept'); const editAliases = ref(''); const editDesc = ref('')
const showIdea = ref(false); const ideaText = ref('')
const showQuestion = ref(false); const questionText = ref('')

function openEdit() {
  if (!entity.value) return
  editName.value = entity.value.name
  editType.value = entity.value.type
  editAliases.value = (entity.value.aliases || []).join(', ')
  editDesc.value = entity.value.description || ''
  showEdit.value = true
}

async function saveEntity() {
  if (!entity.value) return
  try {
    await patchJson('/api/entities/' + entity.value.id, {
      name: editName.value.trim(),
      type: editType.value,
      aliases: editAliases.value.split(',').map(a => a.trim()).filter(Boolean),
      description: editDesc.value,
      properties: entity.value.properties || {},
    })
    showEdit.value = false
    store.toast(t('object.entityUpdated'))
    await load()
  } catch (e: any) { store.toast(e.message) }
}

async function addIdea() {
  if (!ideaText.value.trim()) return
  try {
    await post('/api/ideas', { content: ideaText.value.trim(), source_document_id: evidence.value[0]?.source_document_id || null })
    showIdea.value = false; ideaText.value = ''
    store.toast(t('object.ideaCreated')); await load()
  } catch (e: any) { store.toast(e.message) }
}

async function addQuestion() {
  if (!questionText.value.trim()) return
  try {
    await post('/api/questions', { content: questionText.value.trim(), source_document_id: evidence.value[0]?.source_document_id || null })
    showQuestion.value = false; questionText.value = ''
    store.toast(t('object.questionCreated')); await load()
  } catch (e: any) { store.toast(e.message) }
}

/** 普通界面用语：分页标签与状态一律用产品语言（§11/§32），技术细节收进开发者模式。
 *  id 是稳定键，label 走 i18n。状态词统一走 utils/status（原来这里还有一套
 *  私有词汇表——同一状态两种说法，正是它要防的事）。 */
const TABS = computed(() => [
  { id: 'Overview', label: t('object.tabs.overview') },
  { id: 'Claims', label: t('object.tabs.claims') },
  { id: 'Relations', label: t('object.tabs.relations') },
  { id: 'Graph', label: t('object.tabs.graph') },
  { id: 'Events', label: t('object.tabs.events') },
  { id: 'Evidence', label: t('object.tabs.evidence') },
  { id: 'Questions', label: t('object.tabs.questions') },
  { id: 'Ideas', label: t('object.tabs.ideas') },
])
const tab = ref('Overview')
const entity = ref<Entity | null>(null)
const counts = ref<Record<string, number>>({})
const claims = ref<Claim[]>([])
const relations = ref<any[]>([])
const evidence = ref<Claim[]>([])
const events = ref<EventRow[]>([])
const ideas = ref<IdeaRow[]>([])
const questions = ref<QuestionRow[]>([])
const typeDecision = ref<{ type: string; reason: string; ok: boolean }[]>([])

const loadError = ref('')
/** Evidence shows document titles, not ids — resolve the distinct ones once per load. */
const docLabels = ref<Record<string, string>>({})

async function load() {
  const id = route.params.id as string
  loadError.value = ''
  try {
    const d = await api<any>('/api/entities/' + id + '/object')
    entity.value = d.entity; counts.value = d.counts
    claims.value = d.claims; relations.value = d.relations; evidence.value = d.evidence
    events.value = d.events; ideas.value = d.ideas; questions.value = d.questions
    typeDecision.value = d.type_decision
    await loadDocLabels()
    if (tab.value === 'Graph') drawLocalGraph()
  } catch (e: any) { entity.value = null; loadError.value = e.message }
}

async function loadDocLabels() {
  const ids = [...new Set(evidence.value.map((e: any) => e.source_document_id).filter(Boolean))] as string[]
  const pairs = await Promise.all(ids.map(async id => {
    try { return [id, (await api<{ title: string }>('/api/documents/' + id)).title] as const }
    catch { return [id, ''] as const }
  }))
  docLabels.value = Object.fromEntries(pairs.filter(([, title]) => title))
}

/** Evidence must lead back to the raw source, with the originating chunk highlighted. */
function openEvidenceSource(c: any) {
  if (!c.source_document_id) return
  router.push({
    path: '/knowledge',
    query: { doc: c.source_document_id, ...(c.source_chunk_id ? { chunk: c.source_chunk_id } : {}) },
  })
}
onMounted(load)
watch(() => route.params.id, () => { if (route.path.startsWith('/knowledge/object/')) load() })

function askAbout() {
  if (!entity.value) return
  router.push({ path: '/qa', query: { q: t('object.askQuery', { name: entity.value.name }) } })
}
/* ---------- local graph（从当前对象出发的一跳关系） ---------- */
const graphBox = ref<HTMLElement | null>(null)
let cy: cytoscape.Core | null = null
const graphLoading = ref(false)
const graphEmpty = ref(false)
interface EdgeDetail {
  predicate: string; source: string; target: string
  confidence?: number | null; status?: string
  documentId?: string | null; chunkId?: string | null
}
const selectedEdge = ref<EdgeDetail | null>(null)

/**
 * Draw only the neighbourhood around this object instead of the whole graph —
 * a 120-node force layout answers no question the user actually has.
 */
async function drawLocalGraph() {
  if (!entity.value) return
  graphLoading.value = true
  selectedEdge.value = null
  await nextTick()
  if (!graphBox.value) { graphLoading.value = false; return }
  try {
    const g = await api<any>(`/api/entities/${entity.value.id}/graph?depth=1&limit=100`)
    const nodes: any[] = g.nodes || []
    const nodeIds = new Set(nodes.map(n => n.id))
    // Claims whose object is free text have no node to attach to; drop those edges.
    const edges = (g.edges || []).filter((e: any) => {
      const { source, target } = edgeEndpoints(e)
      return source && target && nodeIds.has(source) && nodeIds.has(target)
    })
    const rootId = entity.value.id
    const elements = [
      ...nodes.map(n => ({
        data: {
          id: n.id, label: n.name,
          color: TYPE_COLORS[n.type] || DEFAULT_NODE_COLOR,
          verified: n.status === 'verified',
          root: n.id === rootId,
        },
      })),
      ...edges.map((e: any) => {
        const { source, target } = edgeEndpoints(e)
        return { data: { id: e.id, source, target, label: labelOf(e.predicate) } }
      }),
    ]
    cy?.destroy()
    cy = cytoscape({
      container: graphBox.value, elements,
      layout: { name: 'cose', animate: false, padding: 40 },
      style: [
        { selector: 'node', style: { 'background-color': 'data(color)', label: 'data(label)', color: '#fff', 'font-size': 9, width: 30, height: 30, 'text-valign': 'center', 'text-halign': 'center', opacity: 0.75 } },
        { selector: 'node[?verified]', style: { opacity: 1 } },
        { selector: 'node[?root]', style: { opacity: 1, width: 46, height: 46, 'border-width': 3, 'border-color': '#16203a', 'font-size': 11 } },
        { selector: 'edge', style: { width: 1.5, 'line-color': '#c2cde3', 'target-arrow-color': '#c2cde3', 'target-arrow-shape': 'triangle', label: 'data(label)', 'font-size': 7, color: '#8595b0', 'curve-style': 'bezier' } },
      ],
    })
    cy.on('tap', 'node', evt => {
      const id = evt.target.id()
      if (id !== rootId) router.push('/knowledge/object/' + id)
    })
    cy.on('tap', 'edge', evt => {
      const edge = edges.find((e: any) => e.id === evt.target.id())
      if (!edge) return
      selectedEdge.value = {
        predicate: edge.predicate,
        source: edge.source_name || edge.subject_name || '—',
        target: edge.target_name || edge.object_name || edge.object_text || '—',
        confidence: edge.confidence,
        status: edge.status,
        documentId: edge.source_document_id,
        chunkId: edge.source_chunk_id,
      }
    })
    cy.fit(undefined, 40)
    graphEmpty.value = nodes.length <= 1
  } catch (e: any) { store.toast(e.message) } finally { graphLoading.value = false }
}

/** A relation is only trustworthy if it leads back to the text that produced it. */
function openEdgeSource() {
  const e = selectedEdge.value
  if (!e?.documentId) return
  router.push({
    path: '/knowledge',
    query: { doc: e.documentId, ...(e.chunkId ? { chunk: e.chunkId } : {}) },
  })
}

watch(tab, t => { if (t === 'Graph') drawLocalGraph() })
</script>

<template>
  <div class="page" v-if="entity">
    <PageHead :title="entity.name" :subtitle="undefined">
      <template #actions>
        <button class="btn" @click="openEdit">{{ t('object.edit') }}</button>
        <button class="btn" @click="router.push('/review')">{{ t('object.review') }}</button>
        <button class="btn" @click="router.push('/research')">{{ t('object.research') }}</button>
        <button class="btn primary" @click="askAbout">{{ t('object.ask') }}</button>
      </template>
    </PageHead>
    <div class="row" style="gap:8px;flex-wrap:wrap;margin:-12px 0 16px">
      <span class="tag blue">{{ entityTypeLabel(entity.type) }}</span>
      <StatusTag :status="entity.status" />
      <span v-for="a in entity.aliases" :key="a" class="tag">{{ a }}</span>
    </div>

    <div class="panel pad">
      <div class="row" style="gap:26px;flex-wrap:wrap;font-size:10.5px">
        <div v-for="(v, k) in counts" :key="k">
          <span class="faint" style="font-size:8px;display:block;letter-spacing:.1em">{{ k.toUpperCase() }}</span>
          <b style="font-size:17px">{{ v }}</b>
        </div>
      </div>
    </div>

    <div class="seg" style="margin:16px 0 14px">
      <button v-for="t in TABS" :key="t.id"
              :class="{ active: tab === t.id }" @click="tab = t.id">{{ t.label }}</button>
    </div>

    <!-- Overview -->
    <div v-if="tab === 'Overview'" class="grid g2">
      <div>
        <div class="sechead"><h3>{{ t('object.whatIsThis') }}</h3></div>
        <div class="panel pad" style="font-size:10.5px;line-height:1.8;color:#3c4b66">{{ entity.description || t('object.noDesc') }}</div>
        <div class="sechead"><h3>{{ t('object.typeDecision') }} <span class="faint" style="font-size:9px;font-weight:400">{{ t('object.typeDecisionSub') }}</span></h3></div>
        <div class="panel" style="padding:6px"><TypeDecision :decisions="typeDecision" /></div>
      </div>
      <div>
        <div class="sechead"><h3>{{ t('object.coreKnowledge') }}</h3><span class="more" @click="tab = 'Claims'">{{ t('object.allN', { n: counts.claims }) }}</span></div>
        <!-- 知识在这里也以卡片出现：同一事实在详情页、搜索、问答、研究里是同一个形状，
             带着同样的状态用词和同样的 [依据][历史][纠正]。之前是原始数据行，
             还把谓词标识符直接打在了界面上。 -->
        <div class="kgrid">
          <KnowledgeCard v-for="c in claims.slice(0, 6)" :key="c.id"
                         :claim="toCard(c)" compact @changed="load" />
        </div>
        <EmptyState v-if="!claims.length" :text="t('object.noClaims')" />
        <div class="sechead"><h3>{{ t('object.tabs.relations') }}</h3><span class="more" @click="tab = 'Relations'">{{ t('object.all') }}</span></div>
        <div class="panel pad" style="padding:6px">
          <div v-for="r in relations.slice(0, 5)" :key="r.id" class="item" style="cursor:default">
            <div class="ico-badge ib-blue"><ArrowRight :size="14" /></div>
            <div class="grow"><b>{{ r.source_name }} → {{ labelOf(r.predicate) }} → {{ r.target_name }}</b><p>{{ statusLabel(r.status) }}</p></div>
          </div>
          <EmptyState v-if="!relations.length" :text="t('object.noRelations')" />
        </div>
      </div>
    </div>

    <!-- Claims -->
    <div v-if="tab === 'Claims'">
      <div class="kgrid">
        <KnowledgeCard v-for="c in claims" :key="c.id"
                       :claim="toCard(c)" @changed="load" />
      </div>
      <EmptyState v-if="!claims.length" :text="t('object.noClaims')" />
    </div>

    <!-- Relations -->
    <div v-if="tab === 'Relations'">
      <div class="panel pad" style="padding:6px">
        <div v-for="r in relations" :key="r.id" class="item" style="cursor:default">
          <div class="ico-badge ib-blue"><ArrowRight :size="14" /></div>
          <div class="grow"><b>{{ r.source_name }} → {{ labelOf(r.predicate) }} → {{ r.target_name }}</b><p>{{ r.confidence != null ? t('object.confidence', { n: Math.round(r.confidence * 100) + '%' }) : '—' }}</p></div>
          <StatusTag :status="r.status" />
        </div>
        <EmptyState v-if="!relations.length" :text="t('object.noRelations')" />
      </div>
      <div class="notice violet" style="margin-top:12px">{{ t('object.relationsNote') }}</div>
    </div>

    <!-- Graph：局部一跳关系（不是全库力导向图） -->
    <div v-if="tab === 'Graph'">
      <div class="row" style="margin-bottom:12px">
        <span class="faint" style="font-size:9px">{{ t('object.graphHint', { name: entity.name }) }}</span>
        <div class="grow"></div>
        <button class="btn sm" @click="drawLocalGraph">{{ t('knowledge.relayout') }}</button>
      </div>
      <div v-if="graphLoading" class="empty" style="margin-bottom:12px">{{ t('object.buildingGraph') }}</div>
      <div ref="graphBox" class="gcanvas"></div>
      <div v-if="selectedEdge" class="panel pad" style="margin-top:12px">
        <div style="font-size:12.5px">
          <b>{{ selectedEdge.source }}</b>
          <span class="tag blue" style="margin:0 6px">{{ labelOf(selectedEdge.predicate) }}</span>
          <b>{{ selectedEdge.target }}</b>
        </div>
        <div class="row" style="margin-top:10px;gap:8px;flex-wrap:wrap">
          <span v-if="selectedEdge.confidence != null" class="tag">{{ t('object.confidence', { n: Math.round(selectedEdge.confidence * 100) + '%' }) }}</span>
          <StatusTag v-if="selectedEdge.status" :status="selectedEdge.status" />
          <button v-if="selectedEdge.documentId" class="btn sm" @click="openEdgeSource">{{ t('object.openSource') }}</button>
          <button class="btn sm ghost" @click="selectedEdge = null">{{ t('object.close') }}</button>
        </div>
      </div>
      <EmptyState
        v-else-if="graphEmpty && !graphLoading"
        :title="t('object.noRelationsTitle')"
        :text="t('object.noRelationsText')"
      />
    </div>

    <!-- Events -->
    <div v-if="tab === 'Events'" class="panel pad">
      <div class="notice" style="margin-bottom:10px;background:var(--surface2)">{{ t('object.eventsNote') }}</div>
      <div class="timeline">
        <div v-for="e in events" :key="e.id" class="tle">
          <b>{{ e.description }}</b>
          <div>
            <StatusTag :status="e.status" />
            <span class="tag">{{ e.event_type }}</span>
            <span class="when">{{ e.time?.start || fmtDateTime(e.created_at) }}</span>
          </div>
        </div>
        <EmptyState v-if="!events.length" :text="t('object.noEvents')" />
      </div>
    </div>

    <!-- Evidence -->
    <div v-if="tab === 'Evidence'">
      <div v-for="c in evidence" :key="c.id" class="evidence" style="margin-bottom:12px">
        “{{ c.source_quote }}”
        <div class="src">
          <span>{{ docLabels[c.source_document_id] || t('object.sourceDoc') }}</span>
          <span v-if="c.source_start_offset != null && store.developerMode">offset [{{ c.source_start_offset }}, {{ c.source_end_offset }})</span>
        </div>
        <button class="btn sm" style="margin-top:9px" @click="openEvidenceSource(c)">{{ t('object.openSource') }}</button>
      </div>
      <EmptyState
        v-if="!evidence.length"
        :title="t('object.noEvidenceTitle')"
        :text="t('object.noEvidenceText')"
      >
        <template #action><button class="btn" @click="router.push('/knowledge')">{{ t('object.goKnowledge') }}</button></template>
      </EmptyState>
    </div>

    <!-- Questions -->
    <div v-if="tab === 'Questions'">
      <div class="sechead" style="margin-top:0"><h3>{{ t('object.relatedQuestions') }}</h3><button class="btn" @click="showQuestion = true">{{ t('object.newQuestion') }}</button></div>
      <div class="panel pad" style="padding:6px">
        <div v-for="q in questions" :key="q.id" class="item" @click="router.push('/research')">
          <div class="ico-badge ib-blue"><CircleHelp :size="14" /></div>
          <div class="grow"><b>{{ q.content }}</b><p>{{ statusLabel(q.status) }}</p></div>
          <StatusTag :status="q.status" />
        </div>
        <EmptyState v-if="!questions.length" :text="t('object.noQuestions')" />
      </div>
    </div>

    <!-- Ideas -->
    <div v-if="tab === 'Ideas'">
      <div class="sechead" style="margin-top:0"><h3>{{ t('object.relatedIdeas') }}</h3><button class="btn" @click="showIdea = true">{{ t('object.newIdea') }}</button></div>
      <div class="panel pad" style="padding:6px">
        <div v-for="i in ideas" :key="i.id" class="item" style="cursor:default">
          <div class="ico-badge ib-violet"><Sparkles :size="14" /></div>
          <div class="grow"><b>{{ i.content }}</b><p>{{ statusLabel(i.status) }}</p></div>
          <StatusTag :status="i.status" />
        </div>
        <EmptyState v-if="!ideas.length" :text="t('object.noIdeas')" />
      </div>
    </div>

    <AppModal :open="showEdit" :title="t('object.modal.editTitle')"
               :subtitle="store.developerMode ? t('object.modal.editSubDev') : t('object.modal.editSub')" @close="showEdit = false">
      <div class="grid gap-3">
        <Input v-model="editName" :placeholder="t('object.modal.name')" />
        <Select v-model="editType">
          <SelectTrigger class="w-full"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem v-for="tt in ENTITY_TYPES" :key="tt.id" :value="tt.id">{{ entityTypeLabel(tt.id) }}</SelectItem>
          </SelectContent>
        </Select>
        <Input v-model="editAliases" :placeholder="t('object.modal.aliases')" />
        <Textarea v-model="editDesc" class="min-h-[80px]" :placeholder="t('object.modal.desc')" />
      </div>
      <div class="mt-4 flex justify-end gap-2">
        <Button variant="outline" size="sm" @click="showEdit = false">{{ t('object.modal.cancel') }}</Button>
        <Button size="sm" @click="saveEntity">{{ t('object.modal.save') }}</Button>
      </div>
    </AppModal>

    <AppModal :open="showIdea" :title="t('object.modal.ideaTitle')" :subtitle="t('object.modal.ideaSub')" @close="showIdea = false">
      <Textarea v-model="ideaText" class="min-h-[100px]" :placeholder="t('object.modal.ideaPlaceholder')" />
      <div class="mt-4 flex justify-end gap-2">
        <Button variant="outline" size="sm" @click="showIdea = false">{{ t('object.modal.cancel') }}</Button>
        <Button size="sm" @click="addIdea">{{ t('object.modal.save') }}</Button>
      </div>
    </AppModal>

    <AppModal :open="showQuestion" :title="t('object.modal.questionTitle')" :subtitle="t('object.modal.questionSub')" @close="showQuestion = false">
      <Textarea v-model="questionText" class="min-h-[90px]" :placeholder="t('object.modal.questionPlaceholder')" />
      <div class="mt-4 flex justify-end gap-2">
        <Button variant="outline" size="sm" @click="showQuestion = false">{{ t('object.modal.cancel') }}</Button>
        <Button size="sm" @click="addQuestion">{{ t('object.modal.create') }}</Button>
      </div>
    </AppModal>
  </div>

  <div class="page" v-else-if="loadError">
    <PageHead :title="t('object.notFound')" :subtitle="loadError">
      <template #actions>
        <button class="btn" @click="router.back()">← {{ t('object.back') }}</button>
      </template>
    </PageHead>
    <EmptyState :text="t('object.notFoundText', { msg: loadError })" />
  </div>
</template>

<style scoped>
/* 知识以卡片出现（与搜索、问答、研究同一个形状），不再以数据行出现 */
.kgrid{display:grid;gap:10px;margin-bottom:14px}
@media (min-width:900px){.kgrid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
