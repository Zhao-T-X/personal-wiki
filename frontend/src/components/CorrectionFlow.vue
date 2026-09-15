<script setup lang="ts">
/* 纠正流程：一句话 → 分析 → 展示「发现已有知识 / 建议 / 旧知识会怎样」→ 确认 → 更新。
 *
 * 它是「QA → 这条有问题」和「知识详情 → 纠正」共用的那一份实现。
 * 后台仍然叫 Correction（Operation / Claim / Evolution），
 * 前台只说：发现了什么、要改成什么、旧的那条会去哪里。
 *
 * 计划接口只规划、不落库（POST /api/knowledge/corrections），
 * 用户看到建议后再走 /corrections/apply —— 不存在「AI 静默改知识」。
 */
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, post } from '../api/client'
import { useAppStore } from '../stores/app'
import { statusStyle } from '../utils/status'
import { reportIssues } from '../utils/issues'

const props = withDefaults(defineProps<{
  /** 内联用（答案卡片 / 知识卡片）：只留结论与两个按钮。 */
  compact?: boolean
  /** 预填内容，例如把刚问到的问题带进来。 */
  seed?: string
  /** 确认按钮文案。 */
  confirmLabel?: string
}>(), {
  compact: false,
  seed: '',
  confirmLabel: '确认修改',
})

const emit = defineEmits<{ (e: 'applied', result: any): void }>()

const router = useRouter()
const store = useAppStore()

const text = ref(props.seed)
const plan = ref<any>(null)
const planQuality = ref<any>(null)
const analyzing = ref(false)
const applying = ref(false)
const error = ref('')

const DIM_LABELS: Record<string, string> = {
  schema: 'Schema', evidence: 'Evidence', quote: 'Quote',
  entity_resolution: '实体解析', predicate: '谓词合法性',
  conflict: '冲突', provenance: '溯源',
}
const VERDICT_LABELS: Record<string, string> = {
  supported: '与现有知识一致',
  contradicted: '与现有知识矛盾',
  unrelated: '与现有知识无关',
  uncertain: '无法验证',
}
/** What will happen to the knowledge we already had — in the user's words. */
const RELATION_LABEL: Record<string, string> = {
  supersedes: '将取代旧知识',
  contradicts: '与旧知识冲突，需你判断',
  duplicate: '与旧知识相同',
  coexists: '与旧知识并存',
  new: '作为新知识写入',
  unresolved: '无法映射到受控词表',
  rejected: '违反本体约束',
}

const affected = ref<any>(null)
const proposed = ref({ subject: '', predicate: '', object: '' })

function reset() {
  plan.value = null; planQuality.value = null; affected.value = null; error.value = ''
}

async function analyze() {
  const q = text.value.trim()
  if (!q) { store.toast('先写一句要改成什么'); return }
  error.value = ''
  reset()
  analyzing.value = true
  try {
    const p = await post<any>('/api/knowledge/corrections', { text: q })
    plan.value = p
    const it = p?.intent || {}
    proposed.value = {
      subject: it.subject || '',
      predicate: it.predicate || it.predicate_candidate || '',
      object: it.object || '',
    }
    affected.value = (p?.candidates || [])[0] || null
    if (p?.related_claim_id) {
      try {
        planQuality.value = await api<any>('/api/knowledge/claims/' + p.related_claim_id + '/quality')
      } catch { planQuality.value = null }
    }
  } catch (e: any) {
    error.value = e.message
  } finally {
    analyzing.value = false
  }
}

/** Execute a confirmed correction. Also the entry for the manual (no-model) path. */
async function applyPayload(payload: any) {
  applying.value = true
  error.value = ''
  try {
    const r = await post<any>('/api/knowledge/corrections/apply', payload)
    plan.value = null
    emit('applied', r)
    // 纠正成功时先说成功；它若牵连出新问题，那条消息会顶上来（两句话不能同时说，
    // 但漏掉「改了」或漏掉「弄坏了什么」都是说谎）。
    if (!reportIssues(r?.issues, store, router)) store.toast('已更新 · 旧知识保留在历史里')
    void store.loadPendingReview()
    return r
  } catch (e: any) {
    error.value = e.message
    return null
  } finally {
    applying.value = false
  }
}

function confirm() {
  if (!plan.value) return
  const it = plan.value.intent || {}
  void applyPayload({
    text: text.value.trim(),
    subject: it.subject, predicate: it.predicate, object: it.object || '',
    polarity: it.polarity || 'positive', confidence: it.confidence ?? null,
    relationship: plan.value.relationship,
    related_claim_id: plan.value.related_claim_id,
    apply_supersede: plan.value.apply_supersede || false,
  })
}

function openClaim(id?: string) { if (id) router.push('/knowledge/claim/' + id) }

defineExpose({ analyze, applyPayload, reset, applyState: applying })
</script>

<template>
  <div class="cflow" :class="{ compact }">
    <textarea v-model="text" :rows="compact ? 2 : 3" class="ta"
              :placeholder="compact ? '哪里不对？例如：苹果现在的 CEO 是 John Ternus。' : '例如：苹果公司的新任 CEO 是约翰·特努斯。'" />
    <div class="row" style="margin-top:8px">
      <button class="btn primary sm" :disabled="analyzing" @click="analyze">
        {{ analyzing ? '分析中…' : '分析' }}
      </button>
      <span v-if="!compact" class="faint small">需要已配置的语言模型；无模型时可在下方手动填写。</span>
    </div>

    <div v-if="error" class="notice red" style="margin-top:10px">{{ error }}</div>

    <!-- 发现已有知识 / 建议 / 旧知识会怎样 —— 用户真正需要判断的三件事 -->
    <div v-if="plan" class="result" style="margin-top:12px">
      <template v-if="plan.blocked">
        <div class="notice red">
          <b>这句改不了。</b>「{{ plan.intent?.predicate_candidate || text }}」映射不到受控词表，
          系统不会为了写入而造一个新谓词。请换一种说法。
        </div>
      </template>
      <template v-else>
        <div class="sechead" style="margin-top:0">
          <h3>要对这条做什么</h3>
          <span class="tag" :class="plan.apply_supersede ? 'amber' : 'blue'" style="margin:0">
            {{ RELATION_LABEL[plan.relationship] || plan.relationship }}
          </span>
        </div>

        <div class="diff">
          <div v-if="affected" class="side old">
            <span class="lbl">已有知识</span>
            <b>{{ affected.content || affected.object || '（原断言）' }}</b>
            <span v-if="affected.status" class="faint small">{{ statusStyle(affected.status).label }}</span>
          </div>
          <div v-else class="side old empty-side">
            <span class="lbl">已有知识</span>
            <b>没有找到相关断言</b>
            <span class="faint small">这条会作为新知识写入</span>
          </div>
          <div class="arrow">→</div>
          <div class="side new">
            <span class="lbl">建议</span>
            <b>{{ proposed.subject }} · {{ proposed.predicate }} · {{ proposed.object || '—' }}</b>
            <span v-if="plan.apply_supersede" class="faint small">旧知识不会被删除，会保留为历史</span>
          </div>
        </div>

        <p v-if="plan.summary" class="summary">{{ plan.summary }}</p>

        <div v-if="plan.verification && plan.verification.verdict !== 'uncertain'" class="verification" :class="plan.verification.verdict">
          <div class="vhead">
            <span class="tag" :class="plan.verification.verdict">{{ VERDICT_LABELS[plan.verification.verdict] || plan.verification.verdict }}</span>
            <span class="faint">置信度 {{ Math.round((plan.verification.confidence || 0) * 100) }}%</span>
          </div>
          <p class="rationale">{{ plan.verification.rationale }}</p>
        </div>

        <div v-if="!compact && planQuality" class="quality">
          <div class="qhead">
            <span>受影响知识的质量</span>
            <b class="grade" :class="'g-' + planQuality.grade">{{ planQuality.grade }}</b>
            <span class="faint">{{ Math.round(planQuality.overall * 100) }}%</span>
          </div>
          <div class="bars">
            <div v-for="(v, k) in planQuality.dimensions" :key="k" class="bar">
              <span>{{ DIM_LABELS[k] || k }}</span>
              <span class="bt"><i :style="{ width: Math.round(v * 100) + '%' }" :class="{ low: v < 0.5 }"></i></span>
              <span class="bv">{{ Math.round(v * 100) }}</span>
            </div>
          </div>
        </div>

        <div class="row" style="margin-top:12px">
          <button class="btn primary sm" :disabled="applying" @click="confirm">
            {{ applying ? '更新中…' : confirmLabel }}
          </button>
          <button class="btn ghost sm" @click="reset">取消</button>
          <button v-if="affected" class="btn ghost sm" @click="openClaim(affected.claim_id)">看旧知识</button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.ta{width:100%;border:1px solid var(--hair);border-radius:11px;padding:10px 12px;font:inherit;font-size:12.5px;resize:vertical;background:#fff;color:var(--text)}
.cflow.compact .ta{font-size:11.5px;border-radius:10px}
.small{font-size:10px}
.result{padding:12px;border:1px solid var(--hair);border-radius:12px;background:#fcfdff}
.cflow.compact .result{padding:11px}
.diff{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:10px}
.cflow.compact .diff{grid-template-columns:1fr;gap:8px}
.side{display:grid;gap:3px;padding:10px 12px;border-radius:11px;font-size:11.5px;min-width:0}
.side b{font-size:12px;font-weight:650;overflow-wrap:anywhere}
.side.old{background:var(--surface2);border:1px solid var(--hair)}
.side.new{background:var(--tint-blue);border:1px solid #cfdcff}
.side .lbl{font-size:8.5px;letter-spacing:.08em;color:var(--faint);font-weight:700}
.arrow{color:var(--faint);font-size:13px;text-align:center}
.cflow.compact .arrow{transform:rotate(90deg)}
.summary{margin:11px 0 0;font-size:12px;color:var(--sub);line-height:1.6}
.verification{margin-top:10px;padding:10px 12px;border-radius:10px;background:#f7f8fb;border:1px solid var(--hair)}
.verification .vhead{display:flex;align-items:center;gap:10px;font-size:11px;margin-bottom:6px}
.verification .rationale{font-size:11px;color:var(--sub);margin:0;line-height:1.55}
.verification.contradicted{background:#fdf2f2;border-color:#f5d0d0}
.verification.supported{background:#f1f8f4;border-color:#cfe9d7}
.tag.contradicted{background:#d9695a;color:#fff}
.tag.supported{background:#3aa76d;color:#fff}
.tag.unrelated{background:#9aa3b2;color:#fff}
.quality{margin-top:11px;padding:11px;border:1px solid var(--hair);border-radius:11px;background:#fff}
.qhead{display:flex;align-items:center;gap:9px;font-size:11px;color:var(--sub)}
.grade{font-size:13px;width:22px;height:22px;border-radius:7px;display:grid;place-items:center;color:#fff}
.g-A{background:#3aa76d}.g-B{background:#5b9bd5}.g-C{background:#e0a93b}.g-D{background:#d9695a}
.bars{margin-top:9px;display:grid;gap:5px}
.bar{display:grid;grid-template-columns:84px 1fr 30px;align-items:center;gap:8px;font-size:10px;color:var(--sub)}
.bt{height:7px;background:#eef1f7;border-radius:99px;overflow:hidden}
.bt i{display:block;height:100%;background:linear-gradient(90deg,#5b7cff,#8b67f7)}
.bt i.low{background:#d9695a}
.bv{text-align:right;color:var(--faint)}
</style>
