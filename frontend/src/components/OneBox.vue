<script setup lang="ts">
/**
 * One Box —— 一个入口，四条已经存在的业务路径。
 *
 * 它不是聊天框，也不是四个页面的快捷方式。它只做两件事：
 *
 *   1. 把一句话交给 `/api/onebox/intent` 判断意图（不调模型、不写任何东西）；
 *   2. 按判断结果执行——**用已经存在的接口**，所以这里没有第二套 Ask / Research /
 *      Correction / 导入逻辑，任何一条被修好，这里自动跟着好。
 *
 * 两条产品规则写在这里：
 *
 * * **读取直接执行，写入先确认。** 问错了只是重说一句；写错了是用户对自己知识库的
 *   信任。所以只有 ASK 会自己跑。
 * * **读错了要容易改。** 判断结果与理由一直显示着，用户可以一键换一个意图，不用重打。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { post } from '../api/client'
import { useAppStore } from '../stores/app'
import CorrectionFlow from './CorrectionFlow.vue'
import KnowledgeCard from './KnowledgeCard.vue'
import MarkdownView from './MarkdownView.vue'
import { toCard } from '../utils/claim'

const props = withDefaults(defineProps<{
  /** 用户当前正在看的知识——让「这个不对」不必重新说明它在说哪条。 */
  contextClaimId?: string | null
}>(), { contextClaimId: null })

const router = useRouter()
const store = useAppStore()
const text = ref('')
const reading = ref(false)
const running = ref(false)
/** 判断结果：意图、置信度、理由，以及要执行哪几步。 */
const plan = ref<any>(null)
/** 执行结果，按意图不同渲染。 */
const result = ref<{ kind: string; body: any } | null>(null)

const INTENT_LABEL: Record<string, string> = {
  ask: '查你的知识', knowledge: '记为知识', research: '去研究', correct: '纠正知识', unknown: '不确定',
}

const needsConfirm = computed(() => !!plan.value?.needs_confirmation)

/** 宿主（首页的示例问题）走同一个入口，所以同一句话不会有两套行为。 */
defineExpose({ run: (sentence: string) => { text.value = sentence; return route() } })

async function route() {
  const sentence = text.value.trim()
  if (!sentence) return
  reading.value = true
  result.value = null
  try {
    plan.value = await post('/api/onebox/intent',
      { text: sentence, context_claim_id: props.contextClaimId })
    // 读取类直接执行：问对了立刻有答案，问错了只是重说一句。
    if (!plan.value.needs_confirmation && plan.value.steps?.length) await run()
  } catch (e: any) { store.toast(e.message) } finally { reading.value = false }
}

/** 按计划的步骤调用既有接口。步骤描述由服务端给出，这里不含业务判断。 */
async function run() {
  if (!plan.value?.steps?.length) return
  running.value = true
  try {
    let body: any = null
    for (const step of plan.value.steps) {
      body = await post(step.endpoint, step.body || {})
    }
    result.value = { kind: plan.value.intent, body }
    if (plan.value.intent === 'knowledge' && body?.id) {
      // 导入是两步（建文档 → 抽取），和导入面板走的是同一对接口。
      const indexed = await post(`/api/documents/${body.id}/index`)
      result.value = { kind: 'knowledge', body: { ...body, index: indexed } }
    }
  } catch (e: any) { store.toast(e.message) } finally { running.value = false }
}

function override(intent: string) {
  if (!plan.value) return
  plan.value = { ...plan.value, intent, needs_confirmation: true, steps: [] }
  store.toast('先选一个意图，我再按它执行')
  void intent
}

function useSuggestion(s: string) { text.value = s; void route() }

const correctionPlan = computed(() => (result.value?.kind === 'correct' ? result.value.body : null))
const knowledge = computed<any[]>(() => result.value?.body?.knowledge || [])
</script>

<template>
  <div class="onebox">
    <div class="askbox">
      <input v-model="text" placeholder="问点什么、记点什么、研究点什么…" @keydown.enter="route()" />
      <button class="go" :disabled="reading || running" @click="route()">
        {{ reading ? '…' : '↑' }}
      </button>
    </div>

    <!-- 结果类型透明：用户不需要知道内部意图，但有权知道系统在做什么 -->
    <div v-if="reading || running || (plan && !result)" class="obstatus">
      <template v-if="reading">正在理解这句话…</template>
      <template v-else-if="running">{{ plan?.summary }}</template>
      <template v-else>{{ plan?.summary }}</template>
    </div>

    <!-- 判断结果一直显示：读错了要能一键改，而不是重打一遍 -->
    <div v-if="plan && !result" class="obplan">
      <span class="tag blue">我理解为：{{ INTENT_LABEL[plan.intent] || plan.intent }}</span>
      <span class="faint" style="font-size:9.5px">{{ plan.reason }}</span>
      <div class="grow"></div>
      <template v-if="plan.intent === 'unknown'">
        <button v-for="s in plan.suggestions" :key="s" class="btn sm" @click="useSuggestion(s)">{{ s }}</button>
      </template>
      <template v-else>
        <button class="btn sm ghost" @click="override('ask')">其实我是想问</button>
        <button class="btn sm ghost" @click="override('research')">其实我是想研究</button>
        <button v-if="needsConfirm" class="btn primary" :disabled="running" @click="run">
          按这个执行
        </button>
      </template>
    </div>

    <!-- 答案：一句话是主角，卡片是它的依据 -->
    <template v-if="result?.kind === 'ask'">
      <div class="panel pad" style="margin-top:12px">
        <MarkdownView :content="result.body.answer || ''" />
        <template v-if="knowledge.length">
          <div class="sechead" style="margin:12px 0 8px"><h3>支持知识</h3></div>
          <div class="obcards">
            <KnowledgeCard v-for="k in knowledge" :key="k.claim_id" :claim="toCard(k)"
                           :evidence-count="k.sources" compact />
          </div>
        </template>
      </div>
    </template>

    <!-- 纠正：原地给出建议，而不是把用户丢到另一个页面 -->
    <template v-else-if="correctionPlan">
      <div class="panel pad" style="margin-top:12px">
        <b style="font-size:12px">{{ correctionPlan.summary || '已找到相关知识与建议' }}</b>
        <p v-if="correctionPlan.related_claim_id" class="faint" style="font-size:10px">
          与现有知识相关：{{ correctionPlan.related_claim_id.slice(0, 8) }} · 关系 {{ correctionPlan.relationship }}
        </p>
        <CorrectionFlow compact :seed="text" style="margin-top:10px"
                        @applied="store.toast('知识已更新，再问一次会得到新答案')" />
      </div>
    </template>

    <template v-else-if="result?.kind === 'research'">
      <div class="panel pad" style="margin-top:12px">
        <b style="font-size:12px">已创建工作研究任务</b>
        <p class="faint" style="font-size:10px;margin:4px 0 0">
          研究完成后，结论会在研究页整理成「研究候选」，采纳后才成为知识。
        </p>
        <button class="btn sm" style="margin-top:9px"
                @click="router.push('/research?tab=tasks')">去看研究 →</button>
      </div>
    </template>

    <template v-else-if="result?.kind === 'knowledge'">
      <div class="panel pad" style="margin-top:12px">
        <b style="font-size:12px">已整理为知识</b>
        <p class="faint" style="font-size:10px;margin:4px 0 0">
          抽取到 {{ result.body.index?.counts?.claims ?? 0 }} 条断言，仍待你确认。
        </p>
        <button class="btn sm" style="margin-top:9px"
                @click="router.push('/knowledge')">去知识空间 →</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.onebox{width:100%}
.obstatus{margin-top:10px;font-size:10px;color:var(--sub)}
.obplan{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:12px;
  padding:9px 11px;border:1px solid var(--hair);border-radius:11px;background:var(--surface2)}
.obcards{display:grid;gap:9px}
.askbox input{flex:1}
</style>
