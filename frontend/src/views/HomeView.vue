<script setup lang="ts">
/* 首页 = 一个输入，两种动作：把东西交给我，或者问一个问题。
 *
 * 空知识库不再展示六个空面板。第一次来的人只需要做一件事，
 * 所以第一屏只有那件事（拖入）+ 它的替代动作（提问）。
 * 「需要你判断的事」只在真的有事时才出现——那才是首页的职责。 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import type { DocumentRow, Entity } from '../api/types'
import ImportPanel from '../components/ImportPanel.vue'
import OneBox from '../components/OneBox.vue'
import StatusTag from '../components/StatusTag.vue'
import { fmtDateTime } from '../utils/time'

const router = useRouter()

const entities = ref<Entity[]>([])
const docs = ref<DocumentRow[]>([])
const openQuestions = ref(0)
const conflicts = ref(0)
const review = ref({ entities: 0, claims: 0, relations: 0 })
const staleCandidates = ref(0)
const changes = ref<{ day: string; added: number; updated: { label: string; claim_id: string | null }[]; evidenced: number } | null>(null)
/** A 7-day roll-up — null when the fetched window does not actually reach back 7 days. */
const window7 = ref<{ added: number; updated: number; evidenced: number } | null>(null)

/** What the system wants from the user, not how much data it holds. */
const pendingTotal = computed(() => review.value.entities + review.value.claims + review.value.relations)
const dueTotal = computed(() => pendingTotal.value + conflicts.value)

const DEFAULT_EXAMPLES = [
  '我的知识库里有哪些核心概念？',
  '最近导入的内容讲了什么？',
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

function dayOf(value: unknown) { return String(value || '').slice(0, 10) }

/** 最近知识变化 —— 不是「最近操作」。
 *
 * 用户在意的不是我们跑了什么任务，而是他的知识发生了什么：哪条被取代了、
 * 多了多少、哪些拿到了新证据。这些事实后端全都有（审计轨迹 + 已接受的
 * duplicate 关系 = 同一断言又有了一个来源），只是从来没有人把它们读出来。
 * 这里只做翻译，不做推断：没有数据就不显示那一行。 */
async function loadChanges() {
  // 200 而不是 100：7 天窗口要用同一份数据算，取太少会把"新增"算小。
  const [ops, rels] = await Promise.all([
    api<any[]>('/api/knowledge/operations?limit=200').catch(() => []),
    api<any[]>('/api/claim-relations?status=accepted&limit=200').catch(() => []),
  ])
  const opList = ops || [], relList = rels || []

  // 只有当取回的记录确实覆盖到 7 天前时才给出 7 天数字。否则宁可整行不显示——
  // 一个被截断的"新增 42"比没有更糟，它看起来精确。
  const stamps = [...opList, ...relList].map(r => String(r.created_at || '')).filter(Boolean)
  const since = new Date(Date.now() - 7 * 86400e3).toISOString().slice(0, 19).replace('T', ' ')
  if (stamps.length && stamps.sort()[0] <= since) {
    const within = (r: any) => String(r.created_at || '') >= since
    window7.value = {
      added: opList.filter(o => within(o) && o.kind === 'CREATE').length,
      updated: opList.filter(o => within(o) && (o.kind === 'SUPERSEDE'
        || (o.kind === 'CORRECT' && o.result?.superseded_claim_id))).length,
      evidenced: relList.filter(r => within(r) && r.relationship === 'duplicate').length,
    }
  } else {
    window7.value = null
  }
  const newest = [...opList, ...relList].map(r => dayOf(r.created_at)).filter(Boolean).sort().pop()
  if (!newest) { changes.value = null; return }
  const onDay = (r: any) => dayOf(r.created_at) === newest
  const superseding = opList.filter(o => onDay(o) && (
    o.kind === 'SUPERSEDE' || (o.kind === 'CORRECT' && o.result?.superseded_claim_id)))
  changes.value = {
    // 「今天」只在真的匹配本地日期时才说，否则如实显示那一天。
    day: newest === dayOf(new Date().toISOString()) ? '今天' : newest,
    added: opList.filter(o => onDay(o) && o.kind === 'CREATE').length,
    updated: superseding.slice(0, 3).map(o => ({
      label: o.payload?.subject ? `${o.payload.subject} 已更新` : '一条知识已更新',
      claim_id: o.result?.claim_id || o.result?.superseded_claim_id || null,
    })),
    evidenced: relList.filter(r => onDay(r) && r.relationship === 'duplicate').length,
  }
}

/** One list, so the panel can be empty, partial or full without special cases. */
const changeRows = computed(() => {
  const rows: { key: string; icon: string; cls: string; text: string; to?: any }[] = []
  if (changes.value) {
    for (const [i, u] of changes.value.updated.entries()) {
      rows.push({ key: 'u' + i, icon: '↻', cls: 'upd', text: u.label,
                  to: u.claim_id ? '/knowledge/claim/' + u.claim_id : undefined })
    }
    if (changes.value.added)
      rows.push({ key: 'added', icon: '+', cls: 'add', text: `${changes.value.added} 条新知识` })
    if (changes.value.evidenced)
      rows.push({ key: 'ev', icon: '✓', cls: 'ok', text: `${changes.value.evidenced} 条获得了新证据` })
  }
  if (conflicts.value)
    rows.push({ key: 'cf', icon: '⚠', cls: 'wrn', text: `${conflicts.value} 条与现有知识冲突`,
                to: { path: '/research', query: { tab: 'conflicts' } } })
  return rows
})

function gotoChange(r: { to?: any }) { if (r.to) router.push(r.to) }

/** Nothing has been given to it yet — so the page is only the way to give it something. */
const hasContent = computed(() =>
  !!(entities.value.length || docs.value.length || openQuestions.value || changeRows.value.length))

async function loadAll() {
  await Promise.all([
    loadChanges(),
    (async () => {
      const [es, ds, qs, cf, rv, health] = await Promise.all([
        api<Entity[]>('/api/entities?limit=6'),
        api<DocumentRow[]>('/api/documents?limit=8'),
        api<any[]>('/api/questions?limit=200'),
        api<any[]>('/api/conflicts'),
        api<any>('/api/review?limit=200'),
        api<any>('/api/knowledge/health').catch(() => null),
      ])
      entities.value = es; docs.value = ds
      openQuestions.value = qs.filter(q => q.status === 'open').length
      conflicts.value = cf.length
      review.value = {
        entities: rv.entities?.length || 0,
        claims: rv.claims?.length || 0,
        relations: rv.relations?.length || 0,
      }
      staleCandidates.value = health?.stale_candidates || 0
      // Example questions come from the user's own open questions when they have any.
      const fromUser = qs.filter(q => q.status === 'open').slice(0, 3).map(q => q.content)
      examples.value = fromUser.length ? fromUser : [...DEFAULT_EXAMPLES]
    })(),
  ])
}

onMounted(loadAll)

/** 首页示例问题走 One Box 的同一个入口：同一句话不该有两种行为。 */
const onebox = ref<{ run: (sentence: string) => Promise<void> } | null>(null)
/** Reading the source stays one click away — but it is not the post-import destination. */
function openDocument(id: string) {
  router.push({ path: '/knowledge', query: { doc: id } })
}
</script>

<template>
  <div class="page">
    <!-- 第一屏只回答一个问题：这东西要我怎么用 -->
    <div style="max-width:720px;margin:4vh auto 0;text-align:center">
      <div class="eyebrow">{{ greeting }}</div>
      <h1 style="font-size:30px;margin:10px 0 22px">把知识交给我</h1>

      <ImportPanel hero title="拖一篇文章、网页或一段文字到这里"
                   hint="Markdown / TXT / HTML，可一次拖入多个"
                   @open-document="openDocument"
                   @imported="loadAll" />

      <div class="orsep"><span>或者直接说一句话</span></div>

      <!-- 一个入口，四条路径：问、记、研究、纠正。判断与执行都在组件里，
           用已经存在的接口——首页不再自己决定一句话该去哪里。 -->
      <OneBox ref="onebox" />
      <div v-if="hasContent" class="row" style="justify-content:center;gap:8px;margin-top:14px;flex-wrap:wrap">
        <span v-for="e in examples" :key="e" class="tag" style="cursor:pointer" @click="onebox?.run(e)">{{ e }}</span>
      </div>
      <p v-else class="faint" style="font-size:10px;margin-top:14px;line-height:1.8">
        还没有内容时，先丢一篇进来。它会读过之后告诉你整理出了什么，之后你问它就有据可依。
      </p>
    </div>

    <!-- 需要你的判断 —— 只在真的有事时出现，这才是首页的职责 -->
    <div v-if="dueTotal" class="panel pad" style="max-width:720px;margin:34px auto 0">
      <div class="row">
        <h3 style="font-size:13px;margin:0">{{ dueTotal }} 项需要你的判断</h3>
        <div class="grow"></div>
        <button class="btn primary" @click="router.push('/review')">开始确认 →</button>
      </div>
      <div class="duerows">
        <div class="duerow"><span>候选实体</span><b>{{ review.entities }}</b></div>
        <div class="duerow"><span>待确认断言</span><b>{{ review.claims }}</b></div>
        <div class="duerow"><span>待确认关系</span><b>{{ review.relations }}</b></div>
        <div class="duerow clickable" :class="{ warn: conflicts > 0 }" @click="router.push({ path: '/research', query: { tab: 'conflicts' } })">
          <span>知识冲突</span><b>{{ conflicts }}</b>
        </div>
        <div v-if="staleCandidates" class="duerow"><span>陈旧候选（&gt;90 天）</span><b>{{ staleCandidates }}</b></div>
      </div>
    </div>

    <div v-if="hasContent" class="grid g2" style="max-width:920px;margin:30px auto 0">
      <div>
        <div class="sechead"><h3>最近知识</h3><span class="more" @click="router.push('/knowledge')">进入知识空间 →</span></div>
        <div class="panel pad" style="padding:6px">
          <div v-for="e in entities" :key="e.id" class="item" @click="router.push('/knowledge/object/' + e.id)">
            <div class="ico-badge ib-blue">✦</div>
            <div class="grow"><b>{{ e.name }}</b><p>{{ e.type }} · {{ e.description?.slice(0, 40) || '—' }}</p></div>
            <StatusTag :status="e.status" />
          </div>
          <div v-if="!entities.length" class="empty">还没有知识对象</div>
        </div>
      </div>
      <div>
        <!-- 知识变化，而不是操作日志：这是「它会自己维护」第一次被用户看见的地方 -->
        <div class="sechead">
          <h3>最近知识变化</h3>
          <span v-if="changes" class="faint" style="font-size:9px">{{ changes.day }}</span>
        </div>
        <div class="panel pad">
          <div v-for="r in changeRows" :key="r.key" class="chgrow" :class="[r.cls, { clickable: !!r.to }]"
               @click="gotoChange(r)">
            <span class="ci">{{ r.icon }}</span><b>{{ r.text }}</b>
          </div>
          <div v-if="!changeRows.length" class="empty">还没有知识变化</div>
          <!-- 一眼看出它不是静态数据库，而是在持续工作 -->
          <div v-if="window7" class="w7">
            <span class="wlbl">最近 7 天</span>
            <span>新增 <b>{{ window7.added }}</b></span>
            <span>更新 <b>{{ window7.updated }}</b></span>
            <span>获得新证据 <b>{{ window7.evidenced }}</b></span>
            <span v-if="dueTotal">待确认 <b>{{ dueTotal }}</b></span>
          </div>
        </div>

        <div class="sechead"><h3>开放问题</h3><span class="more" @click="router.push('/research')">进入研究空间 →</span></div>
        <div class="panel pad" style="padding:6px">
          <div class="item" @click="router.push('/research')">
            <div class="ico-badge ib-blue">◇</div>
            <div class="grow"><b>{{ openQuestions }} 个开放问题</b><p>在研究空间查看与继续研究</p></div>
            <span class="tag blue">open</span>
          </div>
        </div>

        <div v-if="docs.length" class="sechead"><h3>最近加入</h3><span class="more" @click="router.push('/knowledge')">全部 →</span></div>
        <div v-if="docs.length" class="panel pad" style="padding:6px">
          <div v-for="d in docs.slice(0, 4)" :key="d.id" class="item" @click="openDocument(d.id)">
            <div class="ico-badge ib-blue">▤</div>
            <div class="grow"><b>{{ d.title }}</b><p>{{ d.chunk_count }} 片段 · {{ fmtDateTime(d.updated_at) }}</p></div>
            <StatusTag v-if="d.last_run_status" :status="d.last_run_status" /><span v-else class="tag amber">未抽取</span>
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
/* 「把知识交给我」与「问一个问题」之间：两个平级动作，不是主次 */
.orsep{display:flex;align-items:center;gap:12px;margin:22px 0 14px;color:var(--faint);font-size:10px}
.orsep:before,.orsep:after{content:"";flex:1;height:1px;background:var(--hair)}
/* 最近知识变化 */
.chgrow{display:flex;align-items:center;gap:9px;padding:7px 4px;font-size:11.5px;border-bottom:1px solid var(--hair)}
.chgrow:last-child{border-bottom:none}
.chgrow.clickable{cursor:pointer}
.chgrow.clickable:hover{color:#4a63e8}
.ci{width:15px;text-align:center;font-size:12px;flex:none}
.chgrow.upd .ci{color:#4a63e8}
.chgrow.add .ci{color:#3aa76d}
.chgrow.ok .ci{color:#3aa76d}
.chgrow.wrn .ci{color:var(--amber)}
.w7{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;margin-top:12px;padding-top:11px;border-top:1px solid var(--hair);font-size:10px;color:var(--sub)}
.w7 .wlbl{font-size:8.5px;letter-spacing:.08em;font-weight:700;color:var(--faint)}
.w7 b{font-size:12px;color:var(--text)}
</style>
