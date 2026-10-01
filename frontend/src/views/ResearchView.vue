<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api, post, patchJson } from '../api/client'
import type { Claim, QuestionRow } from '../api/types'
import PageHead from '../components/PageHead.vue'
import StatusTag from '../components/StatusTag.vue'
import KpiStrip from '../components/KpiStrip.vue'
import FlowSteps from '../components/FlowSteps.vue'
import EmptyState from '../components/EmptyState.vue'
import MarkdownView from '../components/MarkdownView.vue'
import KnowledgeCard from '../components/KnowledgeCard.vue'
import { useAppStore } from '../stores/app'
import { toCard } from '../utils/claim'
import { fmtDateTime } from '../utils/time'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const store = useAppStore()

const dev = computed(() => store.developerMode)
/** Normal users meet a problem space, not the console. "研究任务" (task objects) and
    "Health" (claim accounting) expose internal execution detail, so they are
    Developer-Mode only; research itself stays fully usable from the Questions tab. */
const tabs = computed(() => dev.value
  ? ['questions', 'tasks', 'conflicts', 'health']
  : ['questions', 'conflicts'])
const tabLabel = (k: string) => t('research.tab.' + k)
/* 审核 is its own page now — keep old ?tab=review deep links working. */
if (route.query.tab === 'review') router.replace('/review')
const req = route.query.tab as string | undefined
const initialTab = req === 'conflicts' ? 'conflicts'
  : req === 'health' ? (dev.value ? 'health' : 'questions')
  : req === 'tasks' ? (dev.value ? 'tasks' : 'questions')
  : 'questions'
const tab = ref(initialTab)
const questions = ref<QuestionRow[]>([])
const conflicts = ref<any[]>([])
const health = ref<Record<string, any> | null>(null)
const claims = ref<Claim[]>([])

const flowQuestion = ref<QuestionRow | null>(null)
const knownClaims = ref<Claim[]>([])
const findings = ref('')
const researching = ref(false)

const researchTasks = ref<any[]>([])

async function load() {
  const [qs, cf, cl, rt] = await Promise.all([
    api<QuestionRow[]>('/api/questions?limit=200'),
    api<any[]>('/api/conflicts'),
    api<Claim[]>('/api/claims?limit=200'),
    api<any[]>('/api/research'),
  ])
  questions.value = qs; conflicts.value = cf; claims.value = cl; researchTasks.value = rt
  health.value = await api('/api/knowledge/health')
}
onMounted(load)

/** 问题列表按页加载：问题会累积成百上千条，一次读全既慢又让"待处理"被冲淡。 */
async function moreQuestions() {
  try {
    const qs = await api<QuestionRow[]>(`/api/questions?limit=200&offset=${questions.value.length}`)
    questions.value = [...questions.value, ...qs]
  } catch (e: any) { store.toast(t('research.moreQuestionsFail') + '：' + (e.message || t('research.pleaseRetry'))) }
}

/** 研究任务 → 它的候选知识。
 *
 *  结论是散文，而「还没被采纳的知识」才是研究真正要处理的东西——所以按需拉取
 *  （展开时一次请求），而不是给每个任务预取一遍。 */
const taskKnowledge = ref<Record<string, any[]>>({})
/** 正在整理成候选的任务 id —— 一次只处理一个，避免重复提交。 */
const proposing = ref<string | null>(null)

async function loadTaskKnowledge(t: any) {
  if (taskKnowledge.value[t.id]) return
  try {
    const body = await api<any>(`/api/research/${t.id}`)
    taskKnowledge.value = { ...taskKnowledge.value, [t.id]: body.knowledge || [] }
  } catch { taskKnowledge.value = { ...taskKnowledge.value, [t.id]: [] } }
}

/** 采纳之后重新读一次，让卡片反映新状态而不是留在旧状态上。 */
async function reloadTaskKnowledge(t: any) {
  const next = { ...taskKnowledge.value }
  delete next[t.id]
  taskKnowledge.value = next
  await loadTaskKnowledge(t)
}

/** 把结论整理成候选知识：结论 → 文档 → 抽取管线 → 候选。
 *  这里不直接写 claim——散文变成知识必须经过编译，否则这是全产品唯一一处
 *  「没被检查就成立」的知识。 */
async function proposeCandidates(t: any) {
  proposing.value = t.id
  try {
    const body = await post<any>(`/api/research/${t.id}/candidates`)
    store.toast(body.knowledge?.length
      ? t('research.proposedCandidates', { n: body.knowledge.length })
      : t('research.noCandidateStatement'))
    await reloadTaskKnowledge(t)
  } catch (e: any) { store.toast(e.message) } finally { proposing.value = null }
}

/** 采纳之后的刷新。
 *
 *  采纳动作和它引起的后果由 KnowledgeCard 统一报告——它拿到的就是操作返回的 `issues`。
 *  这里曾经自己回头查一遍关系来判断「有没有冲突」，于是同一个发现有第二份实现，
 *  并且指向的是冲突中心：那里收的是极性相反的冲突，而采纳产生的这条根本不在里面。
 *  一个判断只该有一处实现，一个发现也只该有一套说法。 */
async function onCandidateChanged(t: any, change: any) {
  if (change?.status !== 'verified') { await reloadTaskKnowledge(t); return }
  await reloadTaskKnowledge(t)
  // 采纳可能新增一条待处理的问题，角标要跟着变——否则它刚刚被报告，数字却还没动。
  await store.loadPendingReview()
}

async function runResearchTask(t: any) {
  try {
    const r = await post<any>(`/api/research/${t.id}/run`)
    store.toast(t('research.doneSaved'))
    await load()
    if (r?.findings) t.findings = r.findings
  } catch (e: any) { store.toast(e.message); await load() }
}

const open = computed(() => questions.value.filter(q => q.status === 'open'))
const partial = computed(() => questions.value.filter(q => q.status === 'partially_answered'))
const resolved = computed(() => questions.value.filter(q => ['resolved', 'answered'].includes(q.status)))

/** The headline strip speaks the user's language; the internal "Total Claims" count is
    a Developer-Mode diagnostic, not something a returning user should read as a metric. */
const kpiCells = computed(() => {
  const cells: { label: string; value: any; small?: string; warn?: boolean }[] = [
    { label: t('research.kpi.openQuestions'), value: open.value.length, small: `${partial.value.length} ${t('research.kpi.partial')}` },
    { label: t('research.kpi.conflicts'), value: conflicts.value.length, warn: conflicts.value.length > 0 },
    { label: t('research.kpi.resolved'), value: resolved.value.length },
  ]
  if (dev.value && health.value) {
    cells.push({ label: t('research.kpi.knowledgeItems'), value: health.value.total_claims ?? '—' })
  }
  return cells
})

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
    store.toast(t('research.taskCreated'))
    await load()
  } catch (e: any) { store.toast(e.message) }
}
/* ---------- 执行过程 ----------
   研究流水线是两次 agent 调用（KnowledgeAgent 采集 → ResearchAgent 综合），
   每次调用都记录一条 task_type='agent' 的 run。用它驱动真实阶段，
   而不是展示永远不变的假进度。 */
const phases = computed(() => [
  { role: 'KnowledgeAgent', label: t('research.phaseKnowledge') },
  { role: 'ResearchAgent', label: t('research.phaseResearch') },
])
const researchRuns = ref<any[]>([])
let researchPoll: number | undefined
let researchBaseline = ''

function phaseState(role: string): 'pending' | 'active' | 'done' | 'failed' {
  const run = researchRuns.value.find(r => r.agent_role === role)
  if (!run) return 'pending'
  if (run.status === 'started') return 'active'
  if (run.status === 'success') return 'done'
  return 'failed'
}

async function refreshResearchRuns() {
  try {
    const runs = await api<any[]>('/api/runs?task_type=agent&limit=10')
    // 只认本次研究开始之后产生的 run（created_at 是 UTC 字符串，字典序即时间序）
    researchRuns.value = researchBaseline
      ? runs.filter(r => r.created_at > researchBaseline)
      : runs
  } catch { /* 一次轮询失败就清空进度会更糟，保留上一次视图 */ }
}

function stopResearchPolling() {
  window.clearInterval(researchPoll)
  researchPoll = undefined
}
onBeforeUnmount(stopResearchPolling)

async function beginResearch() {
  stopResearchPolling()
  researchRuns.value = []
  const latest = await api<any[]>('/api/runs?task_type=agent&limit=1').catch(() => [])
  researchBaseline = latest[0]?.created_at || ''
  researchPoll = window.setInterval(refreshResearchRuns, 1500)
}

const flowSteps = computed(() => [
  { title: t('research.stepQuestion'), sub: flowQuestion.value?.status },
  { title: t('research.stepKnown'), sub: t('research.knownSub', { n: knownClaims.value.length, kind: store.developerMode ? t('research.claims') : t('research.facts') }) },
  { title: t('research.stepFindings'), sub: researching.value ? t('research.inProgress') : (findings.value ? t('research.returned') : t('research.pending')) },
  { title: t('research.stepReview'), sub: t('research.humanReview') },
])

async function continueResearch() {
  if (!flowQuestion.value) return
  researching.value = true; findings.value = ''
  await beginResearch()
  try {
    if (!activeTaskId.value) {
      const created = await post<any>('/api/research', { question_text: flowQuestion.value.content, question_id: flowQuestion.value.id })
      activeTaskId.value = created.id
    }
    const r = await post<any>(`/api/research/${activeTaskId.value}/run`)
    findings.value = r.findings || '(无返回)'
    store.toast(t('research.doneStored'))
    await load()
  } catch (e: any) { store.toast(e.message); await load() } finally {
    researching.value = false
    stopResearchPolling()
    await refreshResearchRuns()  // 最后一次刷新，让阶段定格在完成态
  }
}

async function keepBoth(cf: any) {
  let ok = 0
  for (const c of cf.claims) {
    try { await patchJson(`/api/knowledge/claim/${c.id}/status`, { status: 'verified' }); ok++ } catch { /* keep going */ }
  }
  store.toast(t('research.keptClaims', { ok, total: cf.claims.length }))
  await load()
}

async function createResearchFromConflict(cf: any) {
  const text = `为什么「${cf.subject_name} ${cf.predicate}」在不同来源下结论不同？`
  try {
    await post('/api/research', { question_text: text })
    store.toast(t('research.taskCreatedHint'))
    await load()
  } catch (e: any) { store.toast(e.message) }
}
async function resolveQuestion(q: QuestionRow) {
  try { await patchJson(`/api/knowledge/question/${q.id}/status`, { status: 'resolved' }); store.toast(t('research.markedResolved')); await load() } catch (e: any) { store.toast(e.message) }
}
</script>

<template>
  <div class="page">
    <PageHead :title="t('research.title')" :subtitle="t('research.subtitle')">
      <template #actions><button class="btn" @click="load">{{ t('research.reload') }}</button></template>
    </PageHead>

    <KpiStrip :cells="kpiCells" />

    <div class="seg" style="margin:16px 0 14px">
      <button v-for="tt in tabs" :key="tt" :class="{ active: tab === tt }" @click="tab = tt">
        {{ tabLabel(tt) }}<span v-if="tt === 'conflicts' && conflicts.length" class="tag amber" style="margin-left:4px">{{ conflicts.length }}</span>
      </button>
    </div>

    <div v-if="tab === 'tasks'">
      <div class="sechead"><h3>{{ t('research.tab.tasks') }}</h3><span class="faint" style="font-size:9px">{{ t('research.tasksSub') }}</span></div>
      <div class="panel pad" style="padding:6px">
        <div v-for="t in researchTasks" :key="t.id" class="item" style="cursor:default">
          <div class="ico-badge ib-violet">◇</div>
          <div class="grow">
            <b>{{ t.question_text }}</b>
            <p>{{ fmtDateTime(t.created_at) }} · <StatusTag :status="t.status" /></p>
            <details v-if="t.findings" style="margin-top:6px"
                     @toggle="(e: any) => e.target.open && loadTaskKnowledge(t)">
              <summary style="cursor:pointer;font-size:9px;color:var(--sub)">{{ t('research.findingsCandidates') }}</summary>
              <MarkdownView :content="t.findings" style="margin-top:8px" />
              <!-- 研究发现问题，但不替用户决定哪些成立：候选知识以卡片出现，
                   每张卡自带 [采纳][拒绝][依据]。 -->
              <template v-if="taskKnowledge[t.id]?.length">
                <div class="sechead" style="margin:12px 0 8px">
                  <h3>{{ t('research.candidate') }}</h3>
                  <span class="faint" style="font-size:9px;font-weight:400">
                    {{ t('research.candidateCount', { n: taskKnowledge[t.id].length }) }}
                  </span>
                </div>
                <div class="kcand">
                  <KnowledgeCard v-for="k in taskKnowledge[t.id]" :key="k.claim_id"
                                 :claim="toCard(k)" :evidence-count="k.sources" compact
                                 @changed="onCandidateChanged(t, $event)" />
                </div>
              </template>
              <div v-else-if="taskKnowledge[t.id]" style="margin-top:10px">
                <p class="faint" style="font-size:9.5px;margin:0 0 8px">
                  {{ t('research.noCandidatesYet') }}
                </p>
                <button class="btn sm" :disabled="proposing === t.id" @click="proposeCandidates(t)">
                  {{ proposing === t.id ? t('research.proposing') : t('research.toCandidates') }}
                </button>
              </div>
            </details>
          </div>
          <button class="btn sm" :disabled="t.status === 'running'" @click="runResearchTask(t)">
            {{ t.status === 'running' ? t('research.running') : t('research.continue') }}
          </button>
        </div>
        <EmptyState v-if="!researchTasks.length" :text="t('research.noTasks')" />
      </div>
    </div>

    <!-- Questions + flow -->
    <div v-if="tab === 'questions'" class="grid g2">
      <div>
        <div class="sechead"><h3>{{ t('research.openQuestions') }}</h3></div>
        <div class="panel pad" style="padding:6px">
          <div v-for="q in open" :key="q.id" class="item" style="cursor:default">
            <div class="ico-badge ib-blue">?</div>
            <div class="grow"><b>{{ q.content }}</b><p>{{ fmtDateTime(q.created_at) }}</p></div>
            <button class="btn sm primary" @click="openFlow(q)">{{ t('research.continue') }}</button>
          </div>
          <div v-for="q in partial" :key="q.id" class="item" style="cursor:default">
            <div class="ico-badge ib-amber">?</div>
            <div class="grow"><b>{{ q.content }}</b><p>{{ t('research.partial') }}</p></div>
            <button class="btn sm" @click="resolveQuestion(q)">{{ t('research.markResolved') }}</button>
          </div>
          <EmptyState v-if="!open.length && !partial.length" :text="t('research.noOpenQuestions')" />
          <div v-if="questions.length > 0 && questions.length % 200 === 0" style="text-align:center;margin-top:10px">
            <button class="btn" @click="moreQuestions">{{ t('research.loadMore') }}</button>
          </div>
        </div>
      </div>
      <div>
        <template v-if="flowQuestion">
          <div class="sechead"><h3>{{ t('research.loop') }}</h3><span class="tag blue" style="margin:0">{{ flowQuestion.status }}</span></div>
          <div class="panel pad">
            <b style="font-size:12px">{{ flowQuestion.content }}</b>
            <hr class="hairline" />
            <FlowSteps :steps="flowSteps" />

            <div class="sechead" style="margin:14px 0 10px"><h3>{{ t('research.execution') }}</h3></div>
            <div class="rphases">
              <div v-for="p in phases" :key="p.role" class="rphase" :class="phaseState(p.role)">
                <span class="rphase-dot">{{ phaseState(p.role) === 'done' ? '✓' : phaseState(p.role) === 'failed' ? '×' : phaseState(p.role) === 'active' ? '◌' : '·' }}</span>
                <span class="grow">{{ p.label }}</span>
                <span v-if="store.developerMode" class="tag">{{ p.role }}</span>
              </div>
              <p v-if="!researchRuns.length" class="muted" style="font-size:9px;margin:6px 0 0">
                <template v-if="store.developerMode">{{ t('research.phaseHintDev') }}</template>
                <template v-else>{{ t('research.phaseHint') }}</template>
              </p>
            </div>
            <div class="sechead" style="margin:10px 0 10px"><h3>{{ t('research.knownFacts') }}</h3></div>
            <div v-for="c in knownClaims" :key="c.id" class="item" style="cursor:default">
              <div class="grow">
                <b>{{ store.developerMode
                  ? `${c.subject_name} → ${c.predicate} → ${c.object_name || c.object_text || '—'}`
                  : (c.content || c.object_text || c.subject_name) }}</b>
                <p>{{ (c.source_quote || c.content || '').slice(0, 70) }}</p>
              </div>
            </div>
            <div v-if="!knownClaims.length" class="empty" style="margin-top:8px">{{ t('research.noKnownFacts') }}</div>
            <div v-if="findings" class="sechead"><h3>Findings</h3></div>
            <div v-if="findings" class="evidence">{{ findings }}</div>
            <div class="row" style="margin-top:14px">
              <button class="btn primary" :disabled="researching" @click="continueResearch">{{ researching ? t('research.running') : t('research.continue') }}</button>
              <button class="btn" @click="flowQuestion = null">{{ t('research.collapse') }}</button>
            </div>
          </div>
        </template>
        <template v-else>
          <div class="sechead"><h3>{{ t('research.howItWorks') }}</h3></div>
          <div class="panel pad">
            <FlowSteps :steps="[{ title: t('research.stepProblem') }, { title: t('research.stepKnownUnknown') }, { title: t('research.stepPlan') }, { title: t('research.stepEvidence') }]" />
            <FlowSteps :steps="[{ title: t('research.stepFindings') }, { title: t('research.stepCandidateKnowledge') }, { title: t('research.stepReview') }, { title: t('research.stepKnowledge') }]" />
            <hr class="hairline" />
            <p class="muted" style="font-size:10px;line-height:1.7;margin:0">{{ t('research.howDesc') }}</p>
          </div>
        </template>
      </div>
    </div>

    <!-- Conflicts -->
    <div v-if="tab === 'conflicts'">
      <div v-for="(cf, i) in conflicts" :key="i" class="panel pad" style="margin-bottom:16px">
        <div class="row" style="gap:10px"><span class="tag amber">{{ t('research.conflictN', { n: i + 1 }) }}</span><span class="faint" style="font-size:9px">{{ store.developerMode ? cf.subject_name + ' · ' + cf.predicate : cf.subject_name }}</span></div>
        <div class="grid g2" style="margin-top:14px">
          <div v-for="c in cf.claims" :key="c.id" class="panel pad" style="background:var(--surface2)">
            <span class="tag" :class="c.polarity === 'positive' ? 'green' : 'amber'">{{ c.polarity === 'positive' ? t('research.polarityPositive') : t('research.polarityConditional') }}</span>
            <div style="font-size:11px;margin-top:9px;line-height:1.7"><b>{{ c.content || c.object_text || '—' }}</b>
              <p class="muted" style="font-size:9.5px;margin-top:5px">
                <template v-if="store.developerMode">{{ t('research.sourcePrefix') }}{{ c.source_document_id?.slice(0, 8) }} · {{ c.modality }} · </template><StatusTag :status="c.status" /></p></div>
          </div>
        </div>
        <div class="sechead"><h3>{{ t('research.diffHead') }}</h3><span class="faint" style="font-size:9px">{{ t('research.diffSub') }}</span></div>
        <div class="notice violet">{{ t('research.diffNote') }}</div>
        <div class="row" style="margin-top:12px;gap:8px;flex-wrap:wrap">
          <button class="btn" @click="router.push('/knowledge/claim/' + cf.claims[0].id)">{{ t('research.viewEvidence') }}</button>
          <button class="btn" @click="keepBoth(cf)">{{ t('research.keepBoth') }}</button>
          <button class="btn primary" @click="createResearchFromConflict(cf)">{{ t('research.createResearch') }}</button>
          <button class="btn ghost" @click="router.push('/review')">{{ t('research.goReview') }}</button>
        </div>
      </div>
      <EmptyState v-if="!conflicts.length" :text="t('research.noConflicts')" />
    </div>

    <!-- Health -->
    <div v-if="tab === 'health' && health" class="panel pad">
      <div class="kv" style="grid-template-columns:220px 1fr;gap:14px;font-size:10.5px">
        <span>{{ t('research.health.verifiedClaims') }}</span>
        <b>{{ health.verified_claims }}/{{ health.total_claims }}（{{ health.verified_claim_ratio }}%）
          <span class="tag" :class="health.verified_claim_ratio > 50 ? 'green' : 'amber'" style="margin-left:6px">{{ health.verified_claim_ratio > 50 ? t('research.health.healthy') : t('research.health.mostPending') }}</span></b>
        <span>{{ t('research.health.verifiedEntities') }}</span>
        <b>{{ health.verified_entities }}/{{ health.total_entities }}（{{ health.verified_entity_ratio }}%）</b>
        <span>{{ t('research.health.relations') }}</span><b>{{ health.relations }} {{ t('research.health.relationsTail') }}</b>
        <span>{{ t('research.health.objectClaims') }}</span><b>{{ health.object_text_claims }} <span class="tag" style="margin-left:6px">{{ t('research.health.objectTag') }}</span></b>
        <span>{{ t('research.health.openQuestions') }}</span><b>{{ health.open_questions }}</b>
        <span>{{ t('research.health.stale') }}</span><b>{{ health.stale_candidates }} <span class="tag" style="margin-left:6px">{{ t('research.health.staleTag') }}</span></b>
      </div>
      <div class="notice violet" style="margin-top:12px">{{ t('research.metricsNote') }}</div>
    </div>
  </div>
</template>

<style scoped>
/* 候选知识：研究的产出以卡片收尾，而不是以一段散文收尾 */
.kcand{display:grid;gap:9px}
/* 研究执行过程：真实 agent 阶段，替代写死的假进度 */
.rphases{display:flex;flex-direction:column;gap:7px}
.rphase{display:flex;align-items:center;gap:9px;padding:9px 12px;border-radius:11px;background:var(--surface2);font-size:10px;color:var(--sub)}
.rphase.done{color:#1e8f6b;background:var(--tint-mint)}
.rphase.active{color:var(--accent);background:var(--tint-blue)}
.rphase.failed{color:#c8565f;background:#fff0f1}
.rphase-dot{font-size:10px;line-height:1;flex:none}
</style>
