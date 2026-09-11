<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, post } from '../api/client'
import type { DocumentRow, Entity, Run } from '../api/types'
import KpiStrip from '../components/KpiStrip.vue'
import StatusTag from '../components/StatusTag.vue'
import { useAppStore } from '../stores/app'
import { fmtDateTime } from '../utils/time'

const router = useRouter()
const store = useAppStore()
const question = ref('')
const stats = ref<Record<string, number>>({})
const entities = ref<Entity[]>([])
const docs = ref<DocumentRow[]>([])
const runs = ref<Run[]>([])
const openQuestions = ref(0)
const conflicts = ref(0)

const EXAMPLES = [
  '为什么 RAG 不一定提高回答质量？',
  'GOMS 分页接口的调用链是什么？',
  'AgentScope 的结构化输出如何实现？',
]

onMounted(async () => {
  const [s, es, ds, rs, qs, cf] = await Promise.all([
    api<Record<string, number>>('/api/stats'),
    api<Entity[]>('/api/entities?limit=6'),
    api<DocumentRow[]>('/api/documents?limit=8'),
    api<Run[]>('/api/runs?limit=8'),
    api<any[]>('/api/questions?limit=200'),
    api<any[]>('/api/conflicts'),
  ])
  stats.value = s; entities.value = es; docs.value = ds; runs.value = rs
  openQuestions.value = qs.filter(q => q.status === 'open').length
  conflicts.value = cf.length
})

function ask(q?: string) {
  const text = (q ?? question.value).trim()
  if (!text) return
  router.push({ path: '/qa', query: { q: text } })
}

async function indexDoc(d: DocumentRow) {
  try { const r = await post(`/api/documents/${d.id}/index`); store.toast('抽取完成：' + JSON.stringify(r).slice(0, 60)) } catch (e: any) { store.toast(e.message) }
}
</script>

<template>
  <div class="page">
    <div style="max-width:720px;margin:6vh auto 0;text-align:center">
      <div class="eyebrow">Good afternoon</div>
      <h1 style="font-size:30px;margin:10px 0 24px">What do you want to know?</h1>
      <div class="askbox">
        <input v-model="question" placeholder="Ask anything about your knowledge…" @keydown.enter="ask()" />
        <button class="go" @click="ask()">↑</button>
      </div>
      <div class="row" style="justify-content:center;gap:8px;margin-top:14px;flex-wrap:wrap">
        <span v-for="e in EXAMPLES" :key="e" class="tag" style="cursor:pointer" @click="ask(e)">{{ e }}</span>
      </div>
    </div>

    <KpiStrip style="max-width:720px;margin:40px auto 0" :cells="[
      { label: '知识对象', value: stats.entities ?? '—', small: `Documents ${stats.documents ?? 0}` },
      { label: 'Claims', value: stats.claims ?? '—', small: `Relations ${stats.relations ?? 0}` },
      { label: '开放问题', value: openQuestions, small: `${conflicts} 个冲突`, warn: conflicts > 0 },
      { label: '证据覆盖', value: '—', small: `研究页查看 Health` },
    ]" />

    <div class="grid g2" style="margin-top:34px">
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
        <div class="sechead"><h3>研究</h3><span class="more" @click="router.push('/research')">进入研究空间 →</span></div>
        <div class="panel pad" style="padding:6px">
          <div class="item" @click="router.push('/research')">
            <div class="ico-badge ib-blue">◇</div><div class="grow"><b>{{ openQuestions }} 个开放问题</b><p>在研究空间查看与继续研究</p></div><span class="tag blue">open</span>
          </div>
          <div class="item" @click="router.push({ path: '/research', query: { tab: 'conflicts' } })">
            <div class="ico-badge ib-amber">⚠</div><div class="grow"><b>{{ conflicts }} 个知识冲突</b><p>同一主题存在不同结论的 Claims</p></div><span class="tag amber">conflict</span>
          </div>
        </div>
        <div class="sechead"><h3>最近动态</h3></div>
        <div class="panel pad">
          <div class="timeline">
            <div v-for="r in runs" :key="r.id" class="tle">
              <b>{{ r.task_type }} · {{ r.document_title || r.agent_role }}</b>
              <div>{{ fmtDateTime(r.created_at) }} · {{ r.step_count }} steps · {{ r.duration_ms ?? '—' }} ms <StatusTag :status="r.status" /></div>
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
