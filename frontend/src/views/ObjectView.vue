<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import cytoscape from 'cytoscape'
import { api, patchJson, post } from '../api/client'
import type { Claim, Entity, EventRow, IdeaRow, QuestionRow } from '../api/types'
import PageHead from '../components/PageHead.vue'
import StatusTag from '../components/StatusTag.vue'
import TypeDecision from '../components/TypeDecision.vue'
import EmptyState from '../components/EmptyState.vue'
import AppModal from '../components/AppModal.vue'
import { useAppStore } from '../stores/app'
import { fmtDateTime } from '../utils/time'
import { TYPE_COLORS, DEFAULT_NODE_COLOR, edgeEndpoints } from '../utils/graph'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

const ENTITY_TYPES = ['Person','Organization','Product','Software','Technology','Method','Concept','Theory','Dataset','Model','Standard','Protocol','Resource','Location']
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
    store.toast('实体已更新')
    await load()
  } catch (e: any) { store.toast(e.message) }
}

async function addIdea() {
  if (!ideaText.value.trim()) return
  try {
    await post('/api/ideas', { content: ideaText.value.trim(), source_document_id: evidence.value[0]?.source_document_id || null })
    showIdea.value = false; ideaText.value = ''
    store.toast('想法已记录（candidate）'); await load()
  } catch (e: any) { store.toast(e.message) }
}

async function addQuestion() {
  if (!questionText.value.trim()) return
  try {
    await post('/api/questions', { content: questionText.value.trim(), source_document_id: evidence.value[0]?.source_document_id || null })
    showQuestion.value = false; questionText.value = ''
    store.toast('问题已创建（open）'); await load()
  } catch (e: any) { store.toast(e.message) }
}

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
  router.push({ path: '/qa', query: { q: `${entity.value.name} 是什么？它和哪些东西有关？` } })
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
        return { data: { id: e.id, source, target, label: e.predicate } }
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

const objLabel = (c: Claim) => c.object_name || c.object_text || '—'
</script>

<template>
  <div class="page" v-if="entity">
    <PageHead :title="entity.name" :subtitle="undefined">
      <template #actions>
        <button class="btn" @click="openEdit">编辑</button>
        <button class="btn" @click="router.push('/review')">审核</button>
        <button class="btn" @click="router.push('/research')">研究</button>
        <button class="btn primary" @click="askAbout">就此提问</button>
      </template>
    </PageHead>
    <div class="row" style="gap:8px;flex-wrap:wrap;margin:-12px 0 16px">
      <span class="tag blue">{{ entity.type }}</span>
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
      <button v-for="t in ['Overview', 'Claims', 'Relations', 'Graph', 'Events', 'Evidence', 'Questions', 'Ideas']" :key="t"
              :class="{ active: tab === t }" @click="tab = t">{{ t }}</button>
    </div>

    <!-- Overview -->
    <div v-if="tab === 'Overview'" class="grid g2">
      <div>
        <div class="sechead"><h3>What is it?</h3></div>
        <div class="panel pad" style="font-size:10.5px;line-height:1.8;color:#3c4b66">{{ entity.description || '暂无描述——点右上角「编辑」补充。' }}</div>
        <div class="sechead"><h3>Type Decision <span class="faint" style="font-size:9px;font-weight:400">· 为什么是这个类型</span></h3></div>
        <div class="panel" style="padding:6px"><TypeDecision :decisions="typeDecision" /></div>
      </div>
      <div>
        <div class="sechead"><h3>Key Claims</h3><span class="more" @click="tab = 'Claims'">全部 {{ counts.claims }} →</span></div>
        <div class="panel pad" style="padding:6px">
          <div v-for="c in claims.slice(0, 6)" :key="c.id" class="item" @click="router.push('/knowledge/claim/' + c.id)">
            <div class="grow"><b>{{ c.subject_name }} → {{ c.predicate }} → {{ objLabel(c) }}</b><p>{{ c.claim_type }} · {{ c.polarity }} · {{ c.modality }}</p></div>
            <StatusTag :status="c.status" />
          </div>
          <EmptyState v-if="!claims.length" text="暂无 Claims" />
        </div>
        <div class="sechead"><h3>Relations</h3><span class="more" @click="tab = 'Relations'">全部 →</span></div>
        <div class="panel pad" style="padding:6px">
          <div v-for="r in relations.slice(0, 5)" :key="r.id" class="item" style="cursor:default">
            <div class="ico-badge ib-blue">→</div>
            <div class="grow"><b>{{ r.source_name }} → {{ r.predicate }} → {{ r.target_name }}</b><p>{{ r.status }}</p></div>
          </div>
          <EmptyState v-if="!relations.length" text="暂无图谱关系" />
        </div>
      </div>
    </div>

    <!-- Claims -->
    <div v-if="tab === 'Claims'" class="panel pad" style="padding:6px">
      <div v-for="c in claims" :key="c.id" class="item" @click="router.push('/knowledge/claim/' + c.id)">
        <div class="ico-badge ib-blue">⇢</div>
        <div class="grow"><b>{{ c.subject_name }} → {{ c.predicate }} → {{ objLabel(c) }}</b><p>{{ c.claim_type }} · {{ c.polarity }} · {{ c.modality }} · confidence {{ c.confidence }}</p></div>
        <StatusTag :status="c.status" />
      </div>
      <EmptyState v-if="!claims.length" text="暂无 Claims" />
    </div>

    <!-- Relations -->
    <div v-if="tab === 'Relations'">
      <div class="panel pad" style="padding:6px">
        <div v-for="r in relations" :key="r.id" class="item" style="cursor:default">
          <div class="ico-badge ib-blue">→</div>
          <div class="grow"><b>{{ r.source_name }} → {{ r.predicate }} → {{ r.target_name }}</b><p>confidence {{ r.confidence ?? '—' }}</p></div>
          <StatusTag :status="r.status" />
        </div>
        <EmptyState v-if="!relations.length" text="暂无图谱关系" />
      </div>
      <div class="notice violet" style="margin-top:12px">为什么有的 Claim 没有变成 Relation？Normalization 规则：modality = possible / probable 的因果 Claim 仅保留为 Claim，需要更高确定性才进入图谱。</div>
    </div>

    <!-- Graph：局部一跳关系（不是全库力导向图） -->
    <div v-if="tab === 'Graph'">
      <div class="row" style="margin-bottom:12px">
        <span class="faint" style="font-size:9px">从「{{ entity.name }}」出发的一跳关系 · 点击节点进入对象 · 点击连线查看关系来源</span>
        <div class="grow"></div>
        <button class="btn sm" @click="drawLocalGraph">重新布局</button>
      </div>
      <div v-if="graphLoading" class="empty" style="margin-bottom:12px">正在构建知识地图…</div>
      <div ref="graphBox" class="gcanvas"></div>
      <div v-if="selectedEdge" class="panel pad" style="margin-top:12px">
        <div style="font-size:12.5px">
          <b>{{ selectedEdge.source }}</b>
          <span class="tag blue" style="margin:0 6px">{{ selectedEdge.predicate }}</span>
          <b>{{ selectedEdge.target }}</b>
        </div>
        <div class="row" style="margin-top:10px;gap:8px;flex-wrap:wrap">
          <span v-if="selectedEdge.confidence != null" class="tag">confidence {{ selectedEdge.confidence }}</span>
          <StatusTag v-if="selectedEdge.status" :status="selectedEdge.status" />
          <button v-if="selectedEdge.documentId" class="btn sm" @click="openEdgeSource">打开原文并定位 →</button>
          <button class="btn sm ghost" @click="selectedEdge = null">关闭</button>
        </div>
      </div>
      <EmptyState
        v-else-if="graphEmpty && !graphLoading"
        title="还没有关系"
        text="这个对象尚未与其它实体建立关系——关系由高确定性断言派生，确认候选后会出现。"
      />
    </div>

    <!-- Events -->
    <div v-if="tab === 'Events'" class="panel pad">
      <div class="notice" style="margin-bottom:10px;background:var(--surface2)">以下事件来自与该对象共享来源文档的记录（事件本身不直接关联实体）。</div>
      <div class="timeline">
        <div v-for="e in events" :key="e.id" class="tle">
          <b>{{ e.description }}</b>
          <div>
            <StatusTag :status="e.status" />
            <span class="tag">{{ e.event_type }}</span>
            <span class="when">{{ e.time?.start || fmtDateTime(e.created_at) }}</span>
          </div>
        </div>
        <EmptyState v-if="!events.length" text="该对象关联的来源文档中暂无事件" />
      </div>
    </div>

    <!-- Evidence -->
    <div v-if="tab === 'Evidence'">
      <div v-for="c in evidence" :key="c.id" class="evidence" style="margin-bottom:12px">
        “{{ c.source_quote }}”
        <div class="src">
          <span>{{ docLabels[c.source_document_id] || '来源文档' }}</span>
          <span v-if="c.source_start_offset != null">offset [{{ c.source_start_offset }}, {{ c.source_end_offset }})</span>
        </div>
        <button class="btn sm" style="margin-top:9px" @click="openEvidenceSource(c)">打开原文并定位 →</button>
      </div>
      <EmptyState
        v-if="!evidence.length"
        title="还没有可展示的证据"
        text="证据来自抽取时定位到的原文片段——先为这个对象关联的文档运行抽取。"
      >
        <template #action><button class="btn" @click="router.push('/knowledge')">去知识库</button></template>
      </EmptyState>
    </div>

    <!-- Questions -->
    <div v-if="tab === 'Questions'">
      <div class="sechead" style="margin-top:0"><h3>相关问题</h3><button class="btn" @click="showQuestion = true">＋ 新建问题</button></div>
      <div class="panel pad" style="padding:6px">
        <div v-for="q in questions" :key="q.id" class="item" @click="router.push('/research')">
          <div class="ico-badge ib-blue">?</div>
          <div class="grow"><b>{{ q.content }}</b><p>status: {{ q.status }}</p></div>
          <StatusTag :status="q.status" />
        </div>
        <EmptyState v-if="!questions.length" text="该对象关联的来源文档中暂无问题" />
      </div>
    </div>

    <!-- Ideas -->
    <div v-if="tab === 'Ideas'">
      <div class="sechead" style="margin-top:0"><h3>相关想法</h3><button class="btn" @click="showIdea = true">＋ 记录想法</button></div>
      <div class="panel pad" style="padding:6px">
        <div v-for="i in ideas" :key="i.id" class="item" style="cursor:default">
          <div class="ico-badge ib-violet">✦</div>
          <div class="grow"><b>{{ i.content }}</b><p>status: {{ i.status }}</p></div>
          <StatusTag :status="i.status" />
        </div>
        <EmptyState v-if="!ideas.length" text="该对象关联的来源文档中暂无想法" />
      </div>
    </div>

    <AppModal :open="showEdit" title="编辑实体" subtitle="类型受 Ontology 注册表约束；改名会同步别名并做去重。" @close="showEdit = false">
      <input v-model="editName" class="field" style="width:100%" placeholder="名称" />
      <select v-model="editType" class="field" style="width:100%;margin-top:9px">
        <option v-for="t in ENTITY_TYPES" :key="t">{{ t }}</option>
      </select>
      <input v-model="editAliases" class="field" style="width:100%;margin-top:9px" placeholder="别名（逗号分隔）" />
      <textarea v-model="editDesc" class="field" style="width:100%;height:80px;margin-top:9px" placeholder="描述"></textarea>
      <div style="display:flex;justify-content:flex-end;gap:7px;margin-top:12px">
        <button class="btn" @click="showEdit = false">取消</button>
        <button class="btn primary" @click="saveEntity">保存</button>
      </div>
    </AppModal>

    <AppModal :open="showIdea" title="记录想法" subtitle="手工记录的观点默认为 candidate，可在「审核」中确认。" @close="showIdea = false">
      <textarea v-model="ideaText" class="field" style="width:100%;height:100px" placeholder="来自来源的想法、方案或研究方向…"></textarea>
      <div style="display:flex;justify-content:flex-end;gap:7px;margin-top:12px">
        <button class="btn" @click="showIdea = false">取消</button>
        <button class="btn primary" @click="addIdea">保存</button>
      </div>
    </AppModal>

    <AppModal :open="showQuestion" title="新建问题" subtitle="能够驱动知识获取、研究或验证的问题。" @close="showQuestion = false">
      <textarea v-model="questionText" class="field" style="width:100%;height:90px" placeholder="例如：检索质量如何量化影响 RAG 的最终效果？"></textarea>
      <div style="display:flex;justify-content:flex-end;gap:7px;margin-top:12px">
        <button class="btn" @click="showQuestion = false">取消</button>
        <button class="btn primary" @click="addQuestion">创建</button>
      </div>
    </AppModal>
  </div>

  <div class="page" v-else-if="loadError">
    <PageHead title="对象不存在" :subtitle="loadError">
      <template #actions>
        <button class="btn" @click="router.back()">← 返回</button>
      </template>
    </PageHead>
    <EmptyState :text="'找不到这个知识对象（' + loadError + '）——它可能已被删除或合并。'" />
  </div>
</template>
