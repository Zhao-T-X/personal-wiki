<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, post } from '../api/client'
import type { Run } from '../api/types'
import StatusTag from '../components/StatusTag.vue'
import DataTable from '../components/DataTable.vue'
import RunDrawer from '../components/RunDrawer.vue'
import BoundaryList from '../components/BoundaryList.vue'
import EmptyState from '../components/EmptyState.vue'
import MarkdownView from '../components/MarkdownView.vue'
import { useAppStore } from '../stores/app'
import { fmtDateTime } from '../utils/time'

const route = useRoute()
const router = useRouter()
const store = useAppStore()

type Mode = 'knowledge' | 'agent'
const mode = ref<Mode>((route.query.mode as Mode) === 'agent' ? 'agent' : 'knowledge')
const question = ref((route.query.q as string) || '')

/* knowledge mode */
const loading = ref(false)
const result = ref<{ answer: string; evidence: any[]; citations: any[] } | null>(null)

/* agent mode */
const agentLoading = ref(false)
const agentResult = ref<{ answer: string; agent: string; framework?: string } | null>(null)
const agentRole = ref('auto')
const agentMs = ref<number | null>(null)
const agentRoles = ref<{ id: string; name: string; description: string }[]>([])
const openQuestions = ref<any[]>([])
const recent = ref<Run[]>([])
const runDrawerId = ref<string | null>(null)
const resultBox = ref<HTMLElement | null>(null)

/** Jump to the source: knowledge page opens the document drawer and highlights this chunk. */
function openEvidence(e: { document_id?: string; id?: string }) {
  if (!e.document_id) { store.toast('这条证据没有关联的来源文档'); return }
  router.push({ path: '/knowledge', query: { doc: e.document_id, chunk: e.id } })
}

/** Long answers land below the fold — bring the result into view when it arrives. */
async function scrollToResult() {
  await nextTick()
  resultBox.value?.scrollIntoView({ block: 'start', behavior: 'smooth' })
}

async function loadMeta() {
  const [ask, agent, qs] = await Promise.all([
    api<Run[]>('/api/runs?task_type=ask&limit=10'),
    api<Run[]>('/api/runs?task_type=agent&limit=10'),
    api<any[]>('/api/questions?limit=200'),
  ])
  recent.value = [...ask, ...agent].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 12)
  openQuestions.value = qs.filter(q => q.status === 'open')
  try {
    const r = await api<{ roles: { id: string; name: string; description: string }[] }>('/api/agent/roles')
    agentRoles.value = r.roles
  } catch { agentRoles.value = [] }
}
onMounted(() => { loadMeta(); if (question.value) send() })

const trace = computed(() => {
  const byMethod: Record<string, number> = {}
  for (const e of result.value?.evidence || []) byMethod[e.method || 'hybrid'] = (byMethod[e.method || 'hybrid'] || 0) + 1
  return Object.entries(byMethod)
})

const boundary = computed(() => {
  const ev = result.value?.evidence || []
  const items: { level: 'ok' | 'cond' | 'open'; text: string }[] = []
  if (ev.length) items.push({ level: 'ok', text: `直接证据支持：回答基于 ${ev.length} 条证据` })
  items.push({ level: 'cond', text: '回答不因证据不足而自动补全——条件性结论已在正文中标注' })
  const kw = question.value.trim().slice(0, 6)
  const related = openQuestions.value.filter(q => kw && (q.content as string).includes(kw)).length
  items.push({ level: 'open', text: related ? `${related} 个开放问题与本主题相关，可继续研究` : '没有匹配的开放问题' })
  return items
})

async function send() {
  const q = question.value.trim()
  if (!q) { store.toast('请输入问题'); return }
  if (mode.value === 'knowledge') await askKnowledge()
  else await askAgent()
}

async function askKnowledge() {
  loading.value = true; result.value = null; agentResult.value = null
  try {
    result.value = await post('/api/ask', { question: question.value.trim(), top_k: 8 })
    await loadMeta()
    await scrollToResult()
  } catch (e: any) { store.toast(e.message) } finally { loading.value = false }
}

/* Multi-turn handle (P3): one id per agent chat session; the backend keeps the
   compressed history in conversation_summaries and the model sees the rolling
   summary + recent window instead of nothing. "新会话" resets it. */
const conversationId = ref('')

function newConversation() {
  conversationId.value = (crypto as any).randomUUID ? crypto.randomUUID() : `c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  agentResult.value = null
  store.toast('已开启新会话')
}

async function askAgent() {
  agentLoading.value = true; agentResult.value = null; result.value = null; agentMs.value = null
  if (!conversationId.value) {
    conversationId.value = (crypto as any).randomUUID ? crypto.randomUUID() : `c-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  }
  const t0 = performance.now()
  try {
    const r = await post<any>('/api/agent/ask', {
      message: question.value.trim(), role: agentRole.value, conversation_id: conversationId.value,
    })
    agentMs.value = Math.round(performance.now() - t0)
    agentResult.value = { answer: r.answer, agent: r.agent, framework: r.framework }
    await loadMeta()
    await scrollToResult()
  } catch (e: any) { store.toast(e.message) } finally { agentLoading.value = false }
}

function switchMode(m: Mode) {
  mode.value = m
  result.value = null; agentResult.value = null
}

const columns = [
  { key: 'task_type', label: '模式' }, { key: 'created_at', label: '时间' },
  { key: 'question', label: '问题' }, { key: 'status', label: '结果' }, { key: 'duration_ms', label: '耗时 ms' },
]
</script>

<template>
  <div class="page">
    <div style="text-align:center;margin:2vh 0 18px">
      <h1 style="font-size:24px">Ask your knowledge</h1>
      <p class="muted" style="font-size:10.5px;margin-top:7px">
        <template v-if="mode === 'knowledge'">知识库问答：Hybrid 检索 + Grounded 回答 + 证据与知识边界</template>
        <template v-else>Agent 问答：AgentScope ReAct Agent 可自主检索知识库、查看实体图谱后作答</template>
      </p>
    </div>

    <!-- mode switch -->
    <div class="row" style="justify-content:center;margin-bottom:16px;gap:10px">
      <div class="seg" style="padding:3px">
        <button :class="{ active: mode === 'knowledge' }" @click="switchMode('knowledge')">知识库问答</button>
        <button :class="{ active: mode === 'agent' }" @click="switchMode('agent')">Agent 问答</button>
      </div>
      <select v-if="mode === 'agent'" v-model="agentRole" class="field" style="padding:8px 10px">
        <option value="auto">自动路由 Agent</option>
        <option v-for="r in agentRoles" :key="r.id" :value="r.id">{{ r.name }} · {{ r.description }}</option>
      </select>
    </div>

    <div class="askbox" style="max-width:760px;margin:0 auto">
      <input v-model="question" :placeholder="mode === 'knowledge' ? 'Ask anything about your knowledge…' : '让 Agent 去检索、对比或研究你的知识库…'" @keydown.enter="send()" />
      <button class="go" @click="send()">↑</button>
    </div>

    <div id="qaResult" ref="resultBox" style="max-width:860px;margin:24px auto 0">
      <!-- Agent answer -->
      <div class="panel pad" v-if="mode === 'agent'">
        <div class="row">
          <h3 style="font-size:13px">Answer</h3>
          <span v-if="agentResult" class="tag violet">{{ agentResult.agent }} Agent</span>
          <span v-if="agentResult?.framework" class="tag">{{ agentResult.framework }}</span>
          <span v-if="conversationId" class="tag blue" title="多轮对话：历史以压缩摘要 + 近窗消息随问随答">会话中</span>
          <button v-if="conversationId" class="btn sm ghost" @click="newConversation">新会话</button>
          <div class="grow"></div>
          <span v-if="agentMs" class="faint" style="font-size:9px">{{ agentMs }} ms</span>
        </div>
        <div v-if="agentLoading" class="faint" style="font-size:10px;margin-top:10px">Agent 正在检索知识库并推理…（可调用 search_knowledge / get_entity / get_entity_graph）</div>
        <MarkdownView v-else-if="agentResult" :content="agentResult.answer" style="margin-top:10px" />
        <div v-else class="empty">选择 Agent 与问题后开始提问</div>
      </div>

      <!-- Knowledge answer -->
      <div class="panel pad" v-if="mode === 'knowledge'">
        <div class="row"><h3 style="font-size:13px">回答</h3><span v-if="result" class="tag green">Grounded</span></div>
        <div v-if="loading" class="faint" style="font-size:10px;margin-top:10px">Retrieval → Ranking → Grounded Generation…</div>
        <MarkdownView v-else-if="result" :content="result.answer" style="margin-top:10px" />
        <div v-else class="empty">输入问题开始提问</div>
      </div>

      <div class="grid g2" style="margin-top:16px" v-if="result && mode === 'knowledge'">
        <div>
          <div class="sechead"><h3>证据 <span class="faint" style="font-weight:400;font-size:9px">· {{ result.evidence.length }} sources</span></h3></div>
          <div class="panel pad" style="padding:6px">
            <div v-for="(e, i) in result.evidence" :key="i" class="item" style="cursor:pointer" title="打开来源文档并定位到该片段" @click="openEvidence(e)">
              <span class="tag blue" style="flex:none">{{ i + 1 }}</span>
              <div class="grow"><b>{{ e.title }}</b><p>{{ (e.content || '').slice(0, 80) }}</p>
                <span class="tag">doc:{{ (e.document_id || '').slice(0, 8) }}</span><span class="tag">chunk:{{ (e.id || '').slice(0, 8) }}</span>
                <span class="tag blue">打开来源 →</span>
              </div>
            </div>
            <EmptyState v-if="!result.evidence.length" text="知识库中没有匹配的证据，回答可能不受支持。" />
          </div>
          <div class="sechead"><h3>知识边界</h3></div>
          <div class="panel pad"><BoundaryList :items="boundary" /></div>
        </div>
        <div>
          <div class="sechead"><h3>检索轨迹</h3><span class="tag blue" style="margin:0">{{ result.evidence.length }} hits</span></div>
          <div class="panel pad" style="padding:6px">
            <div v-for="(n, m) in trace" :key="m" class="item" style="cursor:default">
              <div class="ico-badge ib-violet">Σ</div>
              <div class="grow"><b>{{ m }}</b><p>贡献 {{ n }} 条证据</p></div>
            </div>
            <div class="item" style="cursor:default"><div class="ico-badge ib-blue">⌕</div><div class="grow"><b>Hybrid Query</b><p>{{ question }}</p></div></div>
          </div>
          <div class="sechead"><h3>下一步</h3></div>
          <button class="btn" style="width:100%" @click="router.push({ path: '/research', query: { q: question } })">◇ 就此问题继续研究</button>
          <button class="btn" style="width:100%;margin-top:8px" @click="switchMode('agent');send()">◌ 换用 Agent 深入回答</button>
        </div>
      </div>

      <div v-if="agentResult && mode === 'agent'" class="grid g2" style="margin-top:16px">
        <div class="panel pad">
          <div class="sechead" style="margin-top:0"><h3>这次回答做了什么</h3></div>
          <div class="listitem"><b>Agent</b><p>{{ agentResult.agent }} · {{ agentResult.framework || 'AgentScope' }}</p></div>
          <div class="listitem"><b>可用工具</b><p>search_knowledge · get_entity · get_entity_graph · list_skills</p></div>
          <div class="listitem"><b>运行记录</b><p>已写入任务列表（llm_runs · task_type=agent）</p></div>
        </div>
        <div>
          <div class="sechead" style="margin-top:0"><h3>下一步</h3></div>
          <button class="btn" style="width:100%" @click="recent.length && (runDrawerId = recent[0].id)">查看本次运行明细</button>
          <button class="btn" style="width:100%;margin-top:8px" @click="router.push('/agent')">◌ 打开 Agent 工作台</button>
          <button class="btn" style="width:100%;margin-top:8px" @click="switchMode('knowledge');send()">◎ 换用知识库问答（带证据）</button>
        </div>
      </div>

      <div class="sechead"><h3>最近问答</h3><button class="btn sm" @click="loadMeta">刷新</button></div>
      <div class="panel pad" style="padding:6px">
        <DataTable :columns="columns" :rows="recent" clickable @row-click="(r: Run) => (runDrawerId = r.id)">
          <template #cell-task_type="{ row }">
            <span class="tag" :class="row.task_type === 'agent' ? 'violet' : 'blue'">{{ row.task_type === 'agent' ? 'Agent · ' + (row.agent_role || '') : '知识库' }}</span>
          </template>
          <template #cell-question="{ row }"><b>{{ row.summary?.question || (row.task_type === 'agent' ? '(Agent 对话)' : '(历史记录)') }}</b></template>
          <template #cell-status="{ row }"><StatusTag :status="row.status" /></template>
          <template #cell-created_at="{ row }">{{ fmtDateTime(row.created_at) }}</template>
        </DataTable>
      </div>
    </div>

    <RunDrawer :run-id="runDrawerId" @close="runDrawerId = null" />
  </div>
</template>
