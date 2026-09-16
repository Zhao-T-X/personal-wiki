<script setup lang="ts">
import { ref, onMounted } from 'vue'
import {
  getExperimentCorpus,
  runCorpusExperiment,
  getExperimentSnapshots,
  compareExperiments,
  runDocumentExperiment,
  type ExperimentCorpusDoc,
  type ExperimentSnapshot,
  type ExperimentCompare,
} from '../api/experiment'

const corpus = ref<ExperimentCorpusDoc[]>([])
const corpusVersion = ref<string>('')
const report = ref<any>(null)
const running = ref(false)
const error = ref<string>('')

const snapshots = ref<ExperimentSnapshot[]>([])
const beforeId = ref('')
const afterId = ref('')
const compare = ref<ExperimentCompare | null>(null)

const liveContent = ref('')
const liveTitle = ref('')
const liveMsg = ref('')

async function loadCorpus() {
  try {
    const c = await getExperimentCorpus()
    corpus.value = c.documents
    corpusVersion.value = c.version || ''
  } catch (e: any) {
    error.value = String(e?.message || e)
  }
}

async function loadSnapshots() {
  try {
    snapshots.value = await getExperimentSnapshots()
  } catch (e: any) {
    error.value = String(e?.message || e)
  }
}

async function runCorpus() {
  running.value = true
  error.value = ''
  try {
    report.value = await runCorpusExperiment()
  } catch (e: any) {
    error.value = String(e?.message || e)
  } finally {
    running.value = false
  }
}

async function runLive() {
  liveMsg.value = ''
  if (!liveContent.value.trim()) return
  try {
    await runDocumentExperiment(
      liveContent.value,
      liveTitle.value || '实验文档',
      'live',
    )
    liveMsg.value = '已提交真实抽取（需 LLM 已配置）。可在下方快照中选择两次运行做对比。'
    await loadSnapshots()
  } catch (e: any) {
    liveMsg.value = '真实抽取失败：' + String(e?.message || e)
  }
}

async function doCompare() {
  if (!beforeId.value || !afterId.value) return
  compare.value = await compareExperiments(beforeId.value, afterId.value)
}

onMounted(() => {
  loadCorpus()
  loadSnapshots()
})
</script>

<template>
  <div class="exp">
    <h2>抽取对比实验 <span v-if="corpusVersion" class="ver">Golden Corpus {{ corpusVersion }}</span></h2>
    <p class="hint">
      用<strong>真实导入按钮链路</strong>（建文档 → 分块 → 抽取 → 持久化）对同一批文档在「门控关闭(Before)」与「门控开启(After)」下各跑一次，
      比较真实抽取结果。唯一被替换的是 LLM 推断本身（用人工标注的 golden envelope 代替），其余规则均为线上代码。
    </p>

    <section class="panel">
      <h3>Golden Corpus（真实问题文档）</h3>
      <div v-for="d in corpus" :key="d.id" class="doc">
        <div class="docname">{{ d.name }} <span class="src">{{ d.source }}</span></div>
        <div class="row"><span class="lbl ok">应抽：</span><span class="tags">{{ d.expected_entities.join('、') }}</span></div>
        <div class="row"><span class="lbl bad">不应抽：</span><span class="tags bad">{{ d.expected_non_entities.join('、') || '—' }}</span></div>
      </div>
      <button class="btn" :disabled="running" @click="runCorpus">
        {{ running ? '运行中…' : '运行 Golden Corpus Before/After' }}
      </button>
    </section>

    <section v-if="error" class="err">{{ error }}</section>

    <section v-if="report" class="panel">
      <h3>Before / After 汇总</h3>
      <table class="metrics">
        <thead><tr><th>指标</th><th>Before</th><th>After</th></tr></thead>
        <tbody>
          <tr v-for="(v, k) in report.aggregate" :key="k">
            <td>{{ k }}</td>
            <td>{{ v.before }}</td>
            <td>{{ v.after }}</td>
          </tr>
        </tbody>
      </table>

      <h3>逐文档</h3>
      <div v-for="d in report.documents" :key="d.id" class="doc">
        <div class="docname">{{ d.name }}</div>
        <div class="row">
          <span class="lbl">Before</span> 抽 {{ d.before.kept.length }} 个 · Precision {{ d.before.precision }} · Recall {{ d.before.recall }} · 丢弃 {{ d.before.dropped_entities }}
        </div>
        <div class="row">
          <span class="lbl">After</span> 抽 {{ d.after.kept.length }} 个 · Precision {{ d.after.precision }} · Recall {{ d.after.recall }} · 丢弃 {{ d.after.dropped_entities }}
        </div>
        <div v-if="d.after.leaked_non_entities && d.after.leaked_non_entities.length" class="row bad">
          仍泄漏负样本：{{ d.after.leaked_non_entities.join('、') }}
        </div>
        <div v-else class="row ok">负样本已正确排除/隔离</div>
      </div>
    </section>

    <section class="panel">
      <h3>真实按钮抽取（需 LLM 已配置）</h3>
      <input v-model="liveTitle" class="inp" placeholder="文档标题" />
      <textarea v-model="liveContent" class="ta" placeholder="粘贴一份真实文档内容…"></textarea>
      <button class="btn" @click="runLive">用真实导入链路抽取</button>
      <div v-if="liveMsg" class="hint">{{ liveMsg }}</div>
    </section>

    <section class="panel">
      <h3>快照对比</h3>
      <div v-if="snapshots.length" class="snaps">
        <label>Before
          <select v-model="beforeId">
            <option v-for="s in snapshots" :key="s.run_id" :value="s.run_id">
              {{ s.run_id.slice(0, 8) }} · {{ s.view.experiment_tag || '—' }} · 抽 {{ s.view.kept.length }}
            </option>
          </select>
        </label>
        <label>After
          <select v-model="afterId">
            <option v-for="s in snapshots" :key="s.run_id" :value="s.run_id">
              {{ s.run_id.slice(0, 8) }} · {{ s.view.experiment_tag || '—' }} · 抽 {{ s.view.kept.length }}
            </option>
          </select>
        </label>
        <button class="btn" @click="doCompare">对比</button>
      </div>
      <div v-else class="hint">暂无快照。运行上面的实验后会出现在这里。</div>

      <div v-if="compare" class="compare">
        <div class="col">
          <h4>Before 抽取 {{ compare.before.kept.length }} 个</h4>
          <span v-for="n in compare.before.kept" :key="n" class="tag">{{ n }}</span>
        </div>
        <div class="col">
          <h4>After 抽取 {{ compare.after.kept.length }} 个</h4>
          <span v-for="n in compare.after.kept" :key="n" class="tag">{{ n }}</span>
        </div>
        <div class="col">
          <h4>移除（噪声下降）</h4>
          <span v-for="n in compare.removed" :key="n" class="tag bad">{{ n }}</span>
          <span v-if="!compare.removed.length" class="hint">无</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.exp { padding: 16px; max-width: 960px; margin: 0 auto; }
.ver { font-size: 12px; color: #888; margin-left: 8px; }
.hint { color: #666; font-size: 13px; line-height: 1.6; }
.panel { border: 1px solid #e3e3e8; border-radius: 8px; padding: 12px 14px; margin: 14px 0; }
.doc { border-bottom: 1px dashed #eee; padding: 8px 0; }
.docname { font-weight: 600; margin-bottom: 4px; }
.src { font-size: 12px; color: #999; font-weight: 400; margin-left: 6px; }
.row { font-size: 13px; margin: 2px 0; }
.lbl { display: inline-block; min-width: 56px; color: #555; }
.lbl.ok, .ok { color: #1a7f37; }
.lbl.bad, .bad { color: #b42318; }
.tags { color: #333; }
.tags.bad { color: #b42318; }
.metrics { border-collapse: collapse; width: 100%; margin: 8px 0; }
.metrics th, .metrics td { border: 1px solid #e3e3e8; padding: 4px 8px; text-align: left; }
.btn { background: #2f6feb; color: #fff; border: 0; border-radius: 6px; padding: 6px 12px; cursor: pointer; }
.btn:disabled { opacity: .6; }
.inp, .ta { width: 100%; box-sizing: border-box; margin: 6px 0; padding: 6px; border: 1px solid #ccc; border-radius: 6px; }
.ta { min-height: 120px; font-family: monospace; }
.err { color: #b42318; padding: 8px; }
.snaps label { margin-right: 12px; font-size: 13px; }
.compare { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px; margin-top: 10px; }
.tag { display: inline-block; background: #eef2ff; color: #2f6feb; border-radius: 4px; padding: 2px 6px; margin: 2px; font-size: 12px; }
.tag.bad { background: #fde8e8; color: #b42318; }
</style>
