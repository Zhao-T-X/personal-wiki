<script setup lang="ts">
/**
 * KnowledgeCard — how one piece of knowledge reads, everywhere.
 *
 * Front stage:   事实 / 状态 / 依据 / 操作
 * Back stage:    Claim / Predicate / Evidence / Operation  (never shown here)
 *
 * The point of one component is not reuse, it is that the user meets the same shape
 * — a fact, a state, its evidence and what can be done with it — wherever knowledge
 * appears. Three surfaces inventing three layouts is what makes a product feel like
 * several products.
 *
 * Deliberately **not** used in the review queue: that screen is where a candidate is
 * *decided* (通过 / 拒绝), not read. Giving it a [纠正] button would offer two ways
 * to do the same thing and neither would be obviously right.
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { patchJson, post } from '../api/client'
import CorrectionFlow from './CorrectionFlow.vue'
import StatusTag from './StatusTag.vue'
import { useAppStore } from '../stores/app'
import { labelOf, type KnowledgeCardClaim } from '../utils/claim'

/** The host is told when the card changed something, so it can refresh — and, when it
    was an adoption, so it can say what the new knowledge is and whether it collided
    with anything. Hosts that only want to refresh can keep ignoring the payload. */
const emit = defineEmits<{ (e: 'changed', change?: { id: string; status: string }): void }>()

const props = withDefaults(defineProps<{
  claim: KnowledgeCardClaim
  /** Hide the [依据][历史][纠正] row, for summary lists. */
  actions?: boolean
  /** How many sources back this claim — only when the caller already knows it. */
  evidenceCount?: number | null
  compact?: boolean
}>(), { actions: true, evidenceCount: null, compact: false })

const router = useRouter()
const store = useAppStore()
const fixing = ref(false)
const busy = ref(false)

/** Knowledge nobody has accepted yet. Its fitting action is [采纳], not [纠正] —
    and the action follows the *state*, not the page, so the same card never offers
    different choices depending on where it is read. */
const pending = computed(() => props.claim.status === 'candidate')

/** 采纳走 KnowledgeOperation：它是可审计的知识操作，不是一次状态补丁。
    采纳只移动生命周期，不改内容——所以被采纳的就是被提议的那句话。 */
async function adopt() {
  if (!props.claim.id) return
  busy.value = true
  try {
    await post('/api/knowledge/operations', { kind: 'ACCEPT', payload: { claim_id: props.claim.id } })
    store.toast('已采纳为知识')
    emit('changed', { id: props.claim.id, status: 'verified' })
  } catch (e: any) { store.toast(e.message) } finally { busy.value = false }
}

/** 忽略：这条提议不进知识库，但它仍然是记录在案的一次判断。 */
async function dismiss() {
  if (!props.claim.id) return
  busy.value = true
  try {
    await patchJson(`/api/knowledge/claim/${props.claim.id}/status`, { status: 'rejected' })
    store.toast('已忽略这条候选')
    emit('changed', { id: props.claim.id, status: 'rejected' })
  } catch (e: any) { store.toast(e.message) } finally { busy.value = false }
}

/** "苹果公司 · 首席执行官" — the fact's name, in words the registry supplies. */
const title = computed(() =>
  [props.claim.subject, labelOf(props.claim.predicate)].filter(Boolean).join(' · '))

function openEvidence() {
  if (!props.claim.documentId) return
  router.push({
    path: '/knowledge',
    query: { doc: props.claim.documentId, ...(props.claim.chunkId ? { chunk: props.claim.chunkId } : {}) },
  })
}
/** History lives on the claim's own page. The flag opens the chain there, so the
    same button works from anywhere — a card in the knowledge drawer has no way to
    expand a section that is not on screen. */
function openHistory() {
  if (props.claim.id) router.push({ path: '/knowledge/claim/' + props.claim.id, query: { history: '1' } })
}
</script>

<template>
  <div class="kcard" :class="{ compact }">
    <div class="khead">
      <b class="ktitle">{{ title || '（缺少主语或谓词）' }}</b>
      <div class="grow"></div>
      <StatusTag :status="claim.status" :label="claim.stateLabel" />
    </div>

    <div class="kvalue">{{ claim.object || '—' }}</div>

    <!-- 依据：真实引文，不是转述 -->
    <div v-if="claim.quote" class="kquote">“{{ claim.quote }}”</div>

    <div v-if="actions" class="kactions">
      <button class="btn sm" :disabled="!claim.documentId" @click="openEvidence">依据</button>
      <!-- 待确认的知识还没被接受，所以对它说「纠正」是答错了问题。
           研究页、搜索页、问答页因此得到同一组动作——由状态决定，不由页面决定。 -->
      <template v-if="pending">
        <button class="btn sm primary" :disabled="busy" @click="adopt">采纳</button>
        <button class="btn sm" :disabled="busy" @click="dismiss">忽略</button>
      </template>
      <template v-else>
        <button class="btn sm" :disabled="!claim.id" @click="openHistory">历史</button>
        <button class="btn sm" :class="{ primary: fixing }" @click="fixing = !fixing">
          {{ fixing ? '收起' : '纠正' }}
        </button>
      </template>
      <div class="grow"></div>
      <span v-if="evidenceCount != null" class="kcount">{{ evidenceCount }} 个依据</span>
      <span v-else-if="claim.createdAt" class="kcount">{{ claim.createdAt.slice(0, 10) }}</span>
    </div>

    <!-- 纠正长在知识旁边，而不是一个叫 Correction 的页面里 -->
    <CorrectionFlow v-if="fixing" compact :seed="claim.quote" style="margin-top:10px" />
  </div>
</template>

<style scoped>
.kcard{
  padding:13px 15px;border:1px solid var(--hair);border-radius:14px;background:#fff;
  transition:.16s;
}
.kcard:hover{border-color:#dbe3f2;box-shadow:0 6px 18px rgba(91,124,255,.06)}
.kcard.compact{padding:11px 12px;border-radius:12px}
.khead{display:flex;align-items:center;gap:9px}
.ktitle{font-size:11px;color:var(--sub);font-weight:650;letter-spacing:.01em}
.compact .ktitle{font-size:10.5px}
.kvalue{font-size:16px;font-weight:650;letter-spacing:-.02em;margin-top:5px;overflow-wrap:anywhere}
.compact .kvalue{font-size:13.5px;margin-top:4px}
.kquote{
  margin:9px 0 0;padding:8px 11px;border-left:2px solid #dbe3f2;background:var(--surface2);
  border-radius:0 9px 9px 0;font-size:11px;color:var(--sub);line-height:1.6;overflow-wrap:anywhere;
}
.compact .kquote{font-size:10.5px;margin-top:8px;padding:7px 9px}
.kactions{display:flex;align-items:center;gap:7px;margin-top:12px}
.compact .kactions{margin-top:10px}
.kcount{font-size:9.5px;color:var(--faint)}
</style>
