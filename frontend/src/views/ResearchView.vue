<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, post, patchJson } from '../api/client'
import type { Claim, QuestionRow } from '../api/types'
import PageHead from '../components/PageHead.vue'
import StatusTag from '../components/StatusTag.vue'
import KpiStrip from '../components/KpiStrip.vue'
import FlowSteps from '../components/FlowSteps.vue'
import EmptyState from '../components/EmptyState.vue'
import ReviewQueue from '../components/ReviewQueue.vue'
import MarkdownView from '../components/MarkdownView.vue'
import { useAppStore } from '../stores/app'
import { fmtDateTime } from '../utils/time'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

const TABS = ['Questions', '研究任务', 'Conflicts', '审核', 'Health']
const TAB_LABELS: Record<string, string> = { Questions: '问题', Conflicts: '冲突', Health: '知识健康' }
const initialTab = route.query.tab === 'conflicts' ? 'Conflicts'
  : route.query.tab === 'health' ? 'Health'
  : route.query.tab === 'review' ? '审核'
  : route.query.tab === 'tasks' ? '研究任务' : 'Questions'
const tab = ref(initialTab as string)
const questions = ref<QuestionRow[]>([])
const conflicts = ref<any[]>([])
const health = ref<Record<string, any> | null>(null)
const claims = ref<Claim[]>([])

const flowQuestion = ref<QuestionRow | null>(null)
const knownClaims = ref<Claim[]>([])
const findings = ref('')
const researching = ref(false)

const researchTasks = ref<any[]>([])
const pending = ref(0)

async function load() {
  const [qs, cf, cl, rt, rv] = await Promise.all([
    api<QuestionRow[]>('/api/questions?limit=200'),
    api<any[]>('/api/conflicts'),
    api<Claim[]>('/api/claims?limit=200'),
    api<any[]>('/api/research'),
    api<any>('/api/review?limit=200'),
  ])
  questions.value = qs; conflicts.value = cf; claims.value = cl; researchTasks.value = rt
  pending.value = (rv.entities?.length || 0) + (rv.claims?.length || 0) + (rv.relations?.length || 0)
  health.value = await api('/api/knowledge/health')
}
onMounted(load)

async function runResearchTask(t: any) {
  try {
    const r = await post<any>(`/api/research/${t.id}/run`)
    store.toast('研究完成，Findings 已保存')
    await load()
    if (r?.findings) t.findings = r.findings
  } catch (e: any) { store.toast(e.message); await load() }
}

const open = computed(() => questions.value.filter(q => q.status === 'open'))
const partial = computed(() => questions.value.filter(q => q.status === 'partially_answered'))
const resolved = computed(() => questions.value.filter(q => ['resolved', 'answered'].includes(q.status)))

const activeTaskId = ref<string | null>(null)

async function openFlow(q: QuestionRow) {
  flowQuestion.value = q
  const kw = q.content.slice(0, 4)
  knownClaims.value = claims.value.filter(c =>
    (c.content || '').includes(kw) || (c.source_quote || '').includes(kw) || (c.object_text || '').includes(kw)).slice(0, 8)
  findings.value = ''
  try {
    const r = await post<any>('/api/research', { question_text: q.content, question_id: q.id })
    activeTaskId.value = r.id
    store.toast('已创建研究任务')
    await load()
  } catch (e: any) { store.toast(e.message) }
}
async function continueResearch() {
  if (!flowQuestion.value) return
  researching.value = true; findings.value = ''
  try {
    if (!activeTaskId.value) {
      const created = await post<any>('/api/research', { question_text: flowQuestion.value.content, question_id: flowQuestion.value.id })
      activeTaskId.value = created.id
    }
    const r = await post<any>(`/api/research/${activeTaskId.value}/run`)
    findings.value = r.findings || '(无返回)'
    store.toast('研究完成，Findings 已落库')
    await load()
  } catch (e: any) { store.toast(e.message); await load() } finally { researching.value = false }
}

async function keepBoth(cf: any) {
  let ok = 0
  for (const c of cf.claims) {
    try { await patchJson(`/api/knowledge/claim/${c.id}/status`, { status: 'verified' }); ok++ } catch { /* keep going */ }
  }
  store.toast(`已保留 ${ok}/${cf.claims.length} 条 Claim（保留各自条件，均标记 verified）`)
  await load()
}

async function createResearchFromConflict(cf: any) {
  const text = `为什么「${cf.subject_name} ${cf.predicate}」在不同来源下结论不同？`
  try {
    await post('/api/research', { question_text: text })
    store.toast('已创建研究任务，可在「研究任务」中执行')
    await load()
  } catch (e: any) { store.toast(e.message) }
}
async function resolveQuestion(q: QuestionRow) {
  try { await patchJson(`/api/knowledge/question/${q.id}/status`, { status: 'resolved' }); store.toast('已标记 resolved'); await load() } catch (e: any) { store.toast(e.message) }
}
</script>

<template>
  <div class="page">
    <PageHead title="研究" subtitle="Question → Evidence → Claims → Review → Knowledge，一个闭环。">
      <template #actions><button class="btn" @click="load">刷新</button></template>
    </PageHead>

    <KpiStrip :cells="[
      { label: 'Open Questions', value: open.length, small: `${partial.length} 部分回答` },
      { label: 'Conflicts', value: conflicts.length, warn: conflicts.length > 0 },
      { label: 'Resolved', value: resolved.length },
      { label: 'Total Claims', value: health?.total_claims ?? '—' },
    ]" />

    <div class="seg" style="margin:16px 0 14px">
      <button v-for="t in TABS" :key="t" :class="{ active: tab === t }" @click="tab = t">
        {{ TAB_LABELS[t] || t }}<span v-if="t === 'Conflicts' && conflicts.length" class="tag amber" style="margin-left:4px">{{ conflicts.length }}</span>
        <span v-if="t === '审核' && pending" class="tag blue" style="margin-left:4px">{{ pending }}</span>
      </button>
    </div>

    <ReviewQueue v-if="tab === '审核'" />
    <div v-if="tab === '研究任务'">
      <div class="sechead"><h3>研究任务</h3><span class="faint" style="font-size:9px">Question → Findings 的落库闭环</span></div>
      <div class="panel pad" style="padding:6px">
        <div v-for="t in researchTasks" :key="t.id" class="item" style="cursor:default">
          <div class="ico-badge ib-violet">◇</div>
          <div class="grow">
            <b>{{ t.question_text }}</b>
            <p>{{ fmtDateTime(t.created_at) }} · <StatusTag :status="t.status" /></p>
            <details v-if="t.findings" style="margin-top:6px">
              <summary style="cursor:pointer;font-size:9px;color:var(--sub)">Findings</summary>
              <MarkdownView :content="t.findings" style="margin-top:8px" />
            </details>
          </div>
          <button class="btn sm" :disabled="t.status === 'running'" @click="runResearchTask(t)">
            {{ t.status === 'running' ? '研究中…' : '继续研究' }}
          </button>
        </div>
        <EmptyState v-if="!researchTasks.length" text="还没有研究任务——在 Questions 里点「继续研究」会自动创建" />
      </div>
    </div>

    <!-- Questions + flow -->
    <div v-if="tab === 'Questions'" class="grid g2">
      <div>
        <div class="sechead"><h3>Open Questions</h3></div>
        <div class="panel pad" style="padding:6px">
          <div v-for="q in open" :key="q.id" class="item" style="cursor:default">
            <div class="ico-badge ib-blue">?</div>
            <div class="grow"><b>{{ q.content }}</b><p>{{ fmtDateTime(q.created_at) }}</p></div>
            <button class="btn sm primary" @click="openFlow(q)">继续研究</button>
          </div>
          <div v-for="q in partial" :key="q.id" class="item" style="cursor:default">
            <div class="ico-badge ib-amber">?</div>
            <div class="grow"><b>{{ q.content }}</b><p>partially answered</p></div>
            <button class="btn sm" @click="resolveQuestion(q)">标记已解决</button>
          </div>
          <EmptyState v-if="!open.length && !partial.length" text="没有开放问题——知识库状态良好" />
        </div>
      </div>
      <div>
        <template v-if="flowQuestion">
          <div class="sechead"><h3>研究闭环</h3><span class="tag blue" style="margin:0">{{ flowQuestion.status }}</span></div>
          <div class="panel pad">
            <b style="font-size:12px">{{ flowQuestion.content }}</b>
            <hr class="hairline" />
            <FlowSteps :steps="[
              { title: 'Question', sub: flowQuestion.status },
              { title: 'Known', sub: `${knownClaims.length} claims` },
              { title: 'Unknown', sub: '待研究' },
              { title: 'Plan', sub: 'ResearchAgent' },
            ]" />
            <FlowSteps :steps="[
              { title: 'Sources', sub: `${knownClaims.length} 条` },
              { title: 'Findings', sub: researching ? '进行中' : findings ? '已返回' : '待执行' },
              { title: 'Claims', sub: '待生成' },
              { title: 'Review', sub: '人工' },
            ]" />
            <div class="sechead" style="margin:10px 0 10px"><h3>Known · 相关证据</h3></div>
            <div v-for="c in knownClaims" :key="c.id" class="item" style="cursor:default">
              <div class="grow"><b>{{ c.subject_name }} → {{ c.predicate }} → {{ c.object_name || c.object_text || '—' }}</b><p>{{ (c.source_quote || c.content || '').slice(0, 70) }}</p></div>
            </div>
            <div v-if="!knownClaims.length" class="empty" style="margin-top:8px">没有直接相关的 Claims——这正是需要研究的空白</div>
            <div v-if="findings" class="sechead"><h3>Findings</h3></div>
            <div v-if="findings" class="evidence">{{ findings }}</div>
            <div class="row" style="margin-top:14px">
              <button class="btn primary" :disabled="researching" @click="continueResearch">{{ researching ? '研究中…' : '继续研究' }}</button>
              <button class="btn" @click="flowQuestion = null">收起</button>
            </div>
          </div>
        </template>
        <template v-else>
          <div class="sechead"><h3>研究如何运作</h3></div>
          <div class="panel pad">
            <FlowSteps :steps="[{ title: 'Question' }, { title: 'Known / Unknown' }, { title: 'Plan' }, { title: 'Evidence' }]" />
            <FlowSteps :steps="[{ title: 'Findings' }, { title: 'Candidate Claims' }, { title: 'Review' }, { title: 'Knowledge' }]" />
            <hr class="hairline" />
            <p class="muted" style="font-size:10px;line-height:1.7;margin:0">点击左侧任一开放问题的<b style="color:var(--text)">「继续研究」</b>，查看完整闭环：整理已知与未知，由 ResearchAgent 收集证据、产出候选知识，交给你审核。</p>
          </div>
        </template>
      </div>
    </div>

    <!-- Conflicts -->
    <div v-if="tab === 'Conflicts'">
      <div v-for="(cf, i) in conflicts" :key="i" class="panel pad" style="margin-bottom:16px">
        <div class="row" style="gap:10px"><span class="tag amber">Conflict #{{ i + 1 }}</span><span class="faint" style="font-size:9px">{{ cf.subject_name }} · {{ cf.predicate }}</span></div>
        <div class="grid g2" style="margin-top:14px">
          <div v-for="c in cf.claims" :key="c.id" class="panel pad" style="background:var(--surface2)">
            <span class="tag" :class="c.polarity === 'positive' ? 'green' : 'amber'">{{ c.polarity }}</span>
            <div style="font-size:11px;margin-top:9px;line-height:1.7"><b>{{ c.content || c.object_text || '—' }}</b>
              <p class="muted" style="font-size:9.5px;margin-top:5px">来源：{{ c.source_document_id?.slice(0, 8) }} · {{ c.modality }} · <StatusTag :status="c.status" /></p></div>
          </div>
        </div>
        <div class="sechead"><h3>Difference</h3><span class="faint" style="font-size:9px">为什么两个来源结论不同</span></div>
        <div class="notice violet">两个结论可能都成立，只是适用条件不同。请查看各自 Evidence 与来源，决定保留两者（互相标注条件）、创建研究，或提交审核。</div>
        <div class="row" style="margin-top:12px;gap:8px;flex-wrap:wrap">
          <button class="btn" @click="router.push('/knowledge/claim/' + cf.claims[0].id)">查看 Evidence</button>
          <button class="btn" @click="keepBoth(cf)">保留两者</button>
          <button class="btn primary" @click="createResearchFromConflict(cf)">创建研究</button>
          <button class="btn ghost" @click="tab = '审核'">去审核</button>
        </div>
      </div>
      <EmptyState v-if="!conflicts.length" text="没有检测到冲突——同一主题下的 Claims 极性一致" />
    </div>

    <!-- Health -->
    <div v-if="tab === 'Health' && health" class="panel pad">
      <div class="kv" style="grid-template-columns:220px 1fr;gap:14px;font-size:10.5px">
        <span>已验证 Claims</span>
        <b>{{ health.verified_claims }}/{{ health.total_claims }}（{{ health.verified_claim_ratio }}%）
          <span class="tag" :class="health.verified_claim_ratio > 50 ? 'green' : 'amber'" style="margin-left:6px">{{ health.verified_claim_ratio > 50 ? 'healthy' : '多数待在审核' }}</span></b>
        <span>已验证实体</span>
        <b>{{ health.verified_entities }}/{{ health.total_entities }}（{{ health.verified_entity_ratio }}%）</b>
        <span>图谱关系</span><b>{{ health.relations }} 条（由 verified/高确定性 Claims 派生）</b>
        <span>文本对象 Claims</span><b>{{ health.object_text_claims }} <span class="tag" style="margin-left:6px">object 是文本而非实体，设计内行为</span></b>
        <span>开放问题</span><b>{{ health.open_questions }}</b>
        <span>陈旧候选</span><b>{{ health.stale_candidates }} <span class="tag" style="margin-left:6px">候选实体 > 90 天未动</span></b>
      </div>
      <div class="notice violet" style="margin-top:12px">指标口径：<b>已验证比例</b>反映人工审核进度（真实可信度信号）；文本对象 Claims 与图谱关系数仅作规模参考，不再伪装成「覆盖率」。</div>
    </div>
  </div>
</template>
