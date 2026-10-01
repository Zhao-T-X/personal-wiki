<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import PageHead from '../components/PageHead.vue'
import EmptyState from '../components/EmptyState.vue'
import { useAppStore } from '../stores/app'
import { fmtDateTime } from '../utils/time'
import { getEvalBaseline, getEvalRun, listEvalRuns, pinEvalBaseline, runEval } from '../api/eval'
import type { EvalRunDetail, EvalRunListItem, EvalSuite, EvalSummary } from '../api/types'

const { t } = useI18n()
const store = useAppStore()

/* ---- suite metadata ---- */
const SUITES: { id: EvalSuite; label: string; icon: string }[] = [
  { id: 'extraction', label: t('eval.suiteExtraction'), icon: '✦' },
  { id: 'qa', label: t('eval.suiteQa'), icon: '◎' },
  { id: 'retrieval', label: t('eval.suiteRetrieval'), icon: '◇' },
]
const SUITE_LABEL: Record<EvalSuite, string> = {
  extraction: t('eval.suiteExtraction'), qa: t('eval.suiteQa'), retrieval: t('eval.suiteRetrieval'),
}

/** Metric display config. `higherBetter:false` means lower is an improvement. */
const METRIC_META: Record<string, { label: string; higherBetter: boolean }> = {
  entity_precision: { label: t('eval.mEntityPrecision'), higherBetter: true },
  entity_recall: { label: t('eval.mEntityRecall'), higherBetter: true },
  claim_precision: { label: t('eval.mClaimPrecision'), higherBetter: true },
  claim_recall: { label: t('eval.mClaimRecall'), higherBetter: true },
  evidence_accuracy: { label: t('eval.mEvidenceAccuracy'), higherBetter: true },
  ontology_violation_rate: { label: t('eval.mOntologyViolation'), higherBetter: false },
  citation_coverage: { label: t('eval.mCitationCoverage'), higherBetter: true },
  groundedness: { label: t('eval.mGroundedness'), higherBetter: true },
  answer_correctness: { label: t('eval.mAnswerCorrectness'), higherBetter: true },
  unknown_answer_hallucination_rate: { label: t('eval.mHallucination'), higherBetter: false },
  recall_at_5: { label: t('eval.mRecall5'), higherBetter: true },
  recall_at_10: { label: t('eval.mRecall10'), higherBetter: true },
  precision_at_5: { label: t('eval.mPrecision5'), higherBetter: true },
  mrr: { label: t('eval.mMrr'), higherBetter: true },
}

/* ---- state ---- */
const runs = ref<EvalRunListItem[]>([])
const current = ref<EvalRunDetail | null>(null)
const baseline = ref<Record<string, number> | null>(null)
const running = ref<EvalSuite | null>(null)
const loadingRuns = ref(false)
const loadingRun = ref(false)
const expanded = ref<Set<number>>(new Set())
const pinning = ref(false)

const currentSuite = computed<EvalSuite | null>(() => current.value?.suite ?? null)

/* ---- metric cards (value + baseline delta) ---- */
interface MetricCard {
  key: string
  label: string
  value: number
  baseline: number | null
  delta: number | null
  trend: 'up' | 'down' | 'flat'
  improved: boolean | null
  higherBetter: boolean
}
const metricCards = computed<MetricCard[]>(() => {
  const s = current.value?.summary
  if (!s) return []
  const base = baseline.value || {}
  const eps = 0.0005
  return Object.keys(s).map((key) => {
    const val = Number(s[key])
    const b = base[key] != null ? Number(base[key]) : null
    const delta = b != null && !Number.isNaN(b) ? val - b : null
    let trend: 'up' | 'down' | 'flat' = 'flat'
    if (delta != null) {
      if (delta > eps) trend = 'up'
      else if (delta < -eps) trend = 'down'
    }
    const meta = METRIC_META[key] || { label: key, higherBetter: true }
    const improved =
      trend === 'flat' ? null : trend === 'up' ? meta.higherBetter : !meta.higherBetter
    return { key, label: meta.label, value: val, baseline: b, delta, trend, improved, higherBetter: meta.higherBetter }
  })
})

const caseList = computed<Record<string, unknown>[]>(() => {
  const details = current.value?.report?.case_details
  return Array.isArray(details) ? (details as Record<string, unknown>[]) : []
})

/* ---- actions ---- */
function casesOf(run: { summary: EvalSummary }) { return Object.keys(run.summary).length }

async function loadRuns() {
  loadingRuns.value = true
  try {
    const list = await listEvalRuns(20)
    runs.value = list || []
  } catch {
    runs.value = []
  } finally {
    loadingRuns.value = false
  }
}

async function loadBaseline(suite: EvalSuite) {
  try {
    const b = await getEvalBaseline(suite)
    baseline.value = (b && b.metrics) || null
  } catch {
    baseline.value = null
  }
}

async function selectRun(runId: string) {
  loadingRun.value = true
  try {
    const detail = await getEvalRun(runId)
    current.value = detail
  } catch (e: any) {
    store.toast(e?.message ? t('eval.loadFailMsg', { msg: e.message }) : t('eval.loadFail'))
  } finally {
    loadingRun.value = false
  }
}

async function startRun(suite: EvalSuite) {
  if (running.value) return
  running.value = suite
  try {
    const detail = await runEval(suite)
    current.value = detail
    // refresh history, newest first
    await loadRuns()
    store.toast(t('eval.ranSuite', { suite: SUITE_LABEL[suite] }))
  } catch (e: any) {
    store.toast(e?.message ? t('eval.runFailMsg', { msg: e.message }) : t('eval.runFail'))
  } finally {
    running.value = null
  }
}

async function pinBaseline() {
  if (!current.value) return
  pinning.value = true
  try {
    await pinEvalBaseline({ suite: current.value.suite, run_id: current.value.run_id })
    store.toast(t('eval.baselineSet', { suite: SUITE_LABEL[current.value.suite] }))
  } catch (e: any) {
    store.toast(e?.message ? t('eval.pinFailMsg', { msg: e.message }) : t('eval.pinFail'))
  } finally {
    pinning.value = false
  }
}

function toggleCase(i: number) {
  const next = new Set(expanded.value)
  next.has(i) ? next.delete(i) : next.add(i)
  expanded.value = next
}

/* ---- formatting ---- */
function pct(v: number) {
  if (!Number.isFinite(v)) return '—'
  return (v * 100).toFixed(1) + '%'
}
function deltaTxt(d: number | null) {
  if (d == null) return '—'
  const s = d > 0 ? '+' : ''
  return s + (d * 100).toFixed(1)
}
function fmtVal(v: unknown): string {
  if (typeof v === 'number') return (v * 100).toFixed(1) + '%'
  if (typeof v === 'boolean') return v ? t('eval.yes') : t('eval.no')
  if (v == null) return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
function caseTitle(c: Record<string, unknown>, i: number) {
  return (typeof c.case_id === 'string' || typeof c.case_id === 'number'
    ? t('eval.caseId', { id: c.case_id })
    : t('eval.caseId', { id: i + 1 }))
}

watch(currentSuite, (s) => { if (s) loadBaseline(s) })

onMounted(async () => {
  await loadRuns()
  const latest = runs.value[0]
  if (latest) {
    await selectRun(latest.run_id)
  } else if (currentSuite.value) {
    await loadBaseline(currentSuite.value)
  }
})
</script>

<template>
  <div class="page">
    <PageHead :title="t('eval.title')" :subtitle="t('eval.subtitle')" />

    <!-- run controls -->
    <div class="panel pad" style="margin-bottom:16px">
      <div class="row" style="flex-wrap:wrap;gap:10px">
        <button
          v-for="s in SUITES"
          :key="s.id"
          class="btn"
          :class="{ primary: running === s.id }"
          :disabled="!!running"
          @click="startRun(s.id)"
        >
          <span class="ric">{{ s.icon }}</span>
          {{ running === s.id ? t('eval.running', { suite: s.label }) : t('eval.runSuite', { suite: s.label }) }}
        </button>
        <div class="grow"></div>
        <button class="btn ghost" :disabled="loadingRuns" @click="loadRuns">{{ t('eval.refreshHistory') }}</button>
      </div>
    </div>

    <!-- metric cards -->
    <div class="sechead">
      <h3>{{ t('eval.overview') }}
        <span v-if="currentSuite" class="faint" style="font-weight:400;font-size:10px">
          · {{ SUITE_LABEL[currentSuite] }}
          <template v-if="current">· {{ fmtDateTime(current.created_at) }}</template>
        </span>
      </h3>
      <button v-if="current" class="btn sm ghost" :disabled="pinning" @click="pinBaseline">
        {{ pinning ? t('eval.setting') : t('eval.setBaseline') }}
      </button>
    </div>

    <div v-if="metricCards.length" class="metrics">
      <div v-for="m in metricCards" :key="m.key" class="metric">
        <div class="mlabel">{{ m.label }}</div>
        <div class="mval">{{ pct(m.value) }}</div>
        <div class="mbase" v-if="m.delta != null">
          <span class="arrow" :class="m.improved === null ? 'flat' : m.improved ? 'good' : 'bad'">
            {{ m.trend === 'up' ? '▲' : m.trend === 'down' ? '▼' : '＝' }}
          </span>
          <span class="diff" :class="m.improved === null ? 'flat' : m.improved ? 'good' : 'bad'">
            {{ deltaTxt(m.delta) }}
          </span>
          <span class="vs">{{ t('eval.vsBaseline') }}</span>
        </div>
        <div class="mbase faint" v-else>{{ t('eval.noBaseline') }}</div>
      </div>
    </div>
    <EmptyState
      v-else
      :title="t('eval.noMetrics')"
      :text="t('eval.noMetricsBody')"
    />

    <!-- run history -->
    <div class="sechead"><h3>{{ t('eval.runHistory') }}</h3><span class="tag" style="margin:0">{{ runs.length }}</span></div>
    <div class="panel pad" style="padding:6px">
      <div v-if="!runs.length" class="empty">{{ t('eval.noRuns') }}</div>
      <div
        v-for="r in runs"
        :key="r.run_id"
        class="item"
        :class="{ sel: current && current.run_id === r.run_id }"
        @click="selectRun(r.run_id)"
      >
        <span class="tag blue" style="flex:none">{{ SUITE_LABEL[r.suite] }}</span>
        <div class="grow">
          <b>{{ r.run_id.slice(0, 12) }}</b>
          <p class="faint small">{{ fmtDateTime(r.created_at) }} · {{ casesOf(r) }} {{ t('eval.metricUnit') }}</p>
        </div>
        <span v-if="loadingRun && current && current.run_id === r.run_id" class="faint small">{{ t('eval.loadDetail') }}</span>
      </div>
    </div>

    <!-- case details -->
    <div class="sechead" v-if="current">
      <h3>{{ t('eval.caseDetails') }}
        <span class="faint" style="font-weight:400;font-size:10px">· {{ caseList.length }} {{ t('eval.caseUnit') }}</span>
      </h3>
    </div>
    <div v-if="current" class="panel pad" style="padding:6px">
      <div v-if="!caseList.length" class="empty">
        {{ t('eval.noCaseDetails') }}
      </div>
      <div v-for="(c, i) in caseList" :key="i" class="case">
        <div class="item" style="border-radius:0" @click="toggleCase(i)">
          <span class="tag" style="flex:none">{{ i + 1 }}</span>
          <div class="grow">
            <b>{{ caseTitle(c, i) }}</b>
            <p class="faint small">{{ t('eval.tapTo', { action: expanded.has(i) ? t('eval.collapse') : t('eval.expand') }) }}</p>
          </div>
          <span class="faint">{{ expanded.has(i) ? '▾' : '▸' }}</span>
        </div>
        <div v-if="expanded.has(i)" class="cbody">
          <div v-for="(v, k) in c" :key="k" class="crow">
            <span class="ck">{{ METRIC_META[k]?.label || k }}</span>
            <span class="cv">{{ fmtVal(v) }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.metrics{display:grid;grid-template-columns:repeat(auto-fill,minmax(158px,1fr));gap:12px}
.metric{background:rgba(255,255,255,.96);border:1px solid var(--line);border-radius:14px;padding:13px 14px;box-shadow:var(--shadow);position:relative;overflow:hidden}
.metric:after{content:"";position:absolute;width:70px;height:70px;right:-22px;top:-26px;border-radius:50%;background:radial-gradient(circle,rgba(156,90,67,.10),transparent 67%)}
.mlabel{font-size:9.5px;color:var(--sub);font-weight:600}
.mval{font-size:21px;font-weight:800;margin-top:5px;letter-spacing:-.03em;font-variant-numeric:tabular-nums}
.mbase{display:flex;align-items:center;gap:6px;margin-top:7px;font-size:10px}
.arrow{font-size:11px}
.diff{font-weight:700;font-variant-numeric:tabular-nums}
.vs{color:var(--faint);font-size:8.5px}
.good{color:var(--mint)}
.bad{color:var(--red)}
.flat{color:var(--faint)}
.ric{margin-right:6px}
.item.sel{background:linear-gradient(90deg,var(--tint-blue),var(--tint-violet));box-shadow:inset 0 0 0 1px #dfe6fb}
.case{border-bottom:1px solid var(--hair)}
.case:last-child{border-bottom:0}
.cbody{padding:4px 14px 12px;background:#fbfaf7}
.crow{display:grid;grid-template-columns:128px 1fr;gap:10px;font-size:10.5px;padding:5px 0;border-bottom:1px dashed var(--hair)}
.crow:last-child{border-bottom:0}
.ck{color:var(--sub)}
.cv{color:var(--text);font-variant-numeric:tabular-nums;word-break:break-word}
.small{font-size:9.5px}
</style>
