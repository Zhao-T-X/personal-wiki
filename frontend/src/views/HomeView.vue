<script setup lang="ts">
/* 首页 = 一个输入，两种动作：把东西交给我，或者问一个问题。
 *
 * 空知识库不再展示六个空面板。第一次来的人只需要做一件事，
 * 所以第一屏只有那件事（拖入）+ 它的替代动作（提问）。
 * 「需要你判断的事」只在真的有事时才出现——那才是首页的职责。
 *
 * 但"确实什么都没有"和"读不到"必须分开：一次失败的请求曾经把整页退回成
 * 新用户空库的首屏，那对一个知识库产品来说是能说出的最坏的一句话——用户会
 * 理解成"我的知识全没了"。所以首用提示只在 `success && 空` 时出现。 */
import { computed, onMounted, ref, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { CircleCheck, FileText, Plus, RefreshCw, Sparkles, Telescope, TriangleAlert } from 'lucide-vue-next'
import { api } from '../api/client'
import type { DocumentRow, Entity } from '../api/types'
import LoadBoundary from '../components/LoadBoundary.vue'
import OneBox from '../components/OneBox.vue'
import StatusTag from '../components/StatusTag.vue'
import { useAsyncState } from '../utils/useAsyncState'
import { fmtDateTime } from '../utils/time'
import { entityTypeLabel } from '../utils/entityType'

const router = useRouter()
const { t } = useI18n()

function defaultExamples(): string[] {
  return [t('home.example1'), t('home.example2'), t('home.example3')]
}

interface Changes {
  day: string
  added: number
  updated: { label: string; claim_id: string | null }[]
  evidenced: number
}
interface HomeData {
  entities: Entity[]
  docs: DocumentRow[]
  openQuestions: number
  conflicts: number
  review: { entities: number; claims: number; relations: number }
  staleCandidates: number
  examples: string[]
  changes: Changes | null
  /** A 7-day roll-up — null when the fetched window does not actually reach back 7 days. */
  window7: { added: number; updated: number; evidenced: number } | null
}

const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 5) return t('home.greeting.night')
  if (h < 12) return t('home.greeting.morning')
  if (h < 18) return t('home.greeting.afternoon')
  return t('home.greeting.evening')
})

function dayOf(value: unknown) { return String(value || '').slice(0, 10) }

/** 最近知识变化 —— 不是「最近操作」。
 *
 * 用户在意的不是我们跑了什么任务，而是他的知识发生了什么：哪条被取代了、
 * 多了多少、哪些拿到了新证据。这些事实后端全都有（审计轨迹 + 已接受的
 * duplicate 关系 = 同一断言又有了一个来源），只是从来没有人把它们读出来。
 * 这里只做翻译，不做推断：没有数据就不显示那一行。 */
function changesOf(ops: any[], rels: any[]): Changes | null {
  const newest = [...ops, ...rels].map(r => dayOf(r.created_at)).filter(Boolean).sort().pop()
  if (!newest) return null
  const onDay = (r: any) => dayOf(r.created_at) === newest
  const superseding = ops.filter(o => onDay(o) && (
    o.kind === 'SUPERSEDE' || (o.kind === 'CORRECT' && o.result?.superseded_claim_id)))
  return {
    // day 永远存原始日期；「今天」在渲染时判断（否则切换语言后这行不会跟着变）。
    day: newest,
    added: ops.filter(o => onDay(o) && o.kind === 'CREATE').length,
    updated: superseding.slice(0, 3).map(o => ({
      label: o.payload?.subject ? t('home.itemUpdated', { name: o.payload.subject }) : t('home.someUpdated'),
      claim_id: o.result?.claim_id || o.result?.superseded_claim_id || null,
    })),
    evidenced: rels.filter(r => onDay(r) && r.relationship === 'duplicate').length,
  }
}

/** 只有当取回的记录确实覆盖到 7 天前时才给出 7 天数字。否则宁可整行不显示——
 *  一个被截断的"新增 42"比没有更糟，它看起来精确。 */
function window7Of(ops: any[], rels: any[]): HomeData['window7'] {
  const stamps = [...ops, ...rels].map(r => String(r.created_at || '')).filter(Boolean)
  if (!stamps.length) return null
  const since = new Date(Date.now() - 7 * 86400e3).toISOString().slice(0, 19).replace('T', ' ')
  if (stamps.sort()[0] > since) return null
  const within = (r: any) => String(r.created_at || '') >= since
  return {
    added: ops.filter(o => within(o) && o.kind === 'CREATE').length,
    updated: ops.filter(o => within(o) && (o.kind === 'SUPERSEDE'
      || (o.kind === 'CORRECT' && o.result?.superseded_claim_id))).length,
    evidenced: rels.filter(r => within(r) && r.relationship === 'duplicate').length,
  }
}

/** 整页一次读取：一部分读不到和全部读不到，对用户是同一件事——"这一页读不到"。
 *  所以原来那些 `.catch(() => [])` 全部去掉：静默吞掉失败正是"读不到"变成"空"的入口。 */
const home = useAsyncState<HomeData>(async () => {
  // 200 而不是 100：7 天窗口要用同一份数据算，取太少会把"新增"算小。
  const [ops, rels, es, ds, qs, cf, rv, health] = await Promise.all([
    api<any[]>('/api/knowledge/operations?limit=200'),
    api<any[]>('/api/claim-relations?status=accepted&limit=200'),
    api<Entity[]>('/api/entities?limit=6'),
    api<DocumentRow[]>('/api/documents?limit=8'),
    api<any[]>('/api/questions?limit=200'),
    api<any[]>('/api/conflicts'),
    api<any>('/api/review?limit=200'),
    api<any>('/api/knowledge/health').catch(() => null),
  ])
  const open = qs.filter(q => q.status === 'open')
  return {
    entities: es,
    docs: ds,
    openQuestions: open.length,
    conflicts: cf.length,
    review: {
      entities: rv.entities?.length || 0,
      claims: rv.claims?.length || 0,
      relations: rv.relations?.length || 0,
    },
    staleCandidates: health?.stale_candidates || 0,
    // Example questions come from the user's own open questions when they have any.
    examples: open.length ? open.slice(0, 3).map(q => q.content) : defaultExamples(),
    changes: changesOf(ops, rels),
    window7: window7Of(ops, rels),
  }
}, {
  entities: [] as Entity[], docs: [] as DocumentRow[], openQuestions: 0, conflicts: 0,
  review: { entities: 0, claims: 0, relations: 0 }, staleCandidates: 0,
  examples: defaultExamples(), changes: null, window7: null,
})

/** True once the home data has loaded successfully — gates example chips and the
    "needs your judgement" panel so they never appear over a loading/error state. */
const loaded = computed(() => home.status.value === 'success')

const entities = computed(() => home.data.value.entities)
const docs = computed(() => home.data.value.docs)
const openQuestions = computed(() => home.data.value.openQuestions)
const conflicts = computed(() => home.data.value.conflicts)
const review = computed(() => home.data.value.review)
const staleCandidates = computed(() => home.data.value.staleCandidates)
const examples = computed(() => home.data.value.examples)
/** 默认示例在渲染时求值：语言切换后示例文案立即跟着变（数据里只存用户自己的问题）。 */
const exampleChips = computed(() => examples.value.length ? examples.value : defaultExamples())
const todayStr = dayOf(new Date().toISOString())
/** 日期或「今天」标签：today 判断放在渲染层，保证跟随 locale。 */
function dayLabel(day: string) { return day === todayStr ? t('home.today') : day }
const changes = computed(() => home.data.value.changes)
const window7 = computed(() => home.data.value.window7)

const loadAll = home.reload

/** What the system wants from the user, not how much data it holds. */
const pendingTotal = computed(() => review.value.entities + review.value.claims + review.value.relations)
const dueTotal = computed(() => pendingTotal.value + conflicts.value)

/** One list, so the panel can be empty, partial or full without special cases. */
const changeRows = computed(() => {
  const rows: { key: string; icon: Component; cls: string; text: string; to?: any }[] = []
  if (changes.value) {
    for (const [i, u] of changes.value.updated.entries()) {
      rows.push({ key: 'u' + i, icon: RefreshCw, cls: 'upd', text: u.label,
                  to: u.claim_id ? '/knowledge/claim/' + u.claim_id : undefined })
    }
    if (changes.value.added)
      rows.push({ key: 'added', icon: Plus, cls: 'add', text: t('home.addedN', { n: changes.value.added }) })
    if (changes.value.evidenced)
      rows.push({ key: 'ev', icon: CircleCheck, cls: 'ok', text: t('home.evidencedN', { n: changes.value.evidenced }) })
  }
  if (conflicts.value)
    rows.push({ key: 'cf', icon: TriangleAlert, cls: 'wrn', text: t('home.conflictN', { n: conflicts.value }),
                to: { path: '/research', query: { tab: 'conflicts' } } })
  return rows
})

function gotoChange(r: { to?: any }) { if (r.to) router.push(r.to) }

/** Nothing has been given to it yet — so the page is only the way to give it something. */
const hasContent = computed(() =>
  !!(entities.value.length || docs.value.length || openQuestions.value || changeRows.value.length))
/** 首用提示只在"确实读到了、而且确实是空的"时出现——读不到时绝不显示它。 */
const firstUse = computed(() => home.status.value === 'success' && !hasContent.value)

onMounted(loadAll)

/** 首页示例问题走 One Box 的同一个入口：同一句话不该有两种行为。 */
const onebox = ref<{
  ask: (sentence: string) => Promise<void>
  pick: () => void
  importFiles: (files: File[]) => void
} | null>(null)
/** Reading the source stays one click away — but it is not the post-import destination. */
function openDocument(id: string) {
  router.push({ path: '/knowledge', query: { doc: id } })
}
</script>

<template>
  <div class="page">
    <!-- 第一屏只回答一个问题：这东西要我怎么用。OneBox 是唯一主入口，
         文件/网址/文本/自然语言都从它进；不再单独放一个大拖拽框。 -->
    <div class="home-hero">
      <div class="eyebrow">{{ greeting }}</div>
      <h1 class="hero-title">{{ t('home.heroTitle') }}</h1>
      <p class="hero-sub">{{ t('home.heroSub') }}</p>

      <!-- 一个入口，四条路径：问、记、研究、纠正。判断与执行都在组件里，
           用已经存在的接口——首页不再自己决定一句话该去哪里。 -->
      <OneBox ref="onebox" @imported="loadAll" />

      <!-- 示例问题只在真的读到内容时给；读不到时不冒充"你还没有内容" -->
      <div v-if="loaded && hasContent" class="row" style="justify-content:center;gap:8px;margin-top:14px;flex-wrap:wrap">
        <span v-for="e in exampleChips" :key="e" class="tag" style="cursor:pointer" @click="onebox?.ask(e)">{{ e }}</span>
      </div>
      <p v-else-if="firstUse" class="faint" style="font-size:10px;margin-top:14px;line-height:1.8">
        {{ t('home.firstUse') }}
      </p>
    </div>

    <!-- 读不到首页数据时说"读不到"。绝不退回成上面那句新用户提示：
         对用户来说"我的知识全没了"和"这次没读到"是两个完全不同的结论。 -->
    <LoadBoundary :state="home.state.value" :loading-text="t('home.loading')"
                  :error-title="t('home.errorTitle')"
                  :error-text="t('home.errorText')"
                  :reload="loadAll" />

    <template v-if="loaded">
    <!-- 需要你的判断 —— 只在真的有事时出现，这才是首页的职责 -->
    <div v-if="dueTotal" class="panel pad" style="max-width:720px;margin:34px auto 0">
      <div class="row">
        <h3 style="font-size:13px;margin:0">{{ t('home.dueTotal', { n: dueTotal }) }}</h3>
        <div class="grow"></div>
        <button class="btn primary" @click="router.push('/review')">{{ t('home.startReview') }}</button>
      </div>
      <div class="duerows">
        <div class="duerow"><span>{{ t('home.due.entities') }}</span><b>{{ review.entities }}</b></div>
        <div class="duerow"><span>{{ t('home.due.claims') }}</span><b>{{ review.claims }}</b></div>
        <div class="duerow"><span>{{ t('home.due.relations') }}</span><b>{{ review.relations }}</b></div>
        <div class="duerow clickable" :class="{ warn: conflicts > 0 }" @click="router.push({ path: '/research', query: { tab: 'conflicts' } })">
          <span>{{ t('home.due.conflicts') }}</span><b>{{ conflicts }}</b>
        </div>
        <div v-if="staleCandidates" class="duerow"><span>{{ t('home.due.stale') }}</span><b>{{ staleCandidates }}</b></div>
      </div>
    </div>

    <div v-if="hasContent" class="grid g2" style="max-width:920px;margin:30px auto 0">
      <div>
        <div class="sechead"><h3>{{ t('home.recentKnowledge') }}</h3><span class="more" @click="router.push('/knowledge')">{{ t('home.intoKnowledge') }}</span></div>
        <div class="panel pad" style="padding:6px">
          <div v-for="e in entities" :key="e.id" class="item" @click="router.push('/knowledge/object/' + e.id)">
            <div class="ico-badge ib-blue"><Sparkles :size="14" /></div>
            <div class="grow"><b>{{ e.name }}</b><p>{{ entityTypeLabel(e.type) }} · {{ e.description?.slice(0, 40) || '—' }}</p></div>
            <StatusTag :status="e.status" />
          </div>
          <div v-if="!entities.length" class="empty">{{ t('home.noEntities') }}</div>
        </div>
      </div>
      <div>
        <!-- 知识变化，而不是操作日志：这是「它会自己维护」第一次被用户看见的地方 -->
        <div class="sechead">
          <h3>{{ t('home.recentChanges') }}</h3>
          <span v-if="changes" class="faint" style="font-size:9px">{{ dayLabel(changes.day) }}</span>
        </div>
        <div class="panel pad">
          <div v-for="r in changeRows" :key="r.key" class="chgrow" :class="[r.cls, { clickable: !!r.to }]"
               @click="gotoChange(r)">
            <span class="ci"><component :is="r.icon" :size="13" /></span><b>{{ r.text }}</b>
          </div>
          <div v-if="!changeRows.length" class="empty">{{ t('home.noChanges') }}</div>
          <!-- 一眼看出它不是静态数据库，而是在持续工作 -->
          <div v-if="window7" class="w7">
            <span class="wlbl">{{ t('home.last7') }}</span>
            <span>{{ t('home.w7added') }} <b>{{ window7.added }}</b></span>
            <span>{{ t('home.w7updated') }} <b>{{ window7.updated }}</b></span>
            <span>{{ t('home.w7evidenced') }} <b>{{ window7.evidenced }}</b></span>
            <span v-if="dueTotal">{{ t('home.w7due') }} <b>{{ dueTotal }}</b></span>
          </div>
        </div>

        <div class="sechead"><h3>{{ t('home.openQuestions') }}</h3><span class="more" @click="router.push('/research')">{{ t('home.intoResearch') }}</span></div>
        <div class="panel pad" style="padding:6px">
          <div class="item" @click="router.push('/research')">
            <div class="ico-badge ib-blue"><Telescope :size="14" /></div>
            <div class="grow"><b>{{ t('home.openQuestionsN', { n: openQuestions }) }}</b><p>{{ t('home.openQuestionsSub') }}</p></div>
            <span class="tag blue">{{ t('home.openTag') }}</span>
          </div>
        </div>

        <div v-if="docs.length" class="sechead"><h3>{{ t('home.recentDocs') }}</h3><span class="more" @click="router.push('/knowledge')">{{ t('home.allDocs') }}</span></div>
        <div v-if="docs.length" class="panel pad" style="padding:6px">
          <div v-for="d in docs.slice(0, 4)" :key="d.id" class="item" @click="openDocument(d.id)">
            <div class="ico-badge ib-blue"><FileText :size="14" /></div>
            <div class="grow"><b>{{ d.title }}</b><p>{{ t('home.chunksN', { n: d.chunk_count }) }} · {{ fmtDateTime(d.updated_at) }}</p></div>
            <StatusTag v-if="d.last_run_status" :status="d.last_run_status" /><span v-else class="tag amber">{{ t('home.unanalyzed') }}</span>
          </div>
        </div>
      </div>
    </div>
    </template>

    <!-- v3 双卡：问答 / 研究的轻导航，不是新功能，只是两条既有路径的入口 -->
    <div v-if="loaded && hasContent" class="grid g2 minigrid">
      <div class="panel pad mini">
        <h3>{{ t('home.miniQaTitle') }}</h3>
        <p>{{ t('home.miniQaDesc') }}</p>
        <button class="minilink" @click="router.push('/qa')">{{ t('home.miniQaLink') }}</button>
      </div>
      <div class="panel pad mini">
        <h3>{{ t('home.miniResearchTitle') }}</h3>
        <p>{{ t('home.miniResearchDesc') }}</p>
        <button class="minilink" @click="router.push('/research')">{{ t('home.miniResearchLink') }}</button>
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
/* 首页 Hero：克制，不像 Landing Page，只把 OneBox 推到视觉中心 */
.home-hero{max-width:720px;margin:3vh auto 0;text-align:center}
.hero-title{font-size:29px;font-weight:700;letter-spacing:-.03em;margin:10px 0 9px;color:var(--text)}
.hero-sub{font-size:12px;margin:0 0 22px}
/* 主入口下的两个轻动作已并入 OneBox 的 chips，不再单独成行 */
.minigrid{max-width:920px;margin:14px auto 0}
.mini h3{font-size:14px;margin:0 0 7px}
.mini p{font-size:12px;line-height:1.65;color:var(--sub);margin:0}
.minilink{border:0;background:none;color:var(--accent);font-weight:650;font-size:11px;margin-top:14px;padding:0;cursor:pointer}
.minilink:hover{text-decoration:underline}
/* 最近知识变化 */
.chgrow{display:flex;align-items:center;gap:9px;padding:7px 4px;font-size:11.5px;border-bottom:1px solid var(--hair)}
.chgrow:last-child{border-bottom:none}
.chgrow.clickable{cursor:pointer}
.chgrow.clickable:hover{color:var(--accent)}
.ci{width:15px;text-align:center;font-size:12px;flex:none}
.chgrow.upd .ci{color:var(--accent)}
.chgrow.add .ci{color:#3aa76d}
.chgrow.ok .ci{color:#3aa76d}
.chgrow.wrn .ci{color:var(--amber)}
.w7{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;margin-top:12px;padding-top:11px;border-top:1px solid var(--hair);font-size:10px;color:var(--sub)}
.w7 .wlbl{font-size:8.5px;letter-spacing:.08em;font-weight:700;color:var(--faint)}
.w7 b{font-size:12px;color:var(--text)}
</style>
