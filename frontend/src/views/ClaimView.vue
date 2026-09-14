<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, patchJson } from '../api/client'
import type { Claim } from '../api/types'
import Triple from '../components/Triple.vue'
import StatusTag from '../components/StatusTag.vue'
import PageHead from '../components/PageHead.vue'
import { useAppStore } from '../stores/app'

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

const RELATION_LABEL: Record<string, string> = {
  duplicate: '重复', coexists: '并存', supersedes: '取代', contradicts: '矛盾', unclear: '待定',
}

async function load() {
  loadError.value = ''
  docTitle.value = ''
  relations.value = []
  quality.value = null
  try {
    c.value = await api<Claim>('/api/claims/' + route.params.id)
    if (c.value?.source_document_id) {
      try {
        const d = await api<{ title: string }>('/api/documents/' + c.value.source_document_id)
        docTitle.value = d.title
      } catch { docTitle.value = '' }
    }
    try {
      const r = await api<{ relations: any[] }>('/api/claims/' + c.value.id + '/relations')
      relations.value = r.relations || []
    } catch { relations.value = [] }
    try {
      quality.value = await api<any>('/api/knowledge/claims/' + c.value.id + '/quality')
    } catch { quality.value = null }
  } catch (e: any) { c.value = null; loadError.value = e.message }
}

/** A claim stops being current only after a human accepted a supersedes relation. */
const supersededBy = computed(() => relations.value.find(
  r => r.relationship === 'supersedes' && r.status === 'accepted' && r.target_claim_id === c.value?.id))
onMounted(load)

/** Evidence must lead back to the raw source, with the originating chunk highlighted. */
function openSource() {
  if (!c.value?.source_document_id) return
  router.push({
    path: '/knowledge',
    query: { doc: c.value.source_document_id, ...(c.value.source_chunk_id ? { chunk: c.value.source_chunk_id } : {}) },
  })
}
/* Same route record + new :id → Vue reuses this component, so re-fetch on param change. */
watch(() => route.params.id, load)

async function setStatus(status: string) {
  if (!c.value) return
  try {
    await patchJson(`/api/knowledge/claim/${c.value.id}/status`, { status })
    store.toast(status === 'verified' ? '已提交审核：verified' : '已标记 ' + status)
    c.value = { ...c.value, status }
  } catch (e: any) { store.toast(e.message) }
}
const inGraph = () => !!c.value && c.value.modality === 'asserted'
</script>

<template>
  <div class="page" v-if="c">
    <PageHead title="Claim 详情">
      <template #actions>
        <button class="btn ghost" @click="router.back()">← 返回</button>
        <button class="btn primary" @click="router.push('/research')">就此创建研究</button>
      </template>
    </PageHead>

    <div v-if="supersededBy" class="notice violet" style="margin-bottom:14px">
      <b>这条断言已被新知识取代。</b>更新的来源给出了不同结论，系统保留了这条历史记录与它的原始证据。
      <span style="cursor:pointer;text-decoration:underline;margin-left:6px"
            @click="router.push('/knowledge/claim/' + supersededBy.source_claim_id)">查看取代它的断言 →</span>
    </div>

    <div class="panel pad">
      <Triple :subject="c.subject_name || '—'" :predicate="c.predicate" :object="(c.object_name || c.object_text || '—')" />
      <hr class="hairline" />
      <div class="propgrid">
        <div class="prop"><span>Claim Type</span><b>{{ c.claim_type }}</b></div>
        <div class="prop"><span>Polarity</span><b>{{ c.polarity }}</b></div>
        <div class="prop"><span>Modality</span><b>{{ c.modality }}</b></div>
        <div class="prop"><span>Condition</span><b>{{ c.content || c.context?.condition || '—' }}</b></div>
      </div>

      <div v-if="quality" class="sechead"><h3>质量评分</h3><span class="tag" :class="'g-' + quality.grade" style="margin:0">{{ quality.grade }} · {{ Math.round(quality.overall * 100) }}%</span></div>
      <div v-if="quality" class="qbox">
        <div v-for="(v, k) in quality.dimensions" :key="k" class="qbar">
          <span class="qk">{{ k }}</span>
          <span class="qt"><i :style="{ width: Math.round(v * 100) + '%' }" :class="{ low: v < 0.5 }"></i></span>
          <span class="qv">{{ Math.round(v * 100) }}</span>
        </div>
        <div v-if="quality.flags?.length" class="qflags">
          <span v-for="f in quality.flags" :key="f" class="qflag">{{ f }}</span>
        </div>
      </div>

      <div class="sechead"><h3>Evidence</h3><span class="tag" :class="c.source_quote ? 'green' : 'red'" style="margin:0">{{ c.source_quote ? '1 source' : 'missing' }}</span></div>
      <div v-if="c.source_quote" class="evidence">“{{ c.source_quote }}”
        <div class="src">
          <span>{{ docTitle || '来源文档' }}</span>
          <span v-if="c.source_start_offset != null">offset [{{ c.source_start_offset }}, {{ c.source_end_offset }})</span>
        </div>
        <button class="btn sm" style="margin-top:9px" @click="openSource">打开原文并定位 →</button>
      </div>
      <div v-else class="empty">该 Claim 缺少 Evidence Quote——可能来自没有可定位引用的抽取批次。</div>

      <div class="sechead"><h3>Normalization</h3><span class="tag" :class="inGraph() ? 'green' : 'violet'" style="margin:0">{{ inGraph() ? 'May enter graph' : 'Claim Only' }}</span></div>
      <div class="notice violet">
        <template v-if="inGraph()">这条 Claim 的确定性为 <b>asserted</b>，满足进入关系图谱的条件。</template>
        <template v-else>这条 Claim <b>没有进入关系图谱</b>。原因：modality = <b>{{ c.modality }}</b>。Normalize 规则要求因果类 Claim 具备 asserted 以上的确定性，才会派生为 Relation。</template>
      </div>

      <template v-if="relations.length">
        <div class="sechead">
          <h3>知识变化</h3>
          <span class="tag" style="margin:0">{{ relations.length }} 条</span>
        </div>
        <div class="panel" style="padding:6px">
          <div v-for="r in relations" :key="r.id" class="item"
               @click="router.push('/knowledge/claim/' + r.other_id)">
            <span class="tag"
                  :class="r.relationship === 'contradicts' ? 'amber'
                          : r.relationship === 'duplicate' ? 'green'
                          : r.relationship === 'supersedes' ? 'blue' : ''">
              {{ RELATION_LABEL[r.relationship] || r.relationship }}
            </span>
            <div class="grow">
              <b>{{ r.subject_name }} → {{ r.predicate }} → {{ r.object_name || r.object_text || '—' }}</b>
              <p>{{ r.document_title || '来源文档' }} · 置信度 {{ r.confidence != null ? Math.round(r.confidence * 100) + '%' : '—' }}</p>
            </div>
            <StatusTag :status="r.status" />
          </div>
        </div>
      </template>

      <div class="row" style="margin-top:14px;gap:8px">
        <button class="btn" @click="setStatus('verified')">✓ Verify</button>
        <button class="btn" @click="setStatus('rejected')">✕ Reject</button>
        <div class="grow"></div>
        <span class="faint" style="font-size:9px">状态：<StatusTag :status="c.status" /></span>
      </div>
    </div>
  </div>

  <div class="page" v-else-if="loadError">
    <PageHead title="Claim 不存在" :subtitle="loadError">
      <template #actions>
        <button class="btn" @click="router.back()">← 返回</button>
      </template>
    </PageHead>
    <div class="empty">找不到这条 Claim（{{ loadError }}）——它可能已被删除或合并。</div>
  </div>
</template>

<style scoped>
.qbox{margin:10px 0 4px;padding:11px;border:1px solid var(--hair);border-radius:11px;background:#fff}
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
