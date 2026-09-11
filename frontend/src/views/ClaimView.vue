<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
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

async function load() {
  loadError.value = ''
  try {
    c.value = await api<Claim>('/api/claims/' + route.params.id)
  } catch (e: any) { c.value = null; loadError.value = e.message }
}
onMounted(load)
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

    <div class="panel pad">
      <Triple :subject="c.subject_name || '—'" :predicate="c.predicate" :object="(c.object_name || c.object_text || '—')" />
      <hr class="hairline" />
      <div class="propgrid">
        <div class="prop"><span>Claim Type</span><b>{{ c.claim_type }}</b></div>
        <div class="prop"><span>Polarity</span><b>{{ c.polarity }}</b></div>
        <div class="prop"><span>Modality</span><b>{{ c.modality }}</b></div>
        <div class="prop"><span>Condition</span><b>{{ c.content || c.context?.condition || '—' }}</b></div>
      </div>

      <div class="sechead"><h3>Evidence</h3><span class="tag" :class="c.source_quote ? 'green' : 'red'" style="margin:0">{{ c.source_quote ? '1 source' : 'missing' }}</span></div>
      <div v-if="c.source_quote" class="evidence">“{{ c.source_quote }}”
        <div class="src">{{ c.source_document_id?.slice(0, 8) }} · chunk {{ c.source_chunk_id?.slice(0, 8) }} · offset [{{ c.source_start_offset }}, {{ c.source_end_offset }})</div>
      </div>
      <div v-else class="empty">该 Claim 缺少 Evidence Quote</div>

      <div class="sechead"><h3>Normalization</h3><span class="tag" :class="inGraph() ? 'green' : 'violet'" style="margin:0">{{ inGraph() ? 'May enter graph' : 'Claim Only' }}</span></div>
      <div class="notice violet">
        <template v-if="inGraph()">这条 Claim 的确定性为 <b>asserted</b>，满足进入关系图谱的条件。</template>
        <template v-else>这条 Claim <b>没有进入关系图谱</b>。原因：modality = <b>{{ c.modality }}</b>。Normalize 规则要求因果类 Claim 具备 asserted 以上的确定性，才会派生为 Relation。</template>
      </div>

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
