<script setup lang="ts">
/* 知识详情 = 这件事的现在，以及它怎么变成现在这样。
 *
 * 两件刻意的事：
 *
 * * **默认只回答「我现在知道什么、凭什么」。** Claim Type / Polarity / Modality /
 *   offset / Normalization / 质量分对判断这条知识可不可信没有帮助，出现在第一屏
 *   只会把人推走，所以它们收进「技术细节」。
 * * **历史不是可选项。** 不展示演化，用户就无法理解这个产品是在*维护*知识而不是
 *   保存文件——而这条链是真实存储的，不是回放出来的（ADR-005：取代只改生命周期，
 *   从不删除）。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, patchJson } from '../api/client'
import type { Claim } from '../api/types'
import KnowledgeCard from '../components/KnowledgeCard.vue'
import StatusTag from '../components/StatusTag.vue'
import PageHead from '../components/PageHead.vue'
import { useAppStore } from '../stores/app'
import { toCard } from '../utils/claim'

const route = useRoute()
const router = useRouter()
const store = useAppStore()
const c = ref<Claim | null>(null)
const loadError = ref('')
const docTitle = ref('')
/** How this claim relates to other claims — claims are never overwritten. */
const relations = ref<any[]>([])
/** Deterministic quality score (no LLM) — see KnowledgeQualityScore. */
const quality = ref<any>(null)
/** The stored evolution chain, oldest first (GET /api/claims/{id}/history). */
const history = ref<any>(null)
const showHistory = ref(false)

const RELATION_LABEL: Record<string, string> = {
  duplicate: '重复', coexists: '并存', supersedes: '取代', contradicts: '矛盾', unclear: '待定',
}

const card = computed(() => (c.value ? toCard(c.value) : null))
/** 依据数 comes from the chain node: its own quote plus accepted duplicates. */
const sources = computed(() =>
  history.value?.chain?.find((n: any) => n.id === c.value?.id)?.sources ?? null)
/** Supersede edges are what 历史 shows; the rest are still worth listing. */
const otherRelations = computed(() => relations.value.filter(r => r.relationship !== 'supersedes'))

async function load() {
  loadError.value = ''
  docTitle.value = ''
  relations.value = []
  quality.value = null
  history.value = null
  try {
    const claim = await api<Claim>('/api/claims/' + route.params.id)
    c.value = claim
    const [rel, q, hist] = await Promise.all([
      api<{ relations: any[] }>('/api/claims/' + claim.id + '/relations').catch(() => ({ relations: [] })),
      api<any>('/api/knowledge/claims/' + claim.id + '/quality').catch(() => null),
      api<any>('/api/claims/' + claim.id + '/history').catch(() => null),
    ])
    relations.value = rel.relations || []
    quality.value = q
    history.value = hist
    if (claim.source_document_id) {
      try {
        docTitle.value = (await api<{ title: string }>('/api/documents/' + claim.source_document_id)).title
      } catch { docTitle.value = '' }
    }
  } catch (e: any) { c.value = null; loadError.value = e.message }
}

/** Same route record + new :id → Vue reuses this component, so re-fetch on param change. */
watch(() => route.params.id, load)
/** The card's 历史 button anywhere in the app arrives as ?history=1. */
watch(() => route.query.history, v => { if (v) void revealHistory() }, { immediate: true })
onMounted(load)

async function revealHistory() {
  showHistory.value = true
  await nextTick()
  document.getElementById('claim-history')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

/** Evidence must lead back to the raw source, with the originating chunk highlighted. */
function openSource() {
  if (!c.value?.source_document_id) return
  router.push({
    path: '/knowledge',
    query: { doc: c.value.source_document_id, ...(c.value.source_chunk_id ? { chunk: c.value.source_chunk_id } : {}) },
  })
}
function openNode(id: string) {
  if (id !== c.value?.id) router.push('/knowledge/claim/' + id)
}

async function setStatus(status: string) {
  if (!c.value) return
  try {
    await patchJson(`/api/knowledge/claim/${c.value.id}/status`, { status })
    store.toast(status === 'verified' ? '已提交审核：verified' : '已标记 ' + status)
    c.value = { ...c.value, status }
  } catch (e: any) { store.toast(e.message) }
}
const inGraph = () => !!c.value && c.value.modality === 'asserted'
const day = (v: string | null | undefined) => (v || '').slice(0, 10) || '—'
</script>

<template>
  <div class="page" v-if="c && card">
    <PageHead title="知识详情">
      <template #actions>
        <button class="btn ghost" @click="router.back()">← 返回</button>
        <button class="btn" @click="router.push('/research')">就此创建研究</button>
      </template>
    </PageHead>

    <!-- 事实 / 状态 / 依据 / 操作 —— 与其他位置同一张卡 -->
    <KnowledgeCard :claim="card" :evidence-count="sources" />

    <!-- 历史：这条知识怎么变成现在这样 -->
    <div class="sechead" style="margin-top:18px">
      <h3>历史</h3>
      <span v-if="history?.superseded_count" class="tag" style="margin:0">{{ history.superseded_count }} 次变化</span>
      <div class="grow"></div>
      <button class="btn sm" @click="showHistory ? showHistory = false : revealHistory()">
        {{ showHistory ? '收起' : '查看历史' }}
      </button>
    </div>
    <div v-if="showHistory" id="claim-history" class="panel pad">
      <div v-if="history?.cycles" class="notice red" style="margin-bottom:10px">
        这条知识的历史数据存在环（互相取代），显示可能不完整。
      </div>
      <div class="evochain">
        <div v-for="(n, i) in (history?.chain || [])" :key="n.id" class="evonode"
             :class="{ current: n.is_current }" @click="openNode(n.id)">
          <span class="evodot"></span>
          <div class="evobody">
            <div class="row">
              <b class="evovalue">{{ n.object || '—' }}</b>
              <StatusTag :status="n.status" />
              <span v-if="n.is_current" class="evocur">当前</span>
            </div>
            <p class="evometa">
              <template v-if="n.is_current">
                生效中 · 依据 {{ n.sources }} 个来源
              </template>
              <template v-else>
                {{ day(n.effective_from) }} → {{ day(n.effective_to) }}
                · 依据 {{ n.sources }} 个来源
                · 被「{{ history?.chain?.[Number(i) + 1]?.object || '后续陈述' }}」取代
              </template>
            </p>
          </div>
        </div>
        <div v-if="!history?.chain?.length" class="empty">读不到历史记录。</div>
      </div>
      <p class="faint" style="font-size:9.5px;margin:10px 0 0">
        被取代的陈述不会被删除：它保留自己的依据与生效期间，只是不再是当前结论。
      </p>
    </div>

    <!-- 依据：真实引文 + 可定位的原文 -->
    <div class="sechead" style="margin-top:18px">
      <h3>依据</h3>
      <span class="tag" :class="c.source_quote ? 'green' : 'red'" style="margin:0">
        {{ sources != null ? sources : (c.source_quote ? 1 : 0) }} 个来源
      </span>
    </div>
    <div class="panel pad">
      <div v-if="c.source_quote" class="evidence">“{{ c.source_quote }}”
        <div class="src"><span>{{ docTitle || '来源文档' }}</span></div>
        <button class="btn sm" style="margin-top:9px" @click="openSource">打开原文并定位 →</button>
      </div>
      <div v-else class="empty">这条知识缺少可定位的引文——可能来自没有引用信息的抽取批次。</div>
      <p v-if="(sources ?? 0) > 1" class="faint" style="font-size:9.5px;margin:10px 0 0">
        其中 {{ sources - 1 }} 个来自另一个来源对同一事实的重复陈述——它让这条知识更有说服力。
      </p>
    </div>

    <!-- 与其他断言的关系（取代已在「历史」中呈现） -->
    <template v-if="otherRelations.length">
      <div class="sechead">
        <h3>相关断言</h3>
        <span class="tag" style="margin:0">{{ otherRelations.length }} 条</span>
      </div>
      <div class="panel pad" style="padding:6px">
        <div v-for="r in otherRelations" :key="r.id" class="item"
             @click="router.push('/knowledge/claim/' + r.other_id)">
          <span class="tag"
                :class="r.relationship === 'contradicts' ? 'amber'
                        : r.relationship === 'duplicate' ? 'green' : ''">
            {{ RELATION_LABEL[r.relationship] || r.relationship }}
          </span>
          <div class="grow">
            <b>{{ r.subject_name }} · {{ r.object_name || r.object_text || '—' }}</b>
            <p>{{ r.document_title || '来源文档' }} · 置信度 {{ r.confidence != null ? Math.round(r.confidence * 100) + '%' : '—' }}</p>
          </div>
          <StatusTag :status="r.status" />
        </div>
      </div>
    </template>

    <div class="row" style="margin-top:14px;gap:8px">
      <button class="btn" @click="setStatus('verified')">✓ 确认这条</button>
      <button class="btn" @click="setStatus('rejected')">✕ 标记不成立</button>
      <div class="grow"></div>
      <span class="faint" style="font-size:9px">状态：<StatusTag :status="c.status" /></span>
    </div>

    <!-- 技术细节：判断可信度时用不到，所以不占第一屏 -->
    <details class="tech">
      <summary>技术细节</summary>
      <div class="propgrid">
        <div class="prop"><span>Claim Type</span><b>{{ c.claim_type }}</b></div>
        <div class="prop"><span>Polarity</span><b>{{ c.polarity }}</b></div>
        <div class="prop"><span>Modality</span><b>{{ c.modality }}</b></div>
        <div class="prop"><span>Confidence</span><b>{{ c.confidence ?? '—' }}</b></div>
        <div class="prop"><span>Ontology</span><b>{{ c.ontology_version || '—' }}</b></div>
        <div class="prop"><span>进入图谱</span><b>{{ inGraph() ? '是' : '否' }}</b></div>
        <div class="prop"><span>Context</span><b>{{ c.context?.condition || Object.keys(c.context || {}).join(', ') || '—' }}</b></div>
      </div>
      <div v-if="quality" class="qbox">
        <div class="qhead">
          <span>质量评分</span>
          <b class="grade" :class="'g-' + quality.grade">{{ quality.grade }}</b>
          <span class="faint">{{ Math.round(quality.overall * 100) }}%</span>
        </div>
        <div v-for="(v, k) in quality.dimensions" :key="k" class="qbar">
          <span class="qk">{{ k }}</span>
          <span class="qt"><i :style="{ width: Math.round(v * 100) + '%' }" :class="{ low: v < 0.5 }"></i></span>
          <span class="qv">{{ Math.round(v * 100) }}</span>
        </div>
        <div v-if="quality.flags?.length" class="qflags">
          <span v-for="f in quality.flags" :key="f" class="qflag">{{ f }}</span>
        </div>
      </div>
      <p class="faint" style="font-size:9.5px;margin:10px 0 0">
        <template v-if="inGraph()">确定性为 asserted，满足进入关系图谱的条件。</template>
        <template v-else>没有进入关系图谱：modality = {{ c.modality }}，Normalize 规则要求因果类断言具备 asserted 以上确定性才会派生为 Relation。</template>
      </p>
    </details>
  </div>

  <div class="page" v-else-if="loadError">
    <PageHead title="知识不存在" :subtitle="loadError">
      <template #actions>
        <button class="btn" @click="router.back()">← 返回</button>
      </template>
    </PageHead>
    <div class="empty">找不到这条知识（{{ loadError }}）——它可能已被删除或合并。</div>
  </div>
</template>

<style scoped>
/* 历史链：一条竖线串起这件事的每一次变化 */
.evochain{display:grid;gap:2px}
.evonode{display:flex;gap:12px;padding:9px 6px;border-radius:11px;cursor:pointer;transition:.16s;position:relative}
.evonode:hover{background:var(--surface2)}
.evonode.current{background:var(--tint-blue)}
.evodot{
  flex:none;width:9px;height:9px;border-radius:99px;background:#c8d3e6;margin-top:5px;
  position:relative;z-index:1;
}
.evonode.current .evodot{background:#4a63e8;box-shadow:0 0 0 3px rgba(91,124,255,.16)}
/* Connector stops at the last node, so the chain reads as a path, not a list. */
.evonode + .evonode:before{
  content:"";position:absolute;left:10px;top:-9px;height:18px;width:1.5px;background:var(--line);
}
.evobody{min-width:0}
.evovalue{font-size:13.5px;letter-spacing:-.01em}
.evocur{font-size:8.5px;font-weight:700;color:#4a63e8;letter-spacing:.06em}
.evometa{margin:3px 0 0;font-size:10px;color:var(--faint)}
.tech{margin-top:18px;border-top:1px solid var(--hair);padding-top:12px}
.tech summary{cursor:pointer;font-size:10px;color:var(--sub);font-weight:600}
.qbox{margin:10px 0 4px;padding:11px;border:1px solid var(--hair);border-radius:11px;background:#fff}
.qhead{display:flex;align-items:center;gap:9px;font-size:11px;color:var(--sub)}
.grade{font-size:13px;width:22px;height:22px;border-radius:7px;display:grid;place-items:center;color:#fff}
.qbar{display:grid;grid-template-columns:96px 1fr 28px;align-items:center;gap:9px;font-size:10px;color:var(--sub);margin:4px 0}
.qk{font-size:9.5px;text-transform:capitalize}
.qt{height:7px;background:#eef1f7;border-radius:99px;overflow:hidden}
.qt i{display:block;height:100%;background:linear-gradient(90deg,#5b7cff,#8b67f7)}
.qt i.low{background:#d9695a}
.qv{text-align:right;color:var(--faint)}
.qflags{margin-top:9px;display:flex;flex-wrap:wrap;gap:5px}
.qflag{font-size:9px;background:#fbeceb;color:#c0564b;border-radius:99px;padding:2px 8px}
.g-A{background:#3aa76d;color:#fff}.g-B{background:#5b9bd5;color:#fff}
.g-C{background:#e0a93b;color:#fff}.g-D{background:#d9695a;color:#fff}
</style>
