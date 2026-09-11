<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, put, post } from '../api/client'
import type { ContextRun, PromptProfile, Run } from '../api/types'
import PageHead from '../components/PageHead.vue'
import StatusTag from '../components/StatusTag.vue'
import AppDrawer from '../components/AppDrawer.vue'
import RunDrawer from '../components/RunDrawer.vue'
import EmptyState from '../components/EmptyState.vue'
import { useAppStore } from '../stores/app'
import { fmtDateTime } from '../utils/time'

const route = useRoute()
const store = useAppStore()

interface Agent { id: string; name: string; description: string; color: string }
const AGENT_COLORS: Record<string, string> = {
  personal: '#5b7cff', knowledge: '#8b67f7', research: '#2fae87',
  curator: '#d99338', review: '#e46a85', extractor: '#5ac7e8',
}
const agents = ref<Agent[]>([])
const current = ref<Agent | null>(null)
const tab = ref(route.query.tab === 'prompt' ? 'Prompt' : 'Overview')
const TAB_LABELS: Record<string, string> = { Overview: '概览', Prompt: 'Prompt', Skills: 'Skills', Tools: '工具', Runs: '运行记录', Context: 'Context' }

const profile = ref<PromptProfile | null>(null)
const draft = ref('')
const preview = ref(''); const showPreview = ref(false)
const skills = ref<{ name: string; purpose: string }[]>([])
const skillDetail = ref<{ name: string; content: string } | null>(null)
const runs = ref<Run[]>([])
const runDrawerId = ref<string | null>(null)

const TOOLS = [
  { name: 'search_knowledge', desc: '混合检索：FTS5 + Semantic + RRF · read-only' },
  { name: 'get_entity', desc: '读取实体详情与别名 · read-only' },
  { name: 'get_entity_graph', desc: '实体一跳邻居图谱 · read-only' },
  { name: 'list_skills', desc: '列出可用 Skill' },
  { name: 'read_skill_reference', desc: '读取 Skill 参考文档' },
]

async function load() {
  const roles = await api<{ roles: Agent[] }>('/api/agent/roles')
  agents.value = roles.roles.map(r => ({ ...r, color: AGENT_COLORS[r.id] || '#8b67f7' }))
  skills.value = await api<any[]>('/api/skills')
  runs.value = await api<Run[]>('/api/runs?task_type=agent&limit=100')
  if (!current.value && agents.value.length) pick(agents.value[0])
}
onMounted(load)
watch(() => route.query.tab, t => { if (t === 'prompt') tab.value = 'Prompt' })

async function pick(a: Agent) {
  current.value = a
  tab.value = route.query.tab === 'prompt' ? 'Prompt' : 'Overview'
  try { profile.value = await api<PromptProfile>('/api/agent/prompts/' + a.id); draft.value = profile.value?.custom_prompt || '' } catch { profile.value = null }
}

async function savePrompt() {
  if (!current.value) return
  try { await put('/api/agent/prompts/' + current.value.id, { custom_prompt: draft.value, note: 'edited in UI' }); store.toast('Prompt 已保存，新请求立即生效'); await pick(current.value) } catch (e: any) { store.toast(e.message) }
}
async function resetPrompt() {
  if (!current.value || !confirm('恢复默认 Prompt？')) return
  try { await post('/api/agent/prompts/' + current.value.id + '/reset'); store.toast('已恢复默认'); await pick(current.value) } catch (e: any) { store.toast(e.message) }
}
async function togglePreview() {
  if (!current.value) return
  if (showPreview.value) { showPreview.value = false; return }
  const p = await api<PromptProfile>('/api/agent/prompts/' + current.value.id)
  preview.value = p.effective_prompt_preview || ''; showPreview.value = true
}
async function restoreVersion(version: number) {
  if (!current.value) return
  if (!confirm(`恢复到 v${version}？当前内容会先保存为一个新版本。`)) return
  try {
    await post('/api/agent/prompts/' + current.value.id + '/restore', { version })
    store.toast('已恢复到 v' + version)
    await pick(current.value)
  } catch (e: any) { store.toast(e.message) }
}
async function openSkill(name: string) {
  try {
    skillDetail.value = await api<{ name: string; content: string }>('/api/skills/' + name)
  } catch (e: any) { store.toast(e.message) }
}
const agentRuns = () => runs.value.filter(r => current.value && (r.agent_role === current.value.name || r.agent_role === current.value.id))

/* ---------------- Context tab (P4: observability UI) ---------------- */
const ctxMetrics = ref<any>(null)
const ctxRuns = ref<ContextRun[]>([])
const ctxCache = ref<{ entries: number; hits: number; misses: number; hit_rate: number } | null>(null)
const ctxWindow = ref<7 | 14 | 30>(14)
const ctxAllAgents = ref(false)
const ctxLoading = ref(false)
const ctxLoaded = ref(false)
const inspectorRun = ref<ContextRun | null>(null)
const inspectorDetail = ref<any>(null)

async function loadContext() {
  ctxLoading.value = true
  try {
    const [metrics, runsList, cache] = await Promise.all([
      api<any>('/api/context/metrics?days=' + ctxWindow.value),
      api<ContextRun[]>('/api/context/runs?limit=100'),
      // Optional: an older backend without the cache endpoint must not kill the tab.
      api<any>('/api/context/cache').catch(() => null),
    ])
    ctxMetrics.value = metrics
    ctxRuns.value = runsList
    ctxCache.value = cache
    ctxLoaded.value = true
  } catch (e: any) { store.toast(e.message) } finally { ctxLoading.value = false }
}

async function inspect(r: ContextRun) {
  inspectorRun.value = r
  inspectorDetail.value = null
  try { inspectorDetail.value = await api<any>('/api/context/runs/' + r.id) }
  catch (e: any) { store.toast(e.message) }
}

const ctxVisibleRuns = () => ctxAllAgents.value
  ? ctxRuns.value
  : ctxRuns.value.filter(r => r.agent_name === current.value?.name)

const ctxOverallEfficiency = computed(() => {
  const list = ctxMetrics.value?.agents || []
  const calls = list.reduce((s: number, a: any) => s + (a.calls || 0), 0)
  if (!calls) return null
  return list.reduce((s: number, a: any) => s + (a.avg_efficiency || 0) * a.calls, 0) / calls
})

const overallEfficiencyPct = computed(() =>
  ctxOverallEfficiency.value == null ? '—' : Math.round(ctxOverallEfficiency.value * 100) + '%')

/* Prompt tab: token panel geometry */
const customBarPct = computed(() => {
  const p = profile.value
  if (!p?.custom_prompt_tokens || !p?.recommended_max_tokens) return 0
  return Math.min(100, Math.round(p.custom_prompt_tokens / p.recommended_max_tokens * 100))
})
const customBarColor = computed(() => {
  const p = profile.value
  const over = p && p.custom_prompt_tokens && p.recommended_max_tokens
    ? p.custom_prompt_tokens / p.recommended_max_tokens : 0
  if (over > 1) return '#e46a85'
  if (over > 0.8) return '#d99338'
  return '#2fae87'
})

watch(tab, t => { if (t === 'Context' && !ctxLoaded.value) loadContext() })
watch(ctxWindow, () => { if (tab.value === 'Context') loadContext() })
</script>

<template>
  <div class="page">
    <PageHead title="Agent 工作台" subtitle="六个智能体、它们的能力、行为配置与运行历史。" />
    <div style="display:grid;grid-template-columns:250px minmax(0,1fr);gap:18px" class="agent-grid">
      <div class="panel pad" style="padding:8px;align-self:start">
        <div v-for="a in agents" :key="a.id" class="agrow" :class="{ active: current?.id === a.id }" @click="pick(a)">
          <div class="agavatar" :style="{ background: a.color }">{{ a.name[0] }}</div>
          <div><b>{{ a.name }}</b><small>{{ a.description }}</small></div>
        </div>
      </div>

      <div v-if="current">
        <div class="seg" style="margin:16px 0 14px">
          <button v-for="t in ['Overview', 'Prompt', 'Skills', 'Tools', 'Runs', 'Context']" :key="t" :class="{ active: tab === t }" @click="tab = t">{{ TAB_LABELS[t] }}</button>
        </div>

        <!-- Overview -->
        <div v-if="tab === 'Overview'" class="panel pad">
          <div class="row">
            <div class="agavatar" :style="{ background: current.color, width: '44px', height: '44px', fontSize: '16px' }">{{ current.name[0] }}</div>
            <div><b style="font-size:14px">{{ current.name }}</b><div class="faint" style="font-size:9px;margin-top:3px">{{ current.description }}</div></div>
          </div>
          <hr class="hairline" />
          <div class="kv" style="grid-template-columns:120px 1fr;font-size:10px">
            <span>Role</span><b>{{ current.id }}</b>
            <span>Tools</span><b>{{ TOOLS.length }} 个只读工具</b>
            <span>Agent Runs</span><b>{{ agentRuns().length }}</b>
          </div>
        </div>

        <!-- Prompt -->
        <div v-if="tab === 'Prompt'" class="panel pad">
          <div class="sechead" style="margin:0 0 12px"><h3>Effective Prompt 组成</h3><button class="btn sm" @click="togglePreview">预览组合结果</button></div>
          <div class="pipeline">
            <div class="pnode lock">System Contract<span class="lockico">🔒</span></div><span class="farrow">→</span>
            <div class="pnode skill">Skills</div><span class="farrow">→</span>
            <div class="pnode custom">Custom Prompt</div><span class="farrow">→</span>
            <div class="pnode io">Runtime Input</div>
          </div>
          <div class="notice violet" style="margin-top:12px">System Contract（Ontology · Schema · Evidence · Registry · Safety）不可编辑；你能调整的是 Custom Prompt。</div>

          <!-- Context Token panel (P4) -->
          <div v-if="profile?.context_sections" class="panel pad" style="margin-top:12px">
            <div class="sechead" style="margin:0 0 10px">
              <h3>Context Token 面板</h3>
              <span class="tag" :class="profile.prompt_status === 'efficient' ? 'green' : 'amber'">
                {{ profile.prompt_status === 'efficient' ? '健康' : '偏长' }}
              </span>
            </div>
            <div class="kv" style="grid-template-columns:150px 1fr;font-size:10px">
              <span>Context 总量</span><b>{{ profile.context_tokens }} / 预算 {{ profile.context_budget }} tokens</b>
              <span>Custom Prompt</span><b>{{ profile.custom_prompt_tokens }} / 建议上限 {{ profile.recommended_max_tokens }} tokens</b>
            </div>
            <div style="margin:10px 0 4px;height:6px;background:var(--hair);border-radius:99px;overflow:hidden">
              <div :style="{ width: customBarPct + '%', background: customBarColor, height: '100%' }"></div>
            </div>
            <div v-if="profile.prompt_suggestion" class="notice amber" style="margin-top:10px">{{ profile.prompt_suggestion }}</div>
            <div class="sechead" style="margin:14px 0 6px"><h3 style="font-size:10px">分区成本（View · Token cost · Source）</h3></div>
            <div v-for="s in profile.context_sections" :key="s.name" class="row" style="font-size:9.5px;padding:4px 0">
              <span class="tag" :class="s.required ? 'blue' : ''">{{ s.name }}</span>
              <span class="faint" style="flex:none">{{ s.tokens }} tokens</span>
              <div class="grow"></div>
              <span class="faint" style="max-width:45%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" :title="s.source">{{ s.source }}</span>
            </div>
            <div v-if="(profile.context_withheld || []).length" class="sechead" style="margin:14px 0 6px">
              <h3 style="font-size:10px">刻意未加载 · {{ profile.context_withheld?.length }} 项</h3>
            </div>
            <div v-for="(w, i) in (profile.context_withheld || []).slice(0, 6)" :key="i" class="row" style="font-size:9px;padding:2px 0">
              <span class="tag">{{ w.name }}</span>
              <span class="faint">省 {{ w.tokens }} tokens · {{ w.reason }}</span>
            </div>
          </div>

          <div v-if="profile">
            <div class="sechead"><h3>Custom Prompt</h3><span class="faint" style="font-size:9px;font-weight:400">{{ (profile.history || []).length }} 个已存版本</span></div>
            <textarea v-model="draft" class="promptbox" style="width:100%;height:200px" spellcheck="false"></textarea>
            <div v-if="showPreview" class="trace" style="margin-top:10px">{{ preview || '(空)' }}</div>
            <div class="row" style="margin-top:10px;justify-content:flex-end;gap:8px">
              <button class="btn" @click="resetPrompt">恢复默认</button>
              <button class="btn primary" @click="savePrompt">保存</button>
            </div>
            <div v-if="(profile.history || []).length" class="sechead"><h3>版本历史</h3></div>
            <div v-for="h in profile.history" :key="h.version" class="historyrow">
              <div class="version">v{{ h.version }}</div>
              <div><b style="font-size:9px">{{ h.note || '已保存' }}</b><div class="muted" style="font-size:8px;margin-top:3px">{{ fmtDateTime(h.created_at) }}</div></div>
              <div class="grow"></div>
              <button class="btn sm" @click="restoreVersion(h.version)">恢复此版本</button>
            </div>
          </div>
        </div>

        <!-- Skills -->
        <div v-if="tab === 'Skills'" class="grid g2">
          <div v-for="s in skills" :key="s.name" class="panel pad">
            <div class="row"><div class="ico-badge ib-mint">◈</div><b style="font-size:11.5px">{{ s.name }}</b></div>
            <hr class="hairline" />
            <div class="kv" style="grid-template-columns:80px 1fr;font-size:9.5px">
              <span>Purpose</span><b>{{ s.purpose || '—' }}</b>
              <span>Version</span><b>v1.2</b>
            </div>
            <div class="row" style="margin-top:10px"><button class="btn sm" @click="openSkill(s.name)">查看 SKILL.md</button><span class="tag green" style="margin-left:auto">ON</span></div>
          </div>
          <EmptyState v-if="!skills.length" text="未找到 Skill 目录" />
        </div>

        <!-- Tools -->
        <div v-if="tab === 'Tools'" class="panel pad" style="padding:6px">
          <div v-for="t in TOOLS" :key="t.name" class="item" style="cursor:default">
            <div class="ico-badge ib-blue">⌕</div>
            <div class="grow"><b>{{ t.name }}</b><p>{{ t.desc }}</p></div>
            <span class="tag green" style="margin-left:auto">always allowed</span>
          </div>
        </div>

        <!-- Runs -->
        <div v-if="tab === 'Runs'" class="panel pad" style="padding:6px">
          <div v-for="r in agentRuns()" :key="r.id" class="item" @click="runDrawerId = r.id">
            <div class="ico-badge ib-violet">◒</div>
            <div class="grow"><b>{{ r.document_title || r.summary?.answer_chars + ' 字回答' || '(对话)' }}</b><p>{{ fmtDateTime(r.created_at) }} · {{ r.step_count }} steps · {{ r.duration_ms ?? '—' }} ms</p></div>
            <StatusTag :status="r.status" />
          </div>
          <EmptyState v-if="!agentRuns().length" text="该 Agent 还没有运行记录" />
        </div>

        <!-- Context (P4: observability) -->
        <div v-if="tab === 'Context'">
          <div class="row" style="justify-content:space-between;margin-bottom:12px">
            <div class="seg" style="padding:3px">
              <button v-for="d in [7, 14, 30]" :key="d" :class="{ active: ctxWindow === d }" @click="ctxWindow = d">{{ d }} 天</button>
            </div>
            <div class="row" style="gap:10px">
              <label class="faint" style="font-size:9px;display:flex;align-items:center;gap:5px;cursor:pointer">
                <input type="checkbox" v-model="ctxAllAgents" /> 显示全部 Agent
              </label>
              <button class="btn sm" @click="loadContext">刷新</button>
            </div>
          </div>

          <EmptyState v-if="!ctxLoaded && !ctxLoading" text="点击「刷新」加载 Context 观测数据" />
          <template v-if="ctxMetrics">
            <!-- Efficiency / Token-per-X cards -->
            <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px" class="ctx-grid">
              <div class="panel pad" style="padding:10px">
                <div class="faint" style="font-size:8px;letter-spacing:.1em">CONTEXT CALLS</div>
                <b style="font-size:16px">{{ ctxMetrics.totals?.context_calls ?? 0 }}</b>
              </div>
              <div class="panel pad" style="padding:10px">
                <div class="faint" style="font-size:8px;letter-spacing:.1em">CONTEXT TOKENS</div>
                <b style="font-size:16px">{{ (ctxMetrics.totals?.context_tokens ?? 0).toLocaleString() }}</b>
              </div>
              <div class="panel pad" style="padding:10px">
                <div class="faint" style="font-size:8px;letter-spacing:.1em">AVG EFFICIENCY</div>
                <b style="font-size:16px">{{ overallEfficiencyPct }}</b>
              </div>
              <div class="panel pad" style="padding:10px">
                <div class="faint" style="font-size:8px;letter-spacing:.1em">CACHE HIT RATE</div>
                <b style="font-size:16px">{{ ctxCache ? Math.round(ctxCache.hit_rate * 100) + '%' : '—' }}</b>
                <div class="faint" style="font-size:8px;margin-top:2px">{{ ctxCache?.hits ?? 0 }} hits / {{ ctxCache?.misses ?? 0 }} misses</div>
              </div>
            </div>

            <!-- per-agent ledger -->
            <div class="panel pad" style="margin-top:14px;padding:6px">
              <div class="sechead" style="margin:4px 6px 8px"><h3>Per-Agent 账本</h3><span class="faint" style="font-size:9px">近 {{ ctxWindow }} 天</span></div>
              <div v-for="a in ctxMetrics.agents" :key="a.agent_name" class="row" style="font-size:9.5px;padding:5px 6px"
                   :style="a.agent_name === current?.name ? 'background:var(--tint-blue);border-radius:8px' : ''">
                <b style="min-width:110px">{{ a.agent_name }}</b>
                <span class="faint">{{ a.calls }} calls · avg {{ a.avg_context_tokens }} tokens</span>
                <div class="grow"></div>
                <span class="tag" :class="(a.avg_efficiency || 0) >= 0.5 ? 'green' : 'amber'">eff {{ Math.round((a.avg_efficiency || 0) * 100) }}%</span>
                <span v-if="a.over_budget_calls" class="tag red">超预算 {{ a.over_budget_calls }}</span>
              </div>
              <EmptyState v-if="!(ctxMetrics.agents || []).length" text="该时间窗内还没有 Context 记录" />
            </div>

            <!-- Token per task type -->
            <div class="panel pad" style="margin-top:14px;padding:6px">
              <div class="sechead" style="margin:4px 6px 8px"><h3>Token per Run（provider 实测）</h3></div>
              <div v-for="t in ctxMetrics.by_task_type || ctxMetrics.tasks || []" :key="t.task_type" class="row" style="font-size:9.5px;padding:5px 6px">
                <span class="tag blue">{{ t.task_type }}</span>
                <span class="faint">{{ t.runs }} runs · {{ (t.total_tokens ?? 0).toLocaleString() }} tokens</span>
                <div class="grow"></div>
                <b>{{ t.tokens_per_run ?? '—' }} tokens/run</b>
              </div>
            </div>

            <!-- Context runs → Inspector -->
            <div class="panel pad" style="margin-top:14px;padding:6px">
              <div class="sechead" style="margin:4px 6px 8px">
                <h3>Context Runs（点击打开 Inspector）</h3>
                <span class="faint" style="font-size:9px">{{ ctxVisibleRuns().length }} 条{{ ctxAllAgents ? '' : ' · 当前 Agent' }}</span>
              </div>
              <div v-for="r in ctxVisibleRuns()" :key="r.id" class="item" style="cursor:default" @click="inspect(r)">
                <div class="ico-badge" :class="r.over_budget ? 'ib-amber' : 'ib-blue'">▤</div>
                <div class="grow">
                  <b>{{ r.agent_name }}</b>
                  <p>{{ fmtDateTime(r.created_at) }} · {{ r.actual_tokens }} / {{ r.budget_tokens }} tokens · 省 {{ r.trimmed_tokens }}</p>
                  <div style="margin-top:4px;height:4px;background:var(--hair);border-radius:99px;overflow:hidden;max-width:260px">
                    <div :style="{ width: Math.min(100, Math.round(r.actual_tokens / Math.max(1, r.budget_tokens) * 100)) + '%', background: r.over_budget ? '#e46a85' : '#5b7cff', height: '100%' }"></div>
                  </div>
                </div>
                <span class="tag" :class="(r.efficiency || 0) >= 0.5 ? 'green' : 'amber'">eff {{ Math.round((r.efficiency || 0) * 100) }}%</span>
                <span v-if="r.over_budget" class="tag red">超预算</span>
              </div>
              <EmptyState v-if="!ctxVisibleRuns().length" text="当前 Agent 还没有 Context 记录——先在问答页跑一次 Agent" />
            </div>
          </template>
        </div>
      </div>
    </div>

    <AppDrawer :open="!!skillDetail" :title="skillDetail?.name || ''" @close="skillDetail = null">
      <div v-if="skillDetail" class="evidence" style="white-space:pre-wrap">{{ skillDetail.content }}</div>
    </AppDrawer>
    <AppDrawer :open="!!inspectorRun" title="Context Inspector" @close="inspectorRun = null">
      <div v-if="inspectorRun">
        <div class="kv" style="grid-template-columns:120px 1fr;font-size:10px">
          <span>Agent</span><b>{{ inspectorRun.agent_name }}</b>
          <span>时间</span><b>{{ fmtDateTime(inspectorRun.created_at) }}</b>
          <span>预算 / 实际</span><b>{{ inspectorRun.budget_tokens }} / {{ inspectorRun.actual_tokens }} tokens</b>
          <span>Efficiency</span><b>{{ Math.round((inspectorRun.efficiency || 0) * 100) }}%</b>
          <span>Trimmed</span><b>{{ inspectorRun.trimmed_tokens }} tokens</b>
          <span>关联 Run</span><b style="word-break:break-all">{{ inspectorRun.run_id || '—' }}</b>
        </div>
        <div class="sechead" style="margin:16px 0 8px"><h3 style="font-size:10px">分区 · 为什么加载 · 成本 · 来源</h3></div>
        <div v-if="inspectorDetail">
          <div v-for="s in inspectorDetail.sections" :key="s.name + s.section_index" class="listitem" style="margin-bottom:6px">
            <div class="row" style="font-size:10px">
              <b>{{ s.name }}</b>
              <span class="tag" :class="s.policy === 'load' ? 'green' : 'amber'">{{ s.policy }}</span>
              <span v-if="s.trimmed" class="tag amber">已裁剪</span>
              <div class="grow"></div>
              <b style="flex:none">{{ s.tokens }} tokens</b>
            </div>
            <p style="margin:4px 0 0">{{ s.reason || '—' }}</p>
            <p class="faint" style="margin:2px 0 0;font-size:8.5px;word-break:break-all">source: {{ s.source }}</p>
          </div>
          <EmptyState v-if="!(inspectorDetail.sections || []).length" text="这条记录没有分区明细" />
        </div>
        <div v-else class="faint" style="font-size:10px">加载中…</div>
        <template v-if="(inspectorRun.withheld || []).length">
          <div class="sechead" style="margin:16px 0 8px"><h3 style="font-size:10px">刻意未加载 · {{ inspectorRun.withheld.length }} 项</h3></div>
          <div v-for="(w, i) in inspectorRun.withheld" :key="i" class="row" style="font-size:9px;padding:3px 0">
            <span class="tag">{{ w.name || w.id }}</span>
            <span class="faint">省 {{ w.tokens }} tokens · {{ w.reason }}</span>
          </div>
        </template>
        <template v-if="(inspectorRun.optimizations || []).length">
          <div class="sechead" style="margin:16px 0 8px"><h3 style="font-size:10px">优化动作</h3></div>
          <div v-for="(o, i) in inspectorRun.optimizations" :key="i" class="faint" style="font-size:9px;padding:2px 0">· {{ o }}</div>
        </template>
      </div>
    </AppDrawer>
    <RunDrawer :run-id="runDrawerId" @close="runDrawerId = null" />
  </div>
</template>

<style scoped>
@media(max-width:920px){
  .agent-grid{grid-template-columns:1fr!important}
  .ctx-grid{grid-template-columns:repeat(2,1fr)!important}
}
</style>
