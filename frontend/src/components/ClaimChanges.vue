<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { api, patchJson, post } from '../api/client'
import EmptyState from './EmptyState.vue'
import { useAppStore } from '../stores/app'

/**
 * Knowledge changes: how newly extracted claims relate to what the wiki already
 * believed. Nothing here overwrites a claim — the user is confirming a
 * relationship, not editing a record.
 */
const { t } = useI18n()
const router = useRouter()
const store = useAppStore()

interface ClaimChange {
  id: string
  relationship: string
  confidence: number | null
  reason: string | null
  suggested_action: string | null
  status: string
  created_at: string
  new_id: string; new_subject: string; new_predicate: string
  new_object: string | null; new_object_text: string | null
  new_quote: string | null; new_document_id: string | null; new_document_title: string | null
  /* Lifecycle of both sides: whether this pair is still a live question depends on
     them, not on this relation's own status (see `pending` below). */
  new_status: string | null
  old_id: string; old_subject: string; old_predicate: string
  old_object: string | null; old_object_text: string | null
  old_status: string | null
  old_quote: string | null; old_document_id: string | null; old_document_title: string | null
}

const changes = ref<ClaimChange[]>([])

/* 不传 limit：窗口大小由后端的默认值决定，Review Inbox 数的是同一个窗口。
   前端再写一个数字，就又多了一处会和服务端漂移的地方。 */
const loading = ref(false)
const busy = ref('')

async function load() {
  loading.value = true
  try { changes.value = await api<ClaimChange[]>('/api/claim-relations') }
  catch (e: any) { store.toast(e.message) }
  finally { loading.value = false }
}
onMounted(load)

/** 真正还在等你决定的那些——判定规则与后端 `pending_decisions` 保持一致。
 *
 *  只按 `status === 'candidate'` 过滤，会让已经有答案的关系继续挂在列表里：对方那条
 *  已经被取代，这个分歧其实解决了。侧栏角标用的是后端那套规则；两边不一致时，用户先
 *  怀疑角标，再怀疑整个列表——而这份名单的全部价值就在于它可信。 */
const pending = computed(() => changes.value.filter(c =>
  c.status === 'candidate'
  && c.new_status !== 'rejected' && c.old_status !== 'rejected'
  && c.old_status !== 'superseded'))

/* ---------- 批量处理 ----------
   确认「取代」会逐条改变知识库当前的说法，所以它必须显式确认且可撤销；
   「标记为并存」不改变任何断言，风险低。 */
const selected = ref<Set<string>>(new Set())
const selectedCount = computed(() => selected.value.size)
const allSelected = computed(() => pending.value.length > 0 && selected.value.size === pending.value.length)

function isSelected(id: string) { return selected.value.has(id) }
function toggleSelect(id: string) {
  const next = new Set(selected.value)
  next.has(id) ? next.delete(id) : next.add(id)
  selected.value = next
}
function toggleAll() {
  selected.value = allSelected.value ? new Set() : new Set(pending.value.map(c => c.id))
}
function clearSelection() { selected.value = new Set() }

async function batchResolve(status: 'accepted' | 'rejected', relationship?: string) {
  const ids = [...selected.value]
  if (!ids.length) return
  if (relationship === 'supersedes' && !confirm(
      t('change.confirmSupersedeMsg', { n: ids.length }))) return
  busy.value = 'batch'
  let ok = 0
  for (const id of ids) {
    try {
      await patchJson(`/api/claim-relations/${id}`, relationship ? { status, relationship } : { status })
      ok++
    } catch { /* keep going — report partial success */ }
  }
  busy.value = ''
  selected.value = new Set()
  await load()
  const verb = status === 'rejected' ? t('change.toastCoexist') : (relationship === 'supersedes' ? t('change.toastSuperseded') : t('change.toastAccepted'))
  store.toast(t('change.toastBatch', { verb, ok, total: ids.length }), { label: t('common.undo'), run: () => batchReset(ids) })
}

async function batchReset(ids: string[]) {
  let ok = 0
  for (const id of ids) {
    try { await patchJson(`/api/claim-relations/${id}`, { status: 'candidate' }); ok++ }
    catch { /* keep going */ }
  }
  await load()
  store.toast(t('change.toastResetPart', { ok, total: ids.length }))
}

/** 关系标签走 i18n，色调保持原样（change.rel.*）。 */
const REL_TONE: Record<string, string> = {
  duplicate: 'green', coexists: '', supersedes: 'blue', contradicts: 'amber', unclear: '',
}
const rel = (r: string) => ({ label: t('change.rel.' + r) || r, tone: REL_TONE[r] || '' })

const obj = (name: string | null, text: string | null) => name || text || '—'

function openSource(documentId: string | null) {
  if (!documentId) return
  router.push({ path: '/knowledge', query: { doc: documentId } })
}

/**
 * Accepting with `supersedes` is the only path that lets an older claim stop
 * being current. Everything else either links evidence or dismisses the
 * suggestion, and both are undone by resetting the relation to `candidate`.
 */
async function resolve(c: ClaimChange, status: 'accepted' | 'rejected', relationship?: string) {
  busy.value = c.id
  try {
    await patchJson(`/api/claim-relations/${c.id}`,
                    relationship ? { status, relationship } : { status })
    const message = status === 'rejected' ? t('change.toastCoexist')
      : (relationship === 'supersedes' ? t('change.toastSuperseded') : t('change.toastAccepted'))
    store.toast(message, { label: t('common.undo'), run: () => reset(c) })
    await load()
  } catch (e: any) { store.toast(e.message) } finally { busy.value = '' }
}

async function reset(c: ClaimChange) {
  try {
    await patchJson(`/api/claim-relations/${c.id}`, { status: 'candidate' })
    await load()
    store.toast(t('change.toastReverted'))
  } catch (e: any) { store.toast(e.message) }
}

/* Semantic judgement is strictly on demand: importing a document must never
   spend tokens on it. The deterministic rules already cover structure. */
const analyzing = ref('')
const aiVerdicts = ref<Record<string, { relationship: string; confidence: number; reason: string }>>({})

async function analyze(c: ClaimChange) {
  analyzing.value = c.id
  try {
    const verdict = await post<{ relationship: string; confidence: number; reason: string }>(
      `/api/claim-relations/${c.id}/analyze`)
    aiVerdicts.value = { ...aiVerdicts.value, [c.id]: verdict }
  } catch (e: any) { store.toast(e.message) } finally { analyzing.value = '' }
}

async function applyVerdict(c: ClaimChange, verdict: { relationship: string }) {
  await resolve(c, 'accepted', verdict.relationship)
  const next = { ...aiVerdicts.value }
  delete next[c.id]
  aiVerdicts.value = next
}
</script>

<template>
  <div v-if="pending.length || loading" style="margin-bottom:22px">
    <div class="sechead">
      <h3>{{ t('change.title') }} <span class="tag amber" style="margin-left:4px">{{ pending.length }}</span></h3>
      <span class="faint" style="font-size:9px">{{ t('change.titleMeta') }}</span>
    </div>

    <div v-if="pending.length" class="row batchbar">
      <label class="chk">
        <input type="checkbox" :checked="allSelected" @change="toggleAll" />
        <span>{{ t('change.selectAll') }}</span>
      </label>
      <span class="muted" style="font-size:9.5px">
        <template v-if="selectedCount">{{ t('change.selected', { n: selectedCount }) }}</template>
        <template v-else>{{ t('change.selectedHint') }}</template>
      </span>
      <div class="grow"></div>
      <button class="btn primary" :disabled="!selectedCount || busy === 'batch'"
              @click="batchResolve('accepted', 'supersedes')">{{ t('change.confirmSupersede') }}</button>
      <button class="btn" :disabled="!selectedCount || busy === 'batch'"
              @click="batchResolve('rejected')">{{ t('change.markCoexist') }}</button>
      <button v-if="selectedCount" class="btn ghost" :disabled="busy === 'batch'" @click="clearSelection">{{ t('change.clearSelection') }}</button>
    </div>

    <p v-if="changes.length >= 200" class="muted" style="font-size:9px;margin:0 0 12px">
      {{ t('change.moreHint') }}
    </p>

    <div v-for="c in pending" :key="c.id" class="panel pad changecard" :class="{ picked: isSelected(c.id) }">
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <input type="checkbox" :checked="isSelected(c.id)" @change="toggleSelect(c.id)"
               style="flex:none;cursor:pointer" />
        <span class="tag" :class="rel(c.relationship).tone">{{ rel(c.relationship).label }}</span>
        <b style="font-size:11.5px">{{ c.new_subject }} · {{ c.new_predicate }}</b>
        <div class="grow"></div>
        <span v-if="c.confidence != null" class="faint" style="font-size:9px">{{ t('change.confidence', { n: Math.round(c.confidence * 100) }) }}</span>
      </div>

      <div class="grid g2" style="margin-top:12px">
        <div class="changeside old">
          <div class="sidelabel">{{ t('change.sideOld') }}</div>
          <b>{{ obj(c.old_object, c.old_object_text) }}</b>
          <div class="sidefoot">
            <span>{{ c.old_document_title || t('change.sourceDoc') }}</span>
            <button v-if="c.old_document_id" class="btn sm" @click="openSource(c.old_document_id)">{{ t('change.viewOriginal') }}</button>
          </div>
          <p v-if="c.old_quote" class="sidequote">“{{ c.old_quote }}”</p>
        </div>
        <div class="changeside new">
          <div class="sidelabel">{{ t('change.sideNew') }}</div>
          <b>{{ obj(c.new_object, c.new_object_text) }}</b>
          <div class="sidefoot">
            <span>{{ c.new_document_title || t('change.sourceDoc') }}</span>
            <button v-if="c.new_document_id" class="btn sm" @click="openSource(c.new_document_id)">{{ t('change.viewOriginal') }}</button>
          </div>
          <p v-if="c.new_quote" class="sidequote">“{{ c.new_quote }}”</p>
        </div>
      </div>

      <div v-if="c.reason" class="notice violet" style="margin-top:12px">
        <b>{{ t('change.reasonHead') }}</b>{{ c.reason }}
      </div>

      <div class="row" style="margin-top:12px;gap:8px;flex-wrap:wrap">
        <button class="btn primary" :disabled="busy === c.id" @click="resolve(c, 'accepted', 'supersedes')">
          {{ t('change.supersedeBtn') }}
        </button>
        <button class="btn" :disabled="busy === c.id" @click="resolve(c, 'rejected')">{{ t('change.keepBothBtn') }}</button>
        <button class="btn ghost" :disabled="analyzing === c.id" @click="analyze(c)">
          {{ analyzing === c.id ? t('change.aiJudging') : t('change.aiJudgeBtn') }}
        </button>
      </div>

      <div v-if="aiVerdicts[c.id]" class="notice violet" style="margin-top:10px">
        <b>{{ t('change.aiVerdict', { label: rel(aiVerdicts[c.id].relationship).label }) }}</b>
        <span class="faint" style="margin-left:6px">{{ t('change.confidence', { n: Math.round(aiVerdicts[c.id].confidence * 100) }) }}</span>
        <div style="margin-top:6px">{{ aiVerdicts[c.id].reason }}</div>
        <button class="btn sm" style="margin-top:9px" @click="applyVerdict(c, aiVerdicts[c.id])">{{ t('change.adoptVerdict') }}</button>
      </div>
    </div>

    <EmptyState
      v-if="!pending.length && !loading"
      :title="t('change.emptyTitle')"
      :text="t('change.emptyText')"
    />
  </div>
</template>

<style scoped>
.changeside{padding:12px 14px;border-radius:12px;background:var(--surface2)}
.changeside.old{border-left:3px solid #c9d3e6}
.changeside.new{border-left:3px solid #bcd0ff;background:var(--tint-blue)}
.sidelabel{font-size:8px;letter-spacing:.1em;color:var(--faint);font-weight:700;margin-bottom:6px}
.sidefoot{display:flex;align-items:center;gap:9px;margin-top:9px;font-size:9px;color:var(--sub);flex-wrap:wrap}
.sidequote{margin:9px 0 0;font-size:9.5px;line-height:1.65;color:#5a6b85}
/* 批量处理工具条 */
.batchbar{margin-bottom:12px;gap:9px;flex-wrap:wrap}
.chk{display:flex;align-items:center;gap:6px;font-size:9.5px;color:var(--sub);cursor:pointer}
.changecard{margin-bottom:14px;transition:.15s}
.changecard.picked{box-shadow:0 0 0 2px rgba(91,124,255,.28)}
</style>
