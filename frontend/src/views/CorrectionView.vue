<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, post } from '../api/client'
import PageHead from '../components/PageHead.vue'
import { useAppStore } from '../stores/app'

const router = useRouter()
const store = useAppStore()

const text = ref('')
const plan = ref<any>(null)
const planQuality = ref<any>(null)
const analyzing = ref(false)
const applying = ref(false)
const result = ref<any>(null)
const error = ref('')
const manual = ref(false)

// Manual entry is the no-model fallback: the /apply endpoint needs only
// subject/predicate/object, so a correction still works without an LLM.
const m = reactive({ subject: '', predicate: '', object: '', relationship: '', related_claim_id: '' })
const history = ref<any[]>([])

const DIM_LABELS: Record<string, string> = {
  schema: 'Schema', evidence: 'Evidence', quote: 'Quote',
  entity_resolution: '实体解析', predicate: '谓词合法性',
  conflict: '冲突', provenance: '溯源',
}

const VERDICT_LABELS: Record<string, string> = {
  supported: '已支持',
  contradicted: '存在矛盾',
  unrelated: '与现有知识无关',
  uncertain: '无法验证',
}

async function loadHistory() {
  try {
    const ops = await api<any[]>('/api/knowledge/operations?kind=CORRECT&limit=20')
    history.value = ops || []
  } catch { history.value = [] }
}

async function analyze() {
  if (!text.value.trim()) { store.toast('请先输入一句要纠正的知识'); return }
  error.value = ''
  plan.value = null
  planQuality.value = null
  analyzing.value = true
  try {
    plan.value = await post<any>('/api/knowledge/corrections', { text: text.value.trim() })
    if (plan.value?.related_claim_id) {
      try {
        planQuality.value = await api<any>('/api/knowledge/claims/' + plan.value.related_claim_id + '/quality')
      } catch { planQuality.value = null }
    }
  } catch (e: any) {
    error.value = e.message
  } finally {
    analyzing.value = false
  }
}

async function apply(payload: any) {
  applying.value = true
  error.value = ''
  try {
    result.value = await post<any>('/api/knowledge/corrections/apply', payload)
    store.toast('已应用纠正 · 已生成新 Claim 并写入审计')
    await loadHistory()
    plan.value = null
  } catch (e: any) {
    error.value = e.message
  } finally {
    applying.value = false
  }
}

function confirm() {
  if (!plan.value) return
  const it = plan.value.intent || {}
  apply({
    text: text.value.trim(),
    subject: it.subject, predicate: it.predicate, object: it.object || '',
    polarity: it.polarity || 'positive', confidence: it.confidence ?? null,
    relationship: plan.value.relationship,
    related_claim_id: plan.value.related_claim_id,
    apply_supersede: plan.value.apply_supersede || false,
  })
}

function applyManual() {
  if (!m.subject.trim() || !m.predicate.trim()) { store.toast('subject 与 predicate 必填'); return }
  apply({
    text: text.value.trim() || `${m.subject} ${m.predicate} ${m.object}`.trim(),
    subject: m.subject.trim(), predicate: m.predicate.trim(), object: m.object.trim(),
    polarity: 'positive', confidence: null,
    relationship: m.relationship || null, related_claim_id: m.related_claim_id || null,
    apply_supersede: m.relationship === 'supersedes',
  })
}

function openClaim(id?: string) { if (id) router.push('/knowledge/claim/' + id) }

onMounted(loadHistory)
</script>

<template>
  <div class="page">
    <PageHead title="一句话纠正" subtitle="把一句新的事实变成一次真正的知识编辑：分析 → 确认 → 生成新 Claim → Evolution Relation → 审计">
      <template #actions>
        <button class="btn ghost" :class="{ primary: manual }" @click="manual = !manual">
          {{ manual ? '用句子分析' : '手动填写（无需模型）' }}
        </button>
      </template>
    </PageHead>

    <div class="panel pad">
      <label class="lbl">纠正内容（一句话自然语言）</label>
      <textarea v-model="text" class="ta" rows="3" placeholder="例如：OpenAI 是一家营利性公司。" />
      <p class="hint">系统会解析这句话、找到受影响的 Claim，并展示修改计划供你确认。</p>
      <div class="row" style="margin-top:6px">
        <button class="btn primary" :disabled="analyzing" @click="analyze">
          {{ analyzing ? '分析中…' : '分析并生成计划' }}
        </button>
        <span v-if="!manual" class="faint small">需要已配置的语言模型来语义解析；若无模型，请切到“手动填写”。</span>
      </div>

      <div v-if="error" class="notice red" style="margin-top:12px">{{ error }}</div>

      <div v-if="plan" class="plan" style="margin-top:14px">
        <div class="sechead"><h3>修改计划</h3><span class="tag blue" style="margin:0">{{ plan.relationship }}</span></div>
        <p class="summary">{{ plan.summary }}</p>

        <div v-if="plan.verification" class="verification" :class="plan.verification.verdict">
          <div class="vhead">
            <span class="tag" :class="plan.verification.verdict">{{ VERDICT_LABELS[plan.verification.verdict] || plan.verification.verdict }}</span>
            <span class="faint">置信度 {{ Math.round((plan.verification.confidence || 0) * 100) }}%</span>
          </div>
          <p class="rationale">{{ plan.verification.rationale }}</p>
        </div>
        <div v-if="plan.verification?.verdict === 'contradicted'" class="notice red" style="margin-top:8px">
          现有知识可能已包含不同事实。继续应用将创建一条 contradicts 关系；原 Claim 不会被删除，你之后可在 Claim Relations 中改为 supersedes。
        </div>

        <div v-if="plan.blocked" class="notice red" style="margin-top:8px">
          谓词未能映射到受控词表，系统不会创建新谓词，也不会写入这条知识。请改用已注册的谓词来表达该关系。
        </div>

        <div class="chips">
          <span class="chip"><i>subject</i>{{ plan.intent?.subject }}</span>
          <span class="chip"><i>predicate</i>{{ plan.intent?.predicate || plan.intent?.predicate_candidate || '—' }}</span>
          <span class="chip"><i>object</i>{{ plan.intent?.object || '—' }}</span>
          <span v-if="plan.intent?.temporal_signal" class="chip"><i>temporal</i>{{ plan.intent.temporal_signal }}</span>
        </div>

        <div v-if="planQuality" class="quality">
          <div class="qhead">
            <span>受影响 Claim 质量</span>
            <b class="grade" :class="'g-' + planQuality.grade">{{ planQuality.grade }}</b>
            <span class="faint">{{ Math.round(planQuality.overall * 100) }}%</span>
          </div>
          <div class="bars">
            <div v-for="(v, k) in planQuality.dimensions" :key="k" class="bar">
              <span class="bl">{{ DIM_LABELS[k] || k }}</span>
              <span class="bt"><i :style="{ width: Math.round(v * 100) + '%' }" :class="{ low: v < 0.5 }"></i></span>
              <span class="bv">{{ Math.round(v * 100) }}</span>
            </div>
          </div>
          <div v-if="planQuality.flags?.length" class="flags">
            <span v-for="f in planQuality.flags" :key="f" class="flag">{{ f }}</span>
          </div>
        </div>

        <div class="row" style="margin-top:12px">
          <button class="btn primary" :disabled="applying || plan.blocked" @click="confirm">
            {{ plan.blocked ? '不可应用（谓词未注册）' : (applying ? '执行中…' : '确认并应用') }}
          </button>
          <button class="btn ghost" @click="plan = null">取消</button>
        </div>
      </div>

      <div v-if="manual" class="manual">
        <hr class="hairline" style="margin:16px 0" />
        <div class="sechead"><h3>手动填写（直接调用 CORRECT 操作，不依赖模型）</h3></div>
        <div class="grid3">
          <input v-model="m.subject" placeholder="subject（实体名）" />
          <input v-model="m.predicate" placeholder="predicate（如 defined_as）" />
          <input v-model="m.object" placeholder="object（可选）" />
          <input v-model="m.relationship" placeholder="relationship（supersedes 等，可选）" />
          <input v-model="m.related_claim_id" placeholder="受影响 Claim ID（可选）" />
        </div>
        <button class="btn primary" style="margin-top:10px" :disabled="applying" @click="applyManual">
          {{ applying ? '执行中…' : '直接应用' }}
        </button>
      </div>
    </div>

    <div v-if="result" class="panel pad" style="margin-top:16px">
      <div class="sechead"><h3>已生成</h3><span class="tag green" style="margin:0">CORRECT</span></div>
      <div class="out">
        <span>新 Claim：<b class="link" @click="openClaim(result.claim_id)">{{ result.claim_id }}</b></span>
        <span v-if="result.superseded_claim_id">旧 Claim 已 <b>superseded</b>：<b class="link" @click="openClaim(result.superseded_claim_id)">{{ result.superseded_claim_id }}</b></span>
        <span>操作 ID：<code>{{ result.operation_id }}</code></span>
      </div>
    </div>

    <div class="panel pad" style="margin-top:16px">
      <div class="sechead"><h3>纠正审计（最近 CORRECT 操作）</h3><span class="tag" style="margin:0">{{ history.length }}</span></div>
      <div v-if="!history.length" class="empty">暂无纠正记录。</div>
      <div v-for="op in history" :key="op.id" class="item">
        <span class="tag blue">CORRECT</span>
        <div class="grow">
          <b>{{ op.reason || op.actor || 'user' }}</b>
          <p class="faint small">{{ op.created_at }}</p>
        </div>
        <code class="cid">{{ op.id.slice(0, 8) }}</code>
      </div>
    </div>
  </div>
</template>

<style scoped>
.lbl{font-size:10px;color:var(--sub);font-weight:600;letter-spacing:.04em}
.ta{width:100%;margin-top:7px;border:1px solid var(--hair);border-radius:11px;padding:10px 12px;font:inherit;font-size:12.5px;resize:vertical;background:#fff;color:var(--text)}
.hint{font-size:10.5px;color:var(--faint);margin:7px 0 0}
.small{font-size:10px}
.summary{margin:8px 0;font-size:12.5px;color:var(--text)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:#f2f5fc;border:1px solid var(--hair);border-radius:8px;padding:4px 9px;font-size:11px;color:var(--text)}
.chip i{font-style:normal;color:var(--faint);margin-right:5px;font-size:9px;text-transform:uppercase}
.plan{padding:12px;border:1px solid var(--hair);border-radius:12px;background:#fcfdff}
.quality{margin-top:12px;padding:11px;border:1px solid var(--hair);border-radius:11px;background:#fff}
.qhead{display:flex;align-items:center;gap:9px;font-size:11px;color:var(--sub)}
.grade{font-size:13px;width:22px;height:22px;border-radius:7px;display:grid;place-items:center;color:#fff}
.g-A{background:#3aa76d}.g-B{background:#5b9bd5}.g-C{background:#e0a93b}.g-D{background:#d9695a}
.bars{margin-top:9px;display:grid;gap:5px}
.bar{display:grid;grid-template-columns:84px 1fr 30px;align-items:center;gap:8px;font-size:10px;color:var(--sub)}
.bt{height:7px;background:#eef1f7;border-radius:99px;overflow:hidden}
.bt i{display:block;height:100%;background:linear-gradient(90deg,#5b7cff,#8b67f7)}
.bt i.low{background:#d9695a}
.bv{text-align:right;color:var(--faint)}
.flags{margin-top:9px;display:flex;flex-wrap:wrap;gap:5px}
.flag{font-size:9px;background:#fbeceb;color:#c0564b;border-radius:99px;padding:2px 8px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}
.grid3 input{border:1px solid var(--hair);border-radius:9px;padding:8px 10px;font:inherit;font-size:11.5px;background:#fff;color:var(--text)}
.out{display:grid;gap:6px;font-size:12px;margin-top:8px}
.out code,.cid{font-size:10px;background:#f2f5fc;border-radius:6px;padding:1px 6px;color:var(--sub)}
.link{color:#4a63e8;cursor:pointer;text-decoration:underline}
.item{display:flex;align-items:center;gap:10px;padding:8px 4px;border-bottom:1px solid var(--hair)}
.item:last-child{border-bottom:none}
.item p{margin:2px 0 0}
.verification{margin-top:10px;padding:10px 12px;border-radius:10px;background:#f7f8fb;border:1px solid var(--hair)}
.verification .vhead{display:flex;align-items:center;gap:10px;font-size:11px;margin-bottom:6px}
.verification .rationale{font-size:11.5px;color:var(--sub);margin:0;line-height:1.5}
.verification.contradicted{background:#fdf2f2;border-color:#f5d0d0}
.verification.supported{background:#f1f8f4;border-color:#cfe9d7}
.verification.uncertain{background:#fff9ed;border-color:#f5e5c3}
.tag.contradicted{background:#d9695a;color:#fff}
.tag.supported{background:#3aa76d;color:#fff}
.tag.uncertain{background:#e0a93b;color:#fff}
.tag.unrelated{background:#9aa3b2;color:#fff}
</style>
