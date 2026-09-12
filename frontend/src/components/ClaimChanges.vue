<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, patchJson, post } from '../api/client'
import EmptyState from './EmptyState.vue'
import { useAppStore } from '../stores/app'

/**
 * Knowledge changes: how newly extracted claims relate to what the wiki already
 * believed. Nothing here overwrites a claim — the user is confirming a
 * relationship, not editing a record.
 */
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
  old_id: string; old_subject: string; old_predicate: string
  old_object: string | null; old_object_text: string | null
  old_quote: string | null; old_document_id: string | null; old_document_title: string | null
}

const changes = ref<ClaimChange[]>([])
const loading = ref(false)
const busy = ref('')

async function load() {
  loading.value = true
  try { changes.value = await api<ClaimChange[]>('/api/claim-relations?limit=100') }
  catch (e: any) { store.toast(e.message) }
  finally { loading.value = false }
}
onMounted(load)

const pending = computed(() => changes.value.filter(c => c.status === 'candidate'))

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
      `把这 ${ids.length} 条都标记为「新知识取代旧知识」？\n\n` +
      '每一条都会让对应的旧断言不再代表当前状态（内容与证据仍然保留）。可以撤销。')) return
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
  const label = status === 'rejected' ? '已标记并存' : (relationship === 'supersedes' ? '已记录取代' : '已确认')
  store.toast(`${label} ${ok}/${ids.length} 条`, { label: '撤销', run: () => batchReset(ids) })
}

async function batchReset(ids: string[]) {
  let ok = 0
  for (const id of ids) {
    try { await patchJson(`/api/claim-relations/${id}`, { status: 'candidate' }); ok++ }
    catch { /* keep going */ }
  }
  await load()
  store.toast(`已撤销 ${ok}/${ids.length} 条`)
}

const RELATION: Record<string, { label: string; tone: string }> = {
  duplicate: { label: '重复', tone: 'green' },
  coexists: { label: '并存', tone: '' },
  supersedes: { label: '取代', tone: 'blue' },
  contradicts: { label: '矛盾', tone: 'amber' },
  unclear: { label: '待定', tone: '' },
}
const rel = (r: string) => RELATION[r] || { label: r, tone: '' }

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
    const message = status === 'rejected'
      ? '已标记：两者都保留'
      : (relationship === 'supersedes' ? '已记录：新知识取代旧知识' : '已确认')
    store.toast(message, { label: '撤销', run: () => reset(c) })
    await load()
  } catch (e: any) { store.toast(e.message) } finally { busy.value = '' }
}

async function reset(c: ClaimChange) {
  try {
    await patchJson(`/api/claim-relations/${c.id}`, { status: 'candidate' })
    await load()
    store.toast('已撤销，回到待确认')
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
      <h3>知识变化 <span class="tag amber" style="margin-left:4px">{{ pending.length }}</span></h3>
      <span class="faint" style="font-size:9px">新内容与已有知识的关系——系统只提出判断，由你决定</span>
    </div>

    <div v-if="pending.length" class="row batchbar">
      <label class="chk">
        <input type="checkbox" :checked="allSelected" @change="toggleAll" />
        <span>全选</span>
      </label>
      <span class="muted" style="font-size:9.5px">
        <template v-if="selectedCount">已选 {{ selectedCount }} 条</template>
        <template v-else>勾选后可批量处理</template>
      </span>
      <div class="grow"></div>
      <button class="btn primary" :disabled="!selectedCount || busy === 'batch'"
              @click="batchResolve('accepted', 'supersedes')">确认取代</button>
      <button class="btn" :disabled="!selectedCount || busy === 'batch'"
              @click="batchResolve('rejected')">标记为并存</button>
      <button v-if="selectedCount" class="btn ghost" :disabled="busy === 'batch'" @click="clearSelection">清除选择</button>
    </div>

    <div v-for="c in pending" :key="c.id" class="panel pad changecard" :class="{ picked: isSelected(c.id) }">
      <div class="row" style="gap:8px;flex-wrap:wrap">
        <input type="checkbox" :checked="isSelected(c.id)" @change="toggleSelect(c.id)"
               style="flex:none;cursor:pointer" />
        <span class="tag" :class="rel(c.relationship).tone">{{ rel(c.relationship).label }}</span>
        <b style="font-size:11.5px">{{ c.new_subject }} · {{ c.new_predicate }}</b>
        <div class="grow"></div>
        <span v-if="c.confidence != null" class="faint" style="font-size:9px">置信度 {{ Math.round(c.confidence * 100) }}%</span>
      </div>

      <div class="grid g2" style="margin-top:12px">
        <div class="changeside old">
          <div class="sidelabel">已有知识</div>
          <b>{{ obj(c.old_object, c.old_object_text) }}</b>
          <div class="sidefoot">
            <span>{{ c.old_document_title || '来源文档' }}</span>
            <button v-if="c.old_document_id" class="btn sm" @click="openSource(c.old_document_id)">查看原文</button>
          </div>
          <p v-if="c.old_quote" class="sidequote">“{{ c.old_quote }}”</p>
        </div>
        <div class="changeside new">
          <div class="sidelabel">新信息</div>
          <b>{{ obj(c.new_object, c.new_object_text) }}</b>
          <div class="sidefoot">
            <span>{{ c.new_document_title || '来源文档' }}</span>
            <button v-if="c.new_document_id" class="btn sm" @click="openSource(c.new_document_id)">查看原文</button>
          </div>
          <p v-if="c.new_quote" class="sidequote">“{{ c.new_quote }}”</p>
        </div>
      </div>

      <div v-if="c.reason" class="notice violet" style="margin-top:12px">
        <b>为什么会有这条判断？</b>{{ c.reason }}
      </div>

      <div class="row" style="margin-top:12px;gap:8px;flex-wrap:wrap">
        <button class="btn primary" :disabled="busy === c.id" @click="resolve(c, 'accepted', 'supersedes')">
          新知识取代旧知识
        </button>
        <button class="btn" :disabled="busy === c.id" @click="resolve(c, 'rejected')">两者都保留</button>
        <button class="btn ghost" :disabled="analyzing === c.id" @click="analyze(c)">
          {{ analyzing === c.id ? 'AI 判断中…' : '让 AI 判断' }}
        </button>
      </div>

      <div v-if="aiVerdicts[c.id]" class="notice violet" style="margin-top:10px">
        <b>AI 判断：{{ rel(aiVerdicts[c.id].relationship).label }}</b>
        <span class="faint" style="margin-left:6px">置信度 {{ Math.round(aiVerdicts[c.id].confidence * 100) }}%</span>
        <div style="margin-top:6px">{{ aiVerdicts[c.id].reason }}</div>
        <button class="btn sm" style="margin-top:9px" @click="applyVerdict(c, aiVerdicts[c.id])">采纳这个判断</button>
      </div>
    </div>

    <EmptyState
      v-if="!pending.length && !loading"
      title="没有需要确认的知识变化"
      text="新文档进入时会自动与已有断言比对；出现重复、矛盾或可能的取代时，会在这里请你判断。"
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
