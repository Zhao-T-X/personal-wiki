<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
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

const route = useRoute()
const router = useRouter()
const store = useAppStore()

const TABS = ['Questions', '研究任务', 'Conflicts', 'Health']
const TAB_LABELS: Record<string, string> = { Questions: '问题', Conflicts: '冲突', Health: '知识健康' }
/* 审核 is its own page now — keep old ?tab=review deep links working. */
if (route.query.tab === 'review') router.replace('/review')
const initialTab = route.query.tab === 'conflicts' ? 'Conflicts'
  : route.query.tab === 'health' ? 'Health'
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
      ? `已整理出 ${body.knowledge.length} 条研究候选`
      : '结论里没有可落成知识的断言')
    await reloadTaskKnowledge(t)
  } catch (e: any) { store.toast(e.message) } finally { proposing.value = null }
}

/** 采纳后的反馈：不是一句「成功」，而是这条知识现在是什么、以及要不要处理它引起的冲突。 */
async function onCandidateChanged(t: any, change: any) {
  if (change?.status !== 'verified') { await reloadTaskKnowledge(t); return }
  try {
    const relations = (await api<any>(`/api/claims/${change.id}/relations`)).relations || []
    const conflict = relations.find((r: any) => r.relationship === 'contradicts')
    if (conflict) {
      store.toast('已加入知识库，同时发现与现有知识冲突', {
        label: '处理冲突', run: () => router.push({ path: '/research', query: { tab: 'conflicts' } }),
      })
    } else {
      store.toast('已加入知识库', {
        label: '查看知识', run: () => router.push('/knowledge/claim/' + change.id),
      })
    }
  } catch { store.toast('已加入知识库') }
  await reloadTaskKnowledge(t)
}

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
/* ---------- 执行过程 ----------
   研究流水线是两次 agent 调用（KnowledgeAgent 采集 → ResearchAgent 综合），
   每次调用都记录一条 task_type='agent' 的 run。用它驱动真实阶段，
   而不是展示永远不变的假进度。 */
const PHASES = [
  { role: 'KnowledgeAgent', label: '检索知识库里已知的内容' },
  { role: 'ResearchAgent', label: '对比来源并综合出结论' },
]
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
    store.toast('研究完成，Findings 已落库')
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
      </button>
    </div>

    <div v-if="tab === '研究任务'">
      <div class="sechead"><h3>研究任务</h3><span class="faint" style="font-size:9px">Question → Findings 的落库闭环</span></div>
      <div class="panel pad" style="padding:6px">
        <div v-for="t in researchTasks" :key="t.id" class="item" style="cursor:default">
          <div class="ico-badge ib-violet">◇</div>
          <div class="grow">
            <b>{{ t.question_text }}</b>
            <p>{{ fmtDateTime(t.created_at) }} · <StatusTag :status="t.status" /></p>
            <details v-if="t.findings" style="margin-top:6px"
                     @toggle="(e: any) => e.target.open && loadTaskKnowledge(t)">
              <summary style="cursor:pointer;font-size:9px;color:var(--sub)">Findings 与候选知识</summary>
              <MarkdownView :content="t.findings" style="margin-top:8px" />
              <!-- 研究发现问题，但不替用户决定哪些成立：候选知识以卡片出现，
                   每张卡自带 [采纳][拒绝][依据]。 -->
              <template v-if="taskKnowledge[t.id]?.length">
                <div class="sechead" style="margin:12px 0 8px">
                  <h3>研究候选</h3>
                  <span class="faint" style="font-size:9px;font-weight:400">
                    {{ taskKnowledge[t.id].length }} 条 · 采纳后才成为知识
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
                  这份结论还没有落成候选知识。结论里能被引文支撑的断言才会变成候选。
                </p>
                <button class="btn sm" :disabled="proposing === t.id" @click="proposeCandidates(t)">
                  {{ proposing === t.id ? '整理中…' : '把结论整理成研究候选' }}
                </button>
              </div>
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
              { title: 'Known', sub: `${knownClaims.length} 条相关断言` },
              { title: 'Findings', sub: researching ? '进行中' : findings ? '已返回' : '待执行' },
              { title: 'Review', sub: '人工' },
            ]" />

            <div class="sechead" style="margin:14px 0 10px"><h3>执行过程</h3></div>
            <div class="rphases">
              <div v-for="p in PHASES" :key="p.role" class="rphase" :class="phaseState(p.role)">
                <span class="rphase-dot">{{ phaseState(p.role) === 'done' ? '✓' : phaseState(p.role) === 'failed' ? '×' : phaseState(p.role) === 'active' ? '◌' : '·' }}</span>
                <span class="grow">{{ p.label }}</span>
                <span class="tag">{{ p.role }}</span>
              </div>
              <p v-if="!researchRuns.length" class="muted" style="font-size:9px;margin:6px 0 0">
                点「继续研究」后，这里会显示两个 Agent 的真实执行顺序与结果。
              </p>
            </div>
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
          <button class="btn ghost" @click="router.push('/review')">去审核</button>
        </div>
      </div>
      <EmptyState v-if="!conflicts.length" text="没有检测到冲突——同一主题下的 Claims 极性一致" />
    </div>

    <!-- Health -->
    <div v-if="tab === 'Health' && health" class="panel pad">
      <div class="kv" style="grid-template-columns:220px 1fr;gap:14px;font-size:10.5px">
        <span>已验证 Claims</span>
        <b>{{ health.verified_claims }}/{{ health.total_claims }}（{{ health.verified_claim_ratio }}%）
          <span class="tag" :class="health.verified_claim_ratio > 50 ? 'green' : 'amber'" style="margin-left:6px">{{ health.verified_claim_ratio > 50 ? '健康' : '多数待在审核' }}</span></b>
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

<style scoped>
/* 候选知识：研究的产出以卡片收尾，而不是以一段散文收尾 */
.kcand{display:grid;gap:9px}
/* 研究执行过程：真实 agent 阶段，替代写死的假进度 */
.rphases{display:flex;flex-direction:column;gap:7px}
.rphase{display:flex;align-items:center;gap:9px;padding:9px 12px;border-radius:11px;background:var(--surface2);font-size:10px;color:var(--sub)}
.rphase.done{color:#1e8f6b;background:var(--tint-mint)}
.rphase.active{color:#4a63e8;background:var(--tint-blue)}
.rphase.failed{color:#c8565f;background:#fff0f1}
.rphase-dot{font-size:10px;line-height:1;flex:none}
</style>
