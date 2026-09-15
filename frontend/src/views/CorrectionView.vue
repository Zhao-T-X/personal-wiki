<script setup lang="ts">
/* 纠正页 = CorrectionFlow + 两件页面级的东西：手动填写（无模型兜底）与纠正审计。
 *
 * 分析 → 建议 → 确认这段流程本身住在 CorrectionFlow 里，
 * 因为答案卡片也需要它（「这条有问题」）。一个流程，一份实现。
 * 这个页面仍然可以用，只是它不再是唯一的入口——
 * 用户不该为了改一条知识而先学会一个叫「纠正」的功能。
 */
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import CorrectionFlow from '../components/CorrectionFlow.vue'
import PageHead from '../components/PageHead.vue'
import { useAppStore } from '../stores/app'

const router = useRouter()
const store = useAppStore()

const flow = ref<any>(null)
const manual = ref(false)
const applyingManual = ref(false)
const result = ref<any>(null)
const history = ref<any[]>([])

// Manual entry is the no-model fallback: /corrections/apply needs only
// subject/predicate/object, so a correction still works without an LLM.
const m = reactive({ text: '', subject: '', predicate: '', object: '', relationship: '', related_claim_id: '' })

async function loadHistory() {
  try {
    history.value = (await api<any[]>('/api/knowledge/operations?kind=CORRECT&limit=20')) || []
  } catch { history.value = [] }
}

/** CorrectionFlow reports the authoritative result; the page keeps the audit tail. */
async function onApplied(r: any) {
  result.value = r
  await loadHistory()
}

async function applyManual() {
  if (!m.subject.trim() || !m.predicate.trim()) { store.toast('subject 与 predicate 必填'); return }
  applyingManual.value = true
  try {
    await flow.value?.applyPayload({
      text: m.text.trim() || `${m.subject} ${m.predicate} ${m.object}`.trim(),
      subject: m.subject.trim(), predicate: m.predicate.trim(), object: m.object.trim(),
      polarity: 'positive', confidence: null,
      relationship: m.relationship || null, related_claim_id: m.related_claim_id || null,
      apply_supersede: m.relationship === 'supersedes',
    })
  } finally {
    applyingManual.value = false
  }
}

function openClaim(id?: string) { if (id) router.push('/knowledge/claim/' + id) }

onMounted(loadHistory)
</script>

<template>
  <div class="page">
    <PageHead title="纠正知识"
              subtitle="把一句新的事实变成一次真正的知识更新：分析 → 确认 → 新断言 + 演化关系 + 审计">
      <template #actions>
        <button class="btn ghost" :class="{ primary: manual }" @click="manual = !manual">
          {{ manual ? '用句子分析' : '手动填写（无需模型）' }}
        </button>
      </template>
    </PageHead>

    <div class="panel pad">
      <CorrectionFlow ref="flow" @applied="onApplied" />

      <div v-if="manual">
        <hr class="hairline" style="margin:16px 0" />
        <div class="sechead"><h3>手动填写（直接调用 CORRECT 操作，不依赖模型）</h3></div>
        <div class="grid3">
          <input v-model="m.text" placeholder="原句（可选）" />
          <input v-model="m.subject" placeholder="subject（实体名）" />
          <input v-model="m.predicate" placeholder="predicate（如 has_ceo）" />
          <input v-model="m.object" placeholder="object（可选）" />
          <input v-model="m.relationship" placeholder="relationship（supersedes 等，可选）" />
          <input v-model="m.related_claim_id" placeholder="受影响断言 ID（可选）" />
        </div>
        <button class="btn primary" style="margin-top:10px" :disabled="applyingManual" @click="applyManual">
          {{ applyingManual ? '执行中…' : '直接应用' }}
        </button>
      </div>
    </div>

    <div v-if="result" class="panel pad" style="margin-top:16px">
      <div class="sechead"><h3>已生成</h3><span class="tag green" style="margin:0">CORRECT</span></div>
      <div class="out">
        <span>新断言：<b class="link" @click="openClaim(result.claim_id)">{{ result.claim_id }}</b></span>
        <span v-if="result.superseded_claim_id">旧断言已转为历史：<b class="link" @click="openClaim(result.superseded_claim_id)">{{ result.superseded_claim_id }}</b></span>
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
.small{font-size:10px}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}
.grid3 input{border:1px solid var(--hair);border-radius:9px;padding:8px 10px;font:inherit;font-size:11.5px;background:#fff;color:var(--text)}
.out{display:grid;gap:6px;font-size:12px;margin-top:8px}
.out code,.cid{font-size:10px;background:#f2f5fc;border-radius:6px;padding:1px 6px;color:var(--sub)}
.link{color:#4a63e8;cursor:pointer;text-decoration:underline}
.item{display:flex;align-items:center;gap:10px;padding:8px 4px;border-bottom:1px solid var(--hair)}
.item:last-child{border-bottom:none}
.item p{margin:2px 0 0}
</style>
