<script setup lang="ts">
/**
 * AnswerCard —— 一处呈现，三处复用（首页 OneBox / 问答 / 研究候选）。
 *
 * 固定顺序：用户问题 → 结论（即答案）→ 依据 → 纠正。
 *
 * 它刻意「不像一张文档」：外层不套白卡、不加阴影，靠标题、间距、分隔线分层，
 * 所以嵌在 `.panel`（白卡）里也不会出现「大白卡套大白卡」。依据是轻灰列表，
 * 不是另一面墙；纠正入口长在答案旁边，不跳页，沿用 CorrectionFlow 这一份实现。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import MarkdownView from './MarkdownView.vue'
import KnowledgeCard from './KnowledgeCard.vue'
import CorrectionFlow from './CorrectionFlow.vue'
import { toCard } from '../utils/claim'
import { useAppStore } from '../stores/app'

const props = withDefaults(defineProps<{
  /** 用户问的那句话——答案必须能一眼对应到它。 */
  question?: string
  /** 答案本身：结论与简短解释都在这段话里，不强行拆两段。 */
  answer: string
  /** 依据来源：紧跟答案，让用户知道「为什么这么说」。 */
  evidence?: { title?: string; content?: string; id?: string; document_id?: string; source_type?: string }[]
  /** 支持知识：几条事实，可选，自带 [依据][历史][纠正]。 */
  knowledge?: any[]
  /** 纠正起点：要改的是「这个答案依据的那条知识」，不是刚问的那句话。 */
  correctionExisting?: string
  correctionSeed?: string
  /** 首页用更紧凑的间距。 */
  compact?: boolean
  /** 依据是否可点击跳到来源文档。 */
  openable?: boolean
}>(), {
  compact: false,
  openable: true,
  evidence: () => [],
  knowledge: () => [],
})

const emit = defineEmits<{
  (e: 'corrected'): void
  (e: 'supporting-changed'): void
}>()

const router = useRouter()
const store = useAppStore()
const fixOpen = ref(false)

const sources = computed(() => props.evidence || [])
const knowledgeCards = computed(() => (props.knowledge || []).map(k => toCard(k)))

function openEvidence(e: { document_id?: string; id?: string }) {
  if (!e.document_id) { store.toast('这条证据没有关联的来源文档'); return }
  router.push({ path: '/knowledge', query: { doc: e.document_id, chunk: e.id } })
}

function onCorrected() { fixOpen.value = false; emit('corrected') }
</script>

<template>
  <div class="anscard" :class="{ compact }">
    <!-- 用户问题：答案必须一眼对应到它 -->
    <div v-if="question" class="q">{{ question }}</div>

    <!-- 结论 / 解释：答案即结论，直接呈现，不套文档式白卡 -->
    <div class="conclusion">
      <MarkdownView :content="answer" />
    </div>

    <!-- 依据：紧跟回答，轻灰列表 + 分隔线，不另起一面墙 -->
    <template v-if="sources.length">
      <div class="divider"></div>
      <div class="evhead">依据 · {{ sources.length }} 个来源</div>
      <div class="srclist">
        <button v-for="(s, i) in sources" :key="i" class="src" :disabled="!openable"
                :title="openable ? '打开来源文档并定位到该片段' : ''" @click="openEvidence(s)">
          <span class="num">{{ i + 1 }}</span>
          <span class="grow">
            <b>{{ s.title || '来源文档' }}</b>
            <span v-if="s.content" class="snip">{{ (s.content || '').slice(0, 96) }}</span>
          </span>
          <span v-if="openable" class="open">打开 →</span>
        </button>
      </div>
    </template>

    <!-- 支持知识：几条事实，自带追查与就地纠正 -->
    <template v-if="knowledgeCards.length">
      <div class="divider"></div>
      <div class="evhead">支持知识 · {{ knowledgeCards.length }} 条</div>
      <div class="ksupport">
        <KnowledgeCard v-for="k in knowledgeCards" :key="k.id" :claim="k" compact
                       @changed="emit('supporting-changed')" />
      </div>
    </template>

    <!-- 纠正入口：答案旁边的轻入口，不跳页，沿用 CorrectionFlow -->
    <div class="correct">
      <span class="faint">这条回答有问题？</span>
      <button class="btn sm ghost" @click="fixOpen = !fixOpen">{{ fixOpen ? '收起' : '纠正' }}</button>
    </div>
    <CorrectionFlow v-if="fixOpen" compact :existing="correctionExisting" :seed="correctionSeed"
                    style="margin-top:10px" @applied="onCorrected" />
  </div>
</template>

<style scoped>
.anscard{display:flex;flex-direction:column}
.anscard.compact{font-size:.96em}
/* 问题：答案的锚点，先读它再读答案 */
.q{font-size:15px;font-weight:680;letter-spacing:-.02em;color:var(--text);line-height:1.45;
  margin:2px 0 12px;overflow-wrap:anywhere}
/* 结论区：没有自己的卡片边框，靠父级 panel 托底；少圆角少阴影 */
.conclusion{font-size:12.5px;line-height:1.78;color:#2f3744}
.conclusion :deep(p){margin:0 0 8px}
.conclusion :deep(p:last-child){margin-bottom:0}
.divider{height:1px;background:var(--hair);margin:14px 0}
.evhead{font-size:9px;letter-spacing:.08em;font-weight:700;color:var(--faint);margin-bottom:9px}
/* 依据：轻灰列表，不是白卡墙 */
.srclist{display:flex;flex-direction:column;gap:6px}
.src{display:flex;align-items:flex-start;gap:10px;text-align:left;width:100%;
  padding:9px 11px;border:1px solid var(--hair);border-radius:11px;background:var(--surface2);
  transition:.15s}
.src:not(:disabled){cursor:pointer}
.src:not(:disabled):hover{border-color:#ddd6cb;background:#faf8f4}
.num{flex:none;width:18px;height:18px;border-radius:99px;background:var(--tint-blue);color:var(--accent);
  display:grid;place-items:center;font-size:9.5px;font-weight:700;margin-top:1px}
.src .grow{display:flex;flex-direction:column;gap:3px;min-width:0}
.src b{font-size:11px;color:var(--text)}
.src .snip{font-size:9.5px;color:var(--sub);line-height:1.5;overflow:hidden;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.src .open{margin-left:auto;flex:none;font-size:9px;color:var(--blue)}
.ksupport{display:grid;gap:9px}
/* 纠正：一条轻入口，不是一次跳转 */
.correct{display:flex;align-items:center;gap:10px;margin-top:14px;padding-top:12px;border-top:1px solid var(--hair)}
</style>
