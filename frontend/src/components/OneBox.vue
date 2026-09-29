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
 * 三条产品规则写在这里：
 *
 * * **读取直接执行，写入先确认。** 问错了只是重说一句；写错了是用户对自己知识库的
 *   信任。所以只有 ASK 会自己跑。
 * * **读错了要容易改。** 判断结果与理由一直显示着，用户可以一键换一个意图，不用重打。
 * * **看得见的按钮背后一定有动作。** 改判不是把意图字段改掉就完事——它带着同一个句子
 *   重新问一次那张唯一的路由表，所以「按这个执行」永远有真正的步骤。这个不变量由
 *   `canExecute()` 一个谓词同时守住按钮的渲染与 `run()` 的入口，见 utils/onebox.ts。
 *
 * 改判之所以要回到服务端，而不是在这里自己拼步骤：步骤映射只应该有一份（`app/intent.py`
 * 的 `_route`）。前端复制一份，等于制造第二个真相，而且它一定会漂移。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { post } from '../api/client'
import { useAppStore } from '../stores/app'
import CorrectionFlow from './CorrectionFlow.vue'
import KnowledgeCard from './KnowledgeCard.vue'
import MarkdownView from './MarkdownView.vue'
import { toCard } from '../utils/claim'
import { INTENT_LABEL, canExecute, overrideOptions, overrideRequest, type IntentPlan } from '../utils/onebox'

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
const plan = ref<IntentPlan | null>(null)
/** 执行结果，按意图不同渲染。 */
const result = ref<{ kind: string; body: any } | null>(null)
/** 这一次的判断是用户改的，还是系统读的——决定要不要给「恢复系统判断」。 */
const overridden = ref(false)

/** 按钮是否存在、和 run() 是否真的有事可做，是同一个判断。 */
const executable = computed(() => canExecute(plan.value))
/** 除当前意图之外的改法：当前意图的按钮出现只会是废话。 */
const options = computed(() => overrideOptions(plan.value?.intent))

/** 宿主（首页的示例问题）走同一个入口，所以同一句话不会有两套行为。 */
defineExpose({ ask: (sentence: string) => { text.value = sentence; return route() } })

async function route(opts: { intent?: string } = {}) {
  const sentence = text.value.trim()
  if (!sentence) return
  reading.value = true
  result.value = null
  try {
    // 改判不自己编步骤：把选择交给服务端那张唯一的路由表，前端不留第二份映射。
    plan.value = await post<IntentPlan>('/api/onebox/intent', opts.intent
      ? overrideRequest(sentence, opts.intent, props.contextClaimId)
      : { text: sentence, context_claim_id: props.contextClaimId })
    overridden.value = !!opts.intent
    // 读取类直接执行：问对了立刻有答案，问错了只是重说一句。
    if (!plan.value.needs_confirmation && canExecute(plan.value)) await run()
  } catch (e: any) {
    plan.value = null
    overridden.value = false
    store.toast(e.message)
  } finally { reading.value = false }
}

/** 按计划的步骤调用既有接口。步骤描述由服务端给出，这里不含业务判断。 */
async function run() {
  if (!canExecute(plan.value)) {
    // 界面不可达（按钮用同一个谓词），但绝不静默返回：宁可说清「没有下一步」。
    store.toast('这一步没有可执行的动作——换一种说法，或从上面选一个意图')
    return
  }
  running.value = true
  try {
    let body: any = null
    for (const step of plan.value!.steps!) {
      body = await post(step.endpoint, step.body || {})
    }
    result.value = { kind: plan.value!.intent, body }
    if (plan.value!.intent === 'knowledge' && body?.id) {
      // 导入是两步（建文档 → 抽取），和导入面板走的是同一对接口。
      const indexed = await post(`/api/documents/${body.id}/index`)
      result.value = { kind: 'knowledge', body: { ...body, index: indexed } }
    }
  } catch (e: any) { store.toast(e.message) } finally { running.value = false }
}

/** 改判：用同一个句子重新规划，于是按钮背后一定真的有动作。 */
function override(intent: string) { void route({ intent }) }
/** 回到系统原来的判断——改错了要能退出来。 */
function restoreReading() { void route() }

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
        <button v-for="s in plan.suggestions || []" :key="s" class="btn sm" @click="useSuggestion(s)">{{ s }}</button>
      </template>
      <template v-else>
        <button v-for="o in options" :key="o.intent" class="btn sm ghost" @click="override(o.intent)">
          {{ o.label }}
        </button>
        <button v-if="overridden" class="btn sm ghost" @click="restoreReading">恢复系统判断</button>
        <!-- 按钮与 run() 共用 canExecute：没有可执行步骤时按钮不存在，
             而不是存在但点了没反应。 -->
        <button v-if="executable" class="btn primary" :disabled="running" @click="run">
          按这个执行
        </button>
        <span v-else class="faint hint">
          这一步没有可执行的下一步——换一种说法，或从上面选一个意图。
        </span>
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
        <b style="font-size:12px">已创建研究任务</b>
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
          整理了 {{ result.body.index?.counts?.claims ?? 0 }} 条知识，仍待你确认。
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
.hint{font-size:9.5px;line-height:1.6}
.obcards{display:grid;gap:9px}
.askbox input{flex:1}
</style>
