<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Bot, MessageCircle, Search, Sigma, Telescope } from 'lucide-vue-next'
import { api, post } from '../api/client'
import type { Run } from '../api/types'
import AnswerCard from '../components/AnswerCard.vue'
import LoadBoundary from '../components/LoadBoundary.vue'
import { correctionSeedFor } from '../utils/claim'
import { useAsyncState } from '../utils/useAsyncState'
import StatusTag from '../components/StatusTag.vue'
import DataTable from '../components/DataTable.vue'
import RunDrawer from '../components/RunDrawer.vue'
import BoundaryList from '../components/BoundaryList.vue'
import EmptyState from '../components/EmptyState.vue'
import MarkdownView from '../components/MarkdownView.vue'
import { useAppStore } from '../stores/app'
import { fmtDateTime } from '../utils/time'

const route = useRoute()
const router = useRouter()
const store = useAppStore()
const { t } = useI18n()

type Mode = 'knowledge' | 'agent'
/** Normal users never pick an engine — the system answers from the knowledge base and
    escalates to research when needed. The Agent engine is preserved, but only exposed in
    Developer Mode; turning that off always drops the user back to knowledge QA. */
const mode = ref<Mode>('knowledge')
if (store.developerMode && (route.query.mode as Mode) === 'agent') mode.value = 'agent'
watch(() => store.developerMode, on => { if (!on) mode.value = 'knowledge' })
const question = ref((route.query.q as string) || '')

/* knowledge mode */
const loading = ref(false)
/** 答案 / 支持知识 / 依据 —— 三层，顺序就是可信度的展示顺序。
 *
 *  ``knowledge`` 是**佐证**，不是答案：答案是句子，卡片是事实。把答案渲染成卡片会
 *  让用户自己猜哪一行才是回答。 */
const result = ref<{ answer: string; evidence: any[]; citations: any[]; knowledge?: any[]; reason?: string } | null>(null)
/** Citation Validation report for the last answer (POST /api/qa/validate). */
const validation = ref<any>(null)
const validating = ref(false)

/** 「这条有问题」的纠正入口已收进 AnswerCard（沿用 CorrectionFlow），
   这里只负责答案过期后的提示与重问。 */
/** 这条答案是用哪条知识算出来的——要改的就是它，而不是用户刚刚问的那句话。
    没有任何知识支撑这个答案时（答案来自模型常识），起点为空，请用户直接写下正确说法。 */
const correction = computed(() => correctionSeedFor(result.value?.knowledge?.[0]))

/** 知识被改过之后，这条答案就不该再装作还成立。 */
const answerStale = ref(false)

function onCorrected(r?: any) {
  answerStale.value = true
  store.toast(t('qa.correctedToast'), {
    label: t('qa.correctedView'), run: () => { if (r?.claim_id) router.push('/knowledge/claim/' + r.claim_id) },
  })
}

/** 支持知识被就地改了（卡片自带纠正）：答案同样过期。 */
function onSupportingChanged() { answerStale.value = true }

function reask() { void askKnowledge() }

/* agent mode */
const agentLoading = ref(false)
const agentResult = ref<{ answer: string; agent: string; framework?: string } | null>(null)
const agentRole = ref('auto')
const agentMs = ref<number | null>(null)
const agentRoles = ref<{ id: string; name: string; description: string }[]>([])
/** 读不到 Agent 列表时要说出来：只剩「自动路由」看起来像"系统里只有一个 Agent"。 */
const rolesError = ref('')
const runDrawerId = ref<string | null>(null)
const resultBox = ref<HTMLElement | null>(null)

/** Jump to the source: knowledge page opens the document drawer and highlights this chunk. */
function openEvidence(e: { document_id?: string; id?: string }) {
  if (!e.document_id) { store.toast(t('anscard.noSourceToast')); return }
  router.push({ path: '/knowledge', query: { doc: e.document_id, chunk: e.id } })
}

/** Long answers land below the fold — bring the result into view when it arrives. */
async function scrollToResult() {
  await nextTick()
  resultBox.value?.scrollIntoView({ block: 'start', behavior: 'smooth' })
}

/* 最近问答 / 开放问题：读不到时必须说"读不到"。
   曾经失败会渲染成一张空表 —— 用户由此得到的结论是"我从来没问过问题"。 */
const meta = useAsyncState(async () => {
  const [ask, agent, qs] = await Promise.all([
    api<Run[]>('/api/runs?task_type=ask&limit=10'),
    api<Run[]>('/api/runs?task_type=agent&limit=10'),
    api<any[]>('/api/questions?limit=200'),
  ])
  return {
    recent: [...ask, ...agent].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 12),
    open: qs.filter(q => q.status === 'open'),
  }
}, { recent: [] as Run[], open: [] as any[] })
const recent = computed(() => meta.data.value.recent)
const openQuestions = computed(() => meta.data.value.open)

async function loadRoles() {
  rolesError.value = ''
  try {
    const r = await api<{ roles: { id: string; name: string; description: string }[] }>('/api/agent/roles')
    agentRoles.value = r.roles
  } catch (e: any) {
    agentRoles.value = []
    rolesError.value = t('qa.rolesError')
    void e
  }
}
async function loadMeta() { await Promise.all([meta.reload(), loadRoles()]) }
onMounted(() => { void loadMeta(); if (question.value) send() })

const trace = computed(() => {
  const byMethod: Record<string, number> = {}
  for (const e of result.value?.evidence || []) byMethod[e.method || 'hybrid'] = (byMethod[e.method || 'hybrid'] || 0) + 1
  return Object.entries(byMethod)
})

/** What the evidence actually supports — the point is honesty about the gaps. */
const boundary = computed(() => {
  const ev = result.value?.evidence || []
  const items: { level: 'ok' | 'cond' | 'open'; text: string }[] = []
  if (ev.length >= 3) items.push({ level: 'ok', text: t('qa.boundary.strong', { n: ev.length }) })
  else if (ev.length) items.push({ level: 'cond', text: t('qa.boundary.weak', { n: ev.length }) })
  else items.push({ level: 'open', text: t('qa.boundary.none') })
  items.push({ level: 'cond', text: t('qa.boundary.noAutofill') })
  const kw = question.value.trim().slice(0, 6)
  const related = openQuestions.value.filter(q => kw && (q.content as string).includes(kw)).length
  items.push({ level: 'open', text: related ? t('qa.boundary.relatedOpen', { n: related }) : t('qa.boundary.noRelatedOpen') })
  return items
})

/** Evidence strength, not a binary badge — an answer with zero hits must not read as "Grounded". */
const evidenceLevel = computed(() => {
  if (!result.value) return null
  const n = result.value.evidence?.length || 0
  if (n === 0) return { label: t('qa.level.none'), cls: 'red' }
  if (n < 3) return { label: t('qa.level.limited'), cls: 'amber' }
  return { label: t('qa.level.grounded'), cls: 'green' }
})

async function send() {
  const q = question.value.trim()
  if (!q) { store.toast(t('qa.needQuestion')); return }
  if (mode.value === 'knowledge') await askKnowledge()
  else await askAgent()
}

async function askKnowledge() {
  loading.value = true; result.value = null; agentResult.value = null; validation.value = null
  answerStale.value = false
  try {
    result.value = await post('/api/ask', { question: question.value.trim(), top_k: 8 })
    await loadMeta()
    await scrollToResult()
  } catch (e: any) { store.toast(e.message) } finally { loading.value = false }
}

/** Citation Validation: is this answer actually backed by its citations? */
async function validateCitations() {
  if (!result.value) return
  validating.value = true; validation.value = null
  try {
    validation.value = await post('/api/qa/validate', {
      question: question.value.trim(),
      answer: result.value.answer,
      citations: result.value.citations || [],
    })
  } catch (e: any) { store.toast(e.message) } finally { validating.value = false }
}

/* Multi-turn handle (P3): one id per agent chat session; the backend keeps the
   compressed history in conversation_summaries and the model sees the rolling
   summary + recent window instead of nothing. "新会话" resets it. */
const conversationId = ref('')

function newConversation() {
  conversationId.value = (crypto as any).randomUUID ? crypto.randomUUID() : `c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  agentResult.value = null
  store.toast(t('qa.newConversationToast'))
}

async function askAgent() {
  agentLoading.value = true; agentResult.value = null; result.value = null; agentMs.value = null
  if (!conversationId.value) {
    conversationId.value = (crypto as any).randomUUID ? crypto.randomUUID() : `c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  }
  const t0 = performance.now()
  try {
    const r = await post<any>('/api/agent/ask', {
      message: question.value.trim(), role: agentRole.value, conversation_id: conversationId.value,
    })
    agentMs.value = Math.round(performance.now() - t0)
    agentResult.value = { answer: r.answer, agent: r.agent, framework: r.framework }
    await loadMeta()
    await scrollToResult()
  } catch (e: any) { store.toast(e.message) } finally { agentLoading.value = false }
}

function switchMode(m: Mode) {
  mode.value = m
  result.value = null; agentResult.value = null
}

const columns = computed(() => [
  { key: 'task_type', label: t('qa.cols.mode') }, { key: 'created_at', label: t('qa.cols.time') },
  { key: 'question', label: t('qa.cols.question') }, { key: 'status', label: t('qa.cols.status') }, { key: 'duration_ms', label: t('qa.cols.duration') },
])
</script>

<template>
  <div class="page">
    <div class="qahead">
      <div class="eyebrow">ASK</div>
      <h1 class="qatitle">{{ t('qa.title') }}</h1>
      <p class="qasub">{{ t('qa.sub') }}</p>
    </div>

    <!-- mode switch: only offered in Developer Mode. Normal users never choose an engine. -->
    <div v-if="store.developerMode" class="row" style="justify-content:center;margin-bottom:16px;gap:10px">
      <div class="seg" style="padding:3px">
        <button :class="{ active: mode === 'knowledge' }" @click="switchMode('knowledge')">{{ t('qa.modeKnowledge') }}</button>
        <button :class="{ active: mode === 'agent' }" @click="switchMode('agent')">{{ t('qa.modeAgent') }}</button>
      </div>
      <select v-if="mode === 'agent'" v-model="agentRole" class="field" style="padding:8px 10px">
        <option value="auto">{{ t('qa.autoRoute') }}</option>
        <option v-for="r in agentRoles" :key="r.id" :value="r.id">{{ r.name }} · {{ r.description }}</option>
      </select>
      <span v-if="mode === 'agent' && rolesError" class="faint" style="font-size:9.5px">{{ rolesError }}</span>
    </div>

    <div class="askwrap">
      <div class="askcard">
        <input v-model="question" :placeholder="t('qa.askPlaceholder')" @keydown.enter="send()" />
        <div class="askbottom">
          <span>{{ t('qa.basedOn') }}</span>
          <button class="btn primary" :disabled="loading || agentLoading" @click="send()">
            {{ loading || agentLoading ? t('qa.asking') : t('qa.ask') }}
          </button>
        </div>
      </div>
    </div>

    <div id="qaResult" ref="resultBox" style="max-width:860px;margin:24px auto 0">
      <!-- Agent answer -->
      <div class="panel pad" v-if="mode === 'agent'">
        <div class="row">
          <h3 style="font-size:13px">{{ t('qa.agentAnswerTitle') }}</h3>
          <span v-if="agentResult" class="tag violet">{{ agentResult.agent }} Agent</span>
          <span v-if="agentResult?.framework" class="tag">{{ agentResult.framework }}</span>
          <span v-if="conversationId" class="tag blue" :title="t('qa.inConversationTitle')">{{ t('qa.inConversation') }}</span>
          <button v-if="conversationId" class="btn sm ghost" @click="newConversation">{{ t('qa.newConversation') }}</button>
          <div class="grow"></div>
          <span v-if="agentMs" class="faint" style="font-size:9px">{{ agentMs }} ms</span>
        </div>
        <div v-if="agentLoading" class="faint" style="font-size:10px;margin-top:10px">{{ t('qa.agentThinking') }}</div>
        <MarkdownView v-else-if="agentResult" :content="agentResult.answer" style="margin-top:10px" />
        <div v-else class="empty">{{ t('qa.agentEmpty') }}</div>
      </div>

      <!-- Knowledge answer -->
      <div class="panel pad" v-if="mode === 'knowledge'">
        <div class="row">
          <h3 style="font-size:13px">{{ t('qa.answerTitle') }}</h3>
          <span v-if="evidenceLevel" class="tag" :class="evidenceLevel.cls">{{ evidenceLevel.label }}</span>
          <div class="grow"></div>
          <button v-if="result && !loading" class="btn sm" :disabled="validating" @click="validateCitations">
            {{ validating ? t('qa.validating') : t('qa.validate') }}
          </button>
        </div>
        <div v-if="loading" class="faint" style="font-size:10px;margin-top:10px">{{ t('qa.loadingAnswer') }}</div>
        <div v-else-if="!result" class="empty">{{ t('qa.emptyAsk') }}</div>
        <!-- 答案：问题 → 结论 → 依据 → 纠正，统一走 AnswerCard，不与问答页各写一套 -->
        <AnswerCard v-else
          :question="question" :answer="result.answer"
          :evidence="result.evidence" :knowledge="result.knowledge"
          :correction-existing="correction.existing" :correction-seed="correction.seed"
          @corrected="onCorrected" @supporting-changed="onSupportingChanged" />

        <!-- 拒答不说谎，也不把人晾在原地：这条问题知识库里其实「有东西」，
             只是一条研究提案还没被采纳。说清这一点，并把下一步放在手边——
             否则「没有足够证据」会让人去找一份并不缺失的文档。 -->
        <div v-if="result?.reason === 'candidate_not_accepted'" class="notice violet" style="margin-top:12px">
          {{ t('qa.candidateNotice') }}
          <div class="row" style="margin-top:8px;gap:8px">
            <button class="btn sm primary" @click="router.push('/review')">{{ t('qa.candidateCta') }}</button>
          </div>
        </div>
        <div v-if="result && !result.evidence.length" class="notice violet" style="margin-top:12px">
          {{ t('qa.noEvidenceNotice') }}
        </div>

        <!-- 改完之后答案就过期了，说出来并把手边的下一步给出来 -->
        <div v-if="answerStale" class="notice violet" style="margin-top:12px">
          {{ t('qa.staleNotice') }}
          <div class="row" style="margin-top:8px;gap:8px">
            <button class="btn sm primary" :disabled="loading" @click="reask">{{ t('qa.reask') }}</button>
          </div>
        </div>

        <div v-if="validation" class="sechead" style="margin-top:14px">
          <h3>{{ t('qa.validationTitle') }}</h3>
          <span class="tag" :class="'g-' + validation.grade" style="margin:0">{{ validation.grade }} · {{ Math.round(validation.overall * 100) }}%</span>
        </div>
        <div v-if="validation" class="cval">
          <div v-for="(v, k) in validation.dimensions" :key="k" class="cbar">
            <span class="ck">{{ k }}</span>
            <span class="ct"><i :style="{ width: Math.round((v || 0) * 100) + '%' }" :class="{ low: (v || 0) < 0.5 }"></i></span>
            <span class="cv">{{ v == null ? '—' : Math.round(v * 100) }}</span>
          </div>
          <div v-if="validation.grounding" class="gbox">
            <b :class="validation.grounding.grounded ? 'ok' : 'bad'">
              {{ validation.grounding.grounded ? t('qa.groundingOk') : t('qa.groundingBad') }}
            </b>
            <p v-if="validation.grounding.rationale" class="faint small">{{ validation.grounding.rationale }}</p>
            <ul v-if="validation.grounding.assertions?.length" class="glist">
              <li v-for="(a, i) in validation.grounding.assertions" :key="i" :class="a.supported ? 'ok' : 'bad'">
                <span>{{ a.supported ? '✓' : '✕' }}</span>{{ a.claim }}
                <em v-if="a.citation" class="faint">{{ a.citation }}</em>
              </li>
            </ul>
          </div>
          <div v-if="validation.issues?.length">
            <div v-for="(iss, i) in validation.issues" :key="i" class="notice red" style="margin:6px 0">{{ iss }}</div>
          </div>
        </div>
      </div>

      <div class="grid g2" style="margin-top:16px" v-if="result && mode === 'knowledge'">
        <div>
          <div class="sechead"><h3>{{ t('qa.allEvidence') }} <span class="faint" style="font-weight:400;font-size:9px">· {{ t('qa.sourcesMeta', { n: result.evidence.length }) }}</span></h3></div>
          <div class="panel pad" style="padding:6px">
            <div v-for="(e, i) in result.evidence" :key="i" class="item" style="cursor:pointer" :title="t('anscard.openSourceTitle')" @click="openEvidence(e)">
              <span class="tag blue" style="flex:none">{{ i + 1 }}</span>
              <div class="grow">
                <b>{{ e.title }}</b>
                <p>{{ (e.content || '').slice(0, 100) }}</p>
                <div style="margin-top:4px">
                  <span class="tag">{{ e.source_type || 'Document' }}</span>
                  <span class="tag blue">{{ t('anscard.open') }}</span>
                </div>
              </div>
            </div>
            <EmptyState
              v-if="!result.evidence.length"
              :title="t('qa.noEvidenceTitle')"
              :text="t('qa.noEvidenceText')"
            />
          </div>
          <div class="sechead"><h3>{{ t('qa.boundaryTitle') }}</h3></div>
          <div class="panel pad"><BoundaryList :items="boundary" /></div>
        </div>
        <div>
          <div class="sechead"><h3>{{ t('qa.traceTitle') }}</h3><span class="tag blue" style="margin:0">{{ result.evidence.length }} hits</span></div>
          <div class="panel pad" style="padding:6px">
            <div v-for="(n, m) in trace" :key="m" class="item" style="cursor:default">
              <div class="ico-badge ib-violet"><Sigma :size="14" /></div>
              <div class="grow"><b>{{ m }}</b><p>{{ t('qa.contributes', { n }) }}</p></div>
            </div>
            <div class="item" style="cursor:default"><div class="ico-badge ib-blue"><Search :size="14" /></div><div class="grow"><b>Hybrid Query</b><p>{{ question }}</p></div></div>
          </div>
          <div class="sechead"><h3>{{ t('qa.nextSteps') }}</h3></div>
          <button class="btn" style="width:100%" @click="router.push({ path: '/research', query: { q: question } })"><Telescope :size="12" style="vertical-align:-1px" /> {{ t('qa.researchThis') }}</button>
          <button v-if="store.developerMode" class="btn" style="width:100%;margin-top:8px" @click="switchMode('agent');send()"><Bot :size="12" style="vertical-align:-1px" /> {{ t('qa.agentDeep') }}</button>
        </div>
      </div>

      <div v-if="agentResult && mode === 'agent'" class="grid g2" style="margin-top:16px">
        <div class="panel pad">
          <div class="sechead" style="margin-top:0"><h3>{{ t('qa.whatAgentDid') }}</h3></div>
          <div class="listitem"><b>Agent</b><p>{{ agentResult.agent }} · {{ agentResult.framework || 'AgentScope' }}</p></div>
          <div class="listitem"><b>{{ t('qa.tools') }}</b><p>search_knowledge · get_entity · get_entity_graph · list_skills</p></div>
          <div class="listitem"><b>{{ t('qa.runRecord') }}</b><p>{{ t('qa.runRecordText') }}</p></div>
        </div>
        <div>
          <div class="sechead" style="margin-top:0"><h3>{{ t('qa.nextSteps') }}</h3></div>
          <button class="btn" style="width:100%" @click="recent.length && (runDrawerId = recent[0].id)">{{ t('qa.viewRun') }}</button>
          <button class="btn" style="width:100%;margin-top:8px" @click="router.push('/agent')"><Bot :size="12" style="vertical-align:-1px" /> {{ t('qa.openAgent') }}</button>
          <button class="btn" style="width:100%;margin-top:8px" @click="switchMode('knowledge');send()"><MessageCircle :size="12" style="vertical-align:-1px" /> {{ t('qa.switchKb') }}</button>
        </div>
      </div>

      <div class="sechead"><h3>{{ t('qa.recentTitle') }}</h3><button class="btn sm" @click="loadMeta">{{ t('qa.refresh') }}</button></div>
      <!-- 读不到历史时说"读不到"：一张空表会让人以为"我从来没问过问题" -->
      <LoadBoundary :state="meta.state.value" :loading-text="t('qa.loadingRecent')"
                    :error-text="t('qa.errRecent')"
                    :reload="meta.reload">
        <div class="panel pad" style="padding:6px">
          <DataTable :columns="columns" :rows="recent" clickable @row-click="(r: Run) => (runDrawerId = r.id)">
            <template #cell-task_type="{ row }">
              <span v-if="store.developerMode" class="tag" :class="row.task_type === 'agent' ? 'violet' : 'blue'">{{ row.task_type === 'agent' ? 'Agent · ' + (row.agent_role || '') : t('qa.kbTag') }}</span>
              <span v-else class="tag blue">{{ t('qa.qaTag') }}</span>
            </template>
            <template #cell-question="{ row }"><b>{{ row.summary?.question || (row.task_type === 'agent' ? t('qa.agentConversation') : t('qa.historyRecord')) }}</b></template>
            <template #cell-status="{ row }"><StatusTag :status="row.status" /></template>
            <template #cell-created_at="{ row }">{{ fmtDateTime(row.created_at) }}</template>
          </DataTable>
        </div>
      </LoadBoundary>
    </div>

    <RunDrawer :run-id="runDrawerId" @close="runDrawerId = null" />
  </div>
</template>

<style scoped>
/* v3 页头 + 问答盒 */
.qahead{margin:2vh 0 6px}
.qatitle{font-size:26px;letter-spacing:-.03em;margin:8px 0 6px;font-weight:690}
.qasub{margin:0;color:var(--sub);font-size:12px;line-height:1.7}
.askwrap{max-width:820px;margin:20px auto 0}
.askcard{background:#fff;border:1px solid var(--line);border-radius:17px;padding:16px;box-shadow:var(--shadow)}
.askcard input{width:100%;border:0;outline:none;font-size:14px;padding:6px 2px 14px;background:none;color:var(--text)}
.askbottom{display:flex;justify-content:space-between;align-items:center}
.askbottom span{font-size:10px;color:var(--muted)}
.cval{margin:10px 0 4px;padding:11px;border:1px solid var(--hair);border-radius:11px;background:#fff}
.cbar{display:grid;grid-template-columns:104px 1fr 28px;align-items:center;gap:9px;font-size:10px;color:var(--sub);margin:4px 0}
.ck{font-size:9.5px}
.ct{height:7px;background:#ece8e0;border-radius:99px;overflow:hidden}
.ct i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--accent))}
.ct i.low{background:#d9695a}
.cv{text-align:right;color:var(--faint)}
.gbox{margin-top:10px;padding:9px;border:1px solid var(--hair);border-radius:10px;background:#fbfaf7}
.gbox b.ok{color:#3aa76d}.gbox b.bad{color:#d9695a}
.glist{margin:8px 0 0;padding-left:16px;display:grid;gap:4px}
.glist li{font-size:11px;color:var(--text)}
.glist li.ok{color:#2f7d57}.glist li.bad{color:#c0564b}
.glist li em{font-style:normal;margin-left:6px;font-size:9px}
.g-A{background:#3aa76d;color:#fff}.g-B{background:#5b9bd5;color:#fff}
.g-C{background:#e0a93b;color:#fff}.g-D{background:#d9695a;color:#fff}
/* 答案 → 纠正：一个动作，不是一次跳转 */
.fixline{display:flex;align-items:center;gap:10px;margin-top:12px;padding-top:11px;border-top:1px solid var(--hair)}
/* 支持知识：几张卡，不是一面墙 */
.ksupport{display:grid;gap:9px}
</style>
