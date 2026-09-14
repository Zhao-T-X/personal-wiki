<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import PageHead from '../components/PageHead.vue'
import EmptyState from '../components/EmptyState.vue'
import { useAppStore } from '../stores/app'
import { fmtDateTime } from '../utils/time'
import { getEvalBaseline, getEvalRun, listEvalRuns, pinEvalBaseline, runEval } from '../api/eval'
import type { EvalRunDetail, EvalRunListItem, EvalSuite, EvalSummary } from '../api/types'

const store = useAppStore()

/* ---- suite metadata ---- */
const SUITES: { id: EvalSuite; label: string; icon: string }[] = [
  { id: 'extraction', label: '抽取评测', icon: '✦' },
  { id: 'qa', label: '问答评测', icon: '◎' },
  { id: 'retrieval', label: '检索评测', icon: '◇' },
]
const SUITE_LABEL: Record<EvalSuite, string> = {
  extraction: '抽取评测', qa: '问答评测', retrieval: '检索评测',
}

/** Metric display config. `higherBetter:false` means lower is an improvement. */
const METRIC_META: Record<string, { label: string; higherBetter: boolean }> = {
  entity_precision: { label: '实体精确率', higherBetter: true },
  entity_recall: { label: '实体召回率', higherBetter: true },
  claim_precision: { label: '主张精确率', higherBetter: true },
  claim_recall: { label: '主张召回率', higherBetter: true },
  evidence_accuracy: { label: '证据准确率', higherBetter: true },
  ontology_violation_rate: { label: '本体违规率', higherBetter: false },
  citation_coverage: { label: '引用覆盖率', higherBetter: true },
  groundedness: { label: '依据度', higherBetter: true },
  answer_correctness: { label: '回答正确性', higherBetter: true },
  unknown_answer_hallucination_rate: { label: '未知回答幻觉率', higherBetter: false },
  recall_at_5: { label: '召回@5', higherBetter: true },
  recall_at_10: { label: '召回@10', higherBetter: true },
  precision_at_5: { label: '精确率@5', higherBetter: true },
  mrr: { label: 'MRR', higherBetter: true },
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
    store.toast(e?.message ? `加载失败：${e.message}` : '加载评测明细失败')
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
    store.toast(`已运行 ${SUITE_LABEL[suite]}`)
  } catch (e: any) {
    store.toast(e?.message ? `运行失败：${e.message}` : '评测运行失败')
  } finally {
    running.value = null
  }
}

async function pinBaseline() {
  if (!current.value) return
  pinning.value = true
  try {
    await pinEvalBaseline({ suite: current.value.suite, run_id: current.value.run_id })
    store.toast(`已将 ${SUITE_LABEL[current.value.suite]} 设为基线`)
  } catch (e: any) {
    store.toast(e?.message ? `设基线失败：${e.message}` : '设置基线失败')
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
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (v == null) return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
function caseTitle(c: Record<string, unknown>, i: number) {
  return (typeof c.case_id === 'string' || typeof c.case_id === 'number'
    ? `用例 ${c.case_id}`
    : `用例 ${i + 1}`)
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
    <PageHead title="评测看板" subtitle="运行抽取 / 问答 / 检索评测，对比基线指标，逐用例审查明细" />

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
          {{ running === s.id ? `运行中 · ${s.label}…` : `运行 ${s.label}` }}
        </button>
        <div class="grow"></div>
        <button class="btn ghost" :disabled="loadingRuns" @click="loadRuns">↻ 刷新历史</button>
      </div>
    </div>

    <!-- metric cards -->
    <div class="sechead">
      <h3>指标概览
        <span v-if="currentSuite" class="faint" style="font-weight:400;font-size:10px">
          · {{ SUITE_LABEL[currentSuite] }}
          <template v-if="current">· {{ fmtDateTime(current.created_at) }}</template>
        </span>
      </h3>
      <button v-if="current" class="btn sm ghost" :disabled="pinning" @click="pinBaseline">
        {{ pinning ? '设置中…' : '设为基线' }}
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
          <span class="vs">vs 基线</span>
        </div>
        <div class="mbase faint" v-else>无基线可对比</div>
      </div>
    </div>
    <EmptyState
      v-else
      title="暂无指标"
      text="点击上方按钮运行评测，或在后端就绪后加载最近一次运行，即可看到聚合指标与基线对比。"
    />

    <!-- run history -->
    <div class="sechead"><h3>运行历史</h3><span class="tag" style="margin:0">{{ runs.length }}</span></div>
    <div class="panel pad" style="padding:6px">
      <div v-if="!runs.length" class="empty">还没有评测运行记录。</div>
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
          <p class="faint small">{{ fmtDateTime(r.created_at) }} · {{ casesOf(r) }} 项指标</p>
        </div>
        <span v-if="loadingRun && current && current.run_id === r.run_id" class="faint small">加载中…</span>
      </div>
    </div>

    <!-- case details -->
    <div class="sechead" v-if="current">
      <h3>逐用例明细
        <span class="faint" style="font-weight:400;font-size:10px">· {{ caseList.length }} 条</span>
      </h3>
    </div>
    <div v-if="current" class="panel pad" style="padding:6px">
      <div v-if="!caseList.length" class="empty">
        本次运行的报告中没有逐用例明细（report.case_details 为空或未提供）。
      </div>
      <div v-for="(c, i) in caseList" :key="i" class="case">
        <div class="item" style="border-radius:0" @click="toggleCase(i)">
          <span class="tag" style="flex:none">{{ i + 1 }}</span>
          <div class="grow">
            <b>{{ caseTitle(c, i) }}</b>
            <p class="faint small">点击{{ expanded.has(i) ? '收起' : '展开' }}字段</p>
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
.metric:after{content:"";position:absolute;width:70px;height:70px;right:-22px;top:-26px;border-radius:50%;background:radial-gradient(circle,rgba(91,124,255,.12),transparent 67%)}
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
.cbody{padding:4px 14px 12px;background:#fbfcff}
.crow{display:grid;grid-template-columns:128px 1fr;gap:10px;font-size:10.5px;padding:5px 0;border-bottom:1px dashed var(--hair)}
.crow:last-child{border-bottom:0}
.ck{color:var(--sub)}
.cv{color:var(--text);font-variant-numeric:tabular-nums;word-break:break-word}
.small{font-size:9.5px}
</style>
