<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, post } from '../api/client'
import type { DocumentRow, Entity, Run } from '../api/types'
import StatusTag from '../components/StatusTag.vue'
import { useAppStore } from '../stores/app'
import { fmtDateTime, fmtRelative } from '../utils/time'

const router = useRouter()
const store = useAppStore()
const question = ref('')
const entities = ref<Entity[]>([])
const docs = ref<DocumentRow[]>([])
const runs = ref<Run[]>([])
const openQuestions = ref(0)
const conflicts = ref(0)
const review = ref({ entities: 0, claims: 0, relations: 0 })
const staleCandidates = ref(0)

/** What the system wants from the user, not how much data it holds. */
const pendingTotal = computed(() => review.value.entities + review.value.claims + review.value.relations)
const dueTotal = computed(() => pendingTotal.value + conflicts.value)

const DEFAULT_EXAMPLES = [
  '我的知识库里有哪些核心概念？',
  '最近导入的文档讲了什么？',
  '有哪些结论在不同来源里互相冲突？',
]
const examples = ref<string[]>([...DEFAULT_EXAMPLES])

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 5) return '夜深了'
  if (h < 12) return '早上好'
  if (h < 18) return '下午好'
  return '晚上好'
})

const TASK_LABEL: Record<string, string> = {
  extract: '分析了文档', ask: '回答了一个问题', agent: 'Agent 对话', research: '执行了研究',
}
function activityLabel(r: Run) {
  const what = TASK_LABEL[r.task_type] || r.task_type
  const target = r.document_title ? `《${r.document_title}》` : (r.agent_role ? `${r.agent_role} Agent` : '')
  return target ? `${what} · ${target}` : what
}

onMounted(async () => {
  const [es, ds, rs, qs, cf, rv, health] = await Promise.all([
    api<Entity[]>('/api/entities?limit=6'),
    api<DocumentRow[]>('/api/documents?limit=8'),
    api<Run[]>('/api/runs?limit=8'),
    api<any[]>('/api/questions?limit=200'),
    api<any[]>('/api/conflicts'),
    api<any>('/api/review?limit=200'),
    api<any>('/api/knowledge/health').catch(() => null),
  ])
  entities.value = es; docs.value = ds; runs.value = rs
  const open = qs.filter(q => q.status === 'open')
  openQuestions.value = open.length
  conflicts.value = cf.length
  review.value = {
    entities: rv.entities?.length || 0,
    claims: rv.claims?.length || 0,
    relations: rv.relations?.length || 0,
  }
  staleCandidates.value = health?.stale_candidates || 0
  // Example questions come from the user's own open questions when they have any.
  const fromUser = open.slice(0, 3).map(q => q.content)
  examples.value = fromUser.length ? fromUser : [...DEFAULT_EXAMPLES]
})

function ask(q?: string) {
  const text = (q ?? question.value).trim()
  if (!text) return
  router.push({ path: '/qa', query: { q: text } })
}

async function indexDoc(d: DocumentRow) {
  store.beginExtraction(d.id, d.title)
  try {
    await post(`/api/documents/${d.id}/index`)
    store.clearExtraction()
    store.toast(`《${d.title}》抽取完成`, { label: '去审核', run: () => router.push('/review') })
  } catch (e: any) {
    store.clearExtraction()
    store.toast(e.message)
  }
}
</script>

<template>
  <div class="page">
    <div style="max-width:720px;margin:5vh auto 0;text-align:center">
      <div class="eyebrow">{{ greeting }}</div>
      <h1 style="font-size:30px;margin:10px 0 24px">What do you want to know?</h1>
      <div class="askbox">
        <input v-model="question" placeholder="Ask anything about your knowledge…" @keydown.enter="ask()" />
        <button class="go" @click="ask()">↑</button>
      </div>
      <div class="row" style="justify-content:center;gap:8px;margin-top:14px;flex-wrap:wrap">
        <span v-for="e in examples" :key="e" class="tag" style="cursor:pointer" @click="ask(e)">{{ e }}</span>
      </div>
    </div>

    <!-- What needs the user's attention — the dashboard's real job. -->
    <div class="panel pad" style="max-width:720px;margin:36px auto 0">
      <template v-if="dueTotal">
        <div class="row">
          <h3 style="font-size:13px;margin:0">{{ dueTotal }} 项需要你的判断</h3>
          <div class="grow"></div>
          <button class="btn primary" @click="router.push('/review')">开始审核 →</button>
        </div>
        <div class="duerows">
          <div class="duerow"><span>候选实体</span><b>{{ review.entities }}</b></div>
          <div class="duerow"><span>待审 Claims</span><b>{{ review.claims }}</b></div>
          <div class="duerow"><span>待审关系</span><b>{{ review.relations }}</b></div>
          <div class="duerow clickable" :class="{ warn: conflicts > 0 }" @click="router.push({ path: '/research', query: { tab: 'conflicts' } })">
            <span>知识冲突</span><b>{{ conflicts }}</b>
          </div>
          <div v-if="staleCandidates" class="duerow"><span>陈旧候选（&gt;90 天）</span><b>{{ staleCandidates }}</b></div>
        </div>
      </template>
      <template v-else>
        <div class="row">
          <h3 style="font-size:13px;margin:0">知识库已同步</h3>
          <div class="grow"></div>
          <button class="btn" @click="router.push('/knowledge')">导入文档</button>
        </div>
        <p class="muted" style="font-size:10px;margin:9px 0 0;line-height:1.7">
          没有等待你判断的候选知识。导入一篇文档，系统会抽取候选知识并在这里提醒你审核。
        </p>
      </template>
    </div>

    <div class="grid g2" style="max-width:920px;margin:30px auto 0">
      <div>
        <div class="sechead"><h3>最近知识</h3><span class="more" @click="router.push('/knowledge')">进入知识空间 →</span></div>
        <div class="panel pad" style="padding:6px">
          <div v-for="e in entities" :key="e.id" class="item" @click="router.push('/knowledge/object/' + e.id)">
            <div class="ico-badge ib-blue">✦</div>
            <div class="grow"><b>{{ e.name }}</b><p>{{ e.type }} · {{ e.description?.slice(0, 40) || '—' }}</p></div>
            <StatusTag :status="e.status" />
          </div>
          <div v-if="!entities.length" class="empty">还没有知识对象——去知识库导入文档并抽取</div>
        </div>
      </div>
      <div>
        <div class="sechead"><h3>开放问题</h3><span class="more" @click="router.push('/research')">进入研究空间 →</span></div>
        <div class="panel pad" style="padding:6px">
          <div class="item" @click="router.push('/research')">
            <div class="ico-badge ib-blue">◇</div>
            <div class="grow"><b>{{ openQuestions }} 个开放问题</b><p>在研究空间查看与继续研究</p></div>
            <span class="tag blue">open</span>
          </div>
        </div>
        <div class="sechead"><h3>最近动态</h3></div>
        <div class="panel pad">
          <div class="timeline">
            <div v-for="r in runs" :key="r.id" class="tle">
              <b>{{ activityLabel(r) }}</b>
              <div>{{ fmtRelative(r.created_at) }} <StatusTag :status="r.status" /></div>
            </div>
            <div v-if="!runs.length" class="empty" style="margin:10px">还没有活动</div>
          </div>
        </div>
        <div v-if="docs.length" class="sechead"><h3>最近文档</h3><span class="more" @click="router.push('/knowledge')">全部 →</span></div>
        <div v-if="docs.length" class="panel pad" style="padding:6px">
          <div v-for="d in docs.slice(0, 4)" :key="d.id" class="item" @click="router.push('/knowledge')">
            <div class="ico-badge ib-blue">▤</div>
            <div class="grow"><b>{{ d.title }}</b><p>{{ d.chunk_count }} chunks · {{ fmtDateTime(d.updated_at) }}</p></div>
            <button v-if="!d.last_run_status" class="btn sm" @click.stop="indexDoc(d)">Index</button>
            <StatusTag v-else :status="d.last_run_status" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.duerows{display:grid;grid-template-columns:repeat(auto-fit,minmax(128px,1fr));gap:10px;margin-top:14px}
.duerow{display:flex;justify-content:space-between;align-items:baseline;gap:8px;padding:9px 12px;background:var(--surface2);border-radius:10px;font-size:10px}
.duerow b{font-size:15px}
.duerow.warn b{color:var(--amber)}
.duerow.clickable{cursor:pointer}
</style>
