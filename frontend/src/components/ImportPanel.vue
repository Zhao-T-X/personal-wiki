<script setup lang="ts">
/* 导入管道：把文件变成知识，并让用户看见「处理到哪一步、最后发现了什么」。
 *
 * 这是产品的第一印象，所以它不是一句 toast，而是一条有阶段的流水线：
 *   import → parse → chunk → analyze
 * 串行处理——并发抽取会争抢 SQLite 写锁，也会同时打满模型配额。
 *
 * 抽成组件的原因不是复用方便，而是：首页和知识页都必须能「把东西交给我」。
 * 同一件事有两套实现，就会有两种行为（首页曾经要点一下才能抽取）。
 * 一个入口，一份实现。
 */
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, post, postForm } from '../api/client'
import { hasLabel, labelOf } from '../utils/claim'
import { useAppStore } from '../stores/app'

withDefaults(defineProps<{
  /** 首页用大号 hero 版；知识页用常规版。 */
  hero?: boolean
  title?: string
  hint?: string
  /** 队列处理完后是否显示「去审核」。 */
  showReview?: boolean
}>(), {
  hero: false,
  title: '把文件拖到这里导入',
  hint: '支持 Markdown / TXT / HTML，可一次拖入多个文件',
  showReview: true,
})

const emit = defineEmits<{
  (e: 'imported', documentId: string): void
  (e: 'open-document', documentId: string): void
}>()

const router = useRouter()
const store = useAppStore()

type StageState = 'pending' | 'active' | 'done' | 'failed'
interface ImportStage { key: string; label: string; state: StageState }
interface ImportItem {
  title: string
  documentId: string
  stages: ImportStage[]
  counts: Record<string, number> | null
  chunks: number | null
  error: string
  /** What the document contributed, read back from the knowledge base itself. */
  knowledge: any | null
}

const importQueue = ref<ImportItem[]>([])
const importing = ref(false)
const dragOver = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

function newStages(): ImportStage[] {
  return [
    { key: 'import', label: '读取文件', state: 'pending' },
    { key: 'parse', label: '解析内容', state: 'pending' },
    { key: 'chunk', label: '切分片段', state: 'pending' },
    { key: 'analyze', label: '抽取知识', state: 'pending' },
  ]
}

function setStage(item: ImportItem, key: string, state: StageState) {
  const stage = item.stages.find(s => s.key === key)
  if (stage) stage.state = state
}

const IMPORT_COUNTS: { key: string; label: string }[] = [
  { key: 'entities', label: '实体' },
  { key: 'claims', label: '断言' },
  { key: 'relations', label: '关系' },
  { key: 'events', label: '事件' },
  { key: 'ideas', label: '想法' },
  { key: 'questions', label: '问题' },
]
/** Only non-zero counts: a row of zeroes is noise, not a result. */
function foundCounts(item: ImportItem) {
  const c = item.counts
  if (!c) return []
  return IMPORT_COUNTS.map(m => ({ ...m, value: c[m.key] || 0 })).filter(m => m.value > 0)
}
function pendingOf(item: ImportItem) {
  const c = item.counts
  if (!c) return 0
  return (c.entities || 0) + (c.claims || 0) + (c.relations || 0)
}
const totalPending = computed(() => importQueue.value.reduce((n, i) => n + pendingOf(i), 0))
const finishedCount = computed(() => importQueue.value.filter(i => i.counts || i.error).length)

/** Read back what was actually stored. The panel never assembles a second
    version of the result — if this and the knowledge base disagree, the UI is
    wrong, and going through the API is what makes that impossible. */
async function loadKnowledge(item: ImportItem) {
  if (!item.documentId) return
  try { item.knowledge = await api<any>(`/api/documents/${item.documentId}/knowledge`) }
  catch { item.knowledge = null }
}

/** A few triples, in the user's words wherever the registry has words for them. */
function triplesOf(item: ImportItem) {
  return (item.knowledge?.claims || []).slice(0, 4)
}

/** Suggested questions are *derived*, never invented.
    A registry label plus a subject the document really mentioned, or just an
    entity it named. Nothing here asserts a fact the document did not produce,
    and nothing here is a guess dressed up as intelligence.

    `hasLabel` and not `labelOf`: a predicate the registry does not name would
    otherwise produce "「LLM-Wiki」的requires是什么？", which is worse than saying
    nothing — this is exactly the sentence the label pass exists to prevent. */
function questionsFor(item: ImportItem): string[] {
  const k = item.knowledge
  if (!k) return []
  const out: string[] = []
  for (const c of k.claims || []) {
    if (out.length >= 2) break
    if (hasLabel(c.predicate) && c.subject) out.push(`「${c.subject}」的${labelOf(c.predicate)}是什么？`)
  }
  for (const n of k.names || []) {
    if (out.length >= 3) break
    if (!out.some(q => q.includes(n))) out.push(`「${n}」是什么？`)
  }
  if (!out.length) out.push('这篇讲了什么？')
  return out.slice(0, 3)
}

function askAbout(q: string) { router.push({ path: '/qa', query: { q } }) }
function openClaim(id: string) { if (id) router.push('/knowledge/claim/' + id) }

/** One file, end to end. Kept separate so the queue can drive them serially. */
async function processFile(item: ImportItem, file: File) {
  try {
    const fd = new FormData(); fd.append('file', file)
    const doc = await postForm<{ id: string; title: string }>('/api/documents/import', fd)
    item.documentId = doc.id
    item.title = doc.title || file.name
    setStage(item, 'import', 'done')
    setStage(item, 'parse', 'done')

    // Chunking is local and instant, so it always runs.
    setStage(item, 'chunk', 'active')
    const local = await post<{ chunks: number }>(`/api/documents/${doc.id}/index/local`)
    item.chunks = local.chunks
    setStage(item, 'chunk', 'done')
  } catch (e: any) {
    item.error = e.message
    const active = item.stages.find(s => s.state === 'active')
    if (active) active.state = 'failed'
    return
  }

  if (!store.health) await store.loadHealth()
  if (!store.health?.llm_configured) {
    item.error = '未配置语言模型，已完成分块。配置模型后可运行「用模型抽取」补上知识。'
    return
  }

  // Extraction is the slow step; RunProgress (global) shows the live batch counter.
  setStage(item, 'analyze', 'active')
  store.beginExtraction(item.documentId, item.title)
  try {
    const r = await post<{ counts: Record<string, number> }>(`/api/documents/${item.documentId}/index`)
    store.clearExtraction()
    item.counts = r.counts
    setStage(item, 'analyze', 'done')
    await loadKnowledge(item)
    emit('imported', item.documentId)
  } catch (e: any) {
    store.clearExtraction()
    setStage(item, 'analyze', 'failed')
    item.error = e.message
  }
}

/** Serial by design: parallel extraction fights over the SQLite write lock. */
async function importFiles(files: File[]) {
  if (!files.length || importing.value) return
  const items: ImportItem[] = files.map(f => ({
    title: f.name, documentId: '', stages: newStages(), counts: null, chunks: null, error: '',
    knowledge: null,
  }))
  importQueue.value = items
  importing.value = true
  try {
    for (const [i, file] of files.entries()) {
      setStage(items[i], 'import', 'active')
      await processFile(items[i], file)
    }
  } finally {
    importing.value = false
  }
}

function onFilePick(ev: Event) {
  const input = ev.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''   // allow picking the same file twice
  void importFiles(files)
}
function onDrop(ev: DragEvent) {
  dragOver.value = false
  void importFiles(Array.from(ev.dataTransfer?.files || []))
}
function pick() { fileInput.value?.click() }
function closeQueue() { importQueue.value = [] }

defineExpose({ pick, importFiles, closeQueue })
</script>

<template>
  <div class="importpanel">
    <!-- 拖拽导入：一次可以拖入多个文件 -->
    <div v-if="!importQueue.length" class="dropzone" :class="{ over: dragOver, hero }"
         @dragover.prevent="dragOver = true"
         @dragleave.prevent="dragOver = false"
         @drop.prevent="onDrop"
         @click="pick">
      <div class="dzicon">⤓</div>
      <div class="dztitle" :class="{ hero }">{{ title }}</div>
      <div class="dzhint">{{ hint }}<br v-if="!hero" />导入后会自动分块并抽取知识，抽完再让你确认</div>
    </div>

    <!-- 导入队列：每个文件一行，展示处理到哪一步、发现了什么 -->
    <div v-if="importQueue.length" class="panel pad">
      <div class="row">
        <b style="font-size:12px">
          <template v-if="importing">正在处理 {{ Math.min(finishedCount + 1, importQueue.length) }} / {{ importQueue.length }}</template>
          <template v-else>已处理 {{ importQueue.length }} 个文件</template>
        </b>
        <div class="grow"></div>
        <button v-if="showReview && totalPending" class="btn primary sm" @click="router.push('/review')">
          去确认这 {{ totalPending }} 项 →
        </button>
        <button class="btn sm ghost" :disabled="importing" @click="closeQueue">关闭</button>
      </div>

      <div v-for="(item, i) in importQueue" :key="i" class="impitem">
        <div class="ico-badge" :class="item.error ? 'ib-amber' : item.counts ? 'ib-mint' : 'ib-blue'">▤</div>
        <div class="grow">
          <b>{{ item.title }}</b>
          <div class="ifsteps">
            <div v-for="s in item.stages" :key="s.key" class="ifstep" :class="s.state">
              <span class="ifdot">{{ s.state === 'done' ? '✓' : s.state === 'failed' ? '×' : s.state === 'active' ? '◌' : '·' }}</span>
              <span>{{ s.label }}</span>
              <span v-if="s.key === 'chunk' && item.chunks" class="tag">{{ item.chunks }} 片段</span>
            </div>
          </div>
          <p v-if="item.error" class="muted" style="font-size:9px;margin:9px 0 0">{{ item.error }}</p>

          <!-- 所以它读出了什么。数字不是结果，内容才是：
               它一眼要让用户明白「这个产品不是帮我存文件，是帮我理解文件」。 -->
          <div v-else-if="item.knowledge" class="learned">
            <div class="lhead">我从这篇整理出了什么</div>

            <div v-if="item.knowledge.names?.length" class="lnames">
              <span v-for="n in item.knowledge.names.slice(0, 8)" :key="n" class="tag blue">{{ n }}</span>
            </div>

            <div v-if="triplesOf(item).length" class="ltriples">
              <div v-for="c in triplesOf(item)" :key="c.id" class="ltriple"
                   :title="c.quote || ''" @click="openClaim(c.id)">
                <b>{{ c.subject }}</b>
                <span class="lp">{{ labelOf(c.predicate) }}</span>
                <b>{{ c.object || '—' }}</b>
              </div>
            </div>

            <p v-if="item.knowledge.counts?.pending" class="lpending">
              {{ foundCounts(item).map(c => `${c.value} ${c.label}`).join(' · ') }}
              —— 其中 {{ item.knowledge.counts.pending }} 条需要你确认才会成为正式知识
            </p>
            <p v-else-if="foundCounts(item).length" class="lpending">
              {{ foundCounts(item).map(c => `${c.value} ${c.label}`).join(' · ') }}
            </p>

            <div class="lask">
              <span class="faint" style="font-size:9.5px">关于这篇，你可以问：</span>
              <button v-for="q in questionsFor(item)" :key="q" class="qchip" @click="askAbout(q)">{{ q }}</button>
            </div>

            <div class="row" style="margin-top:11px">
              <button class="btn sm" @click="emit('open-document', item.documentId)">查看这篇的原文与断言 →</button>
            </div>
          </div>

          <template v-else>
            <div v-if="foundCounts(item).length" class="ifcounts" style="margin-top:11px">
              <div v-for="c in foundCounts(item)" :key="c.key" class="ifcount">
                <b>{{ c.value }}</b><span>{{ c.label }}</span>
              </div>
            </div>
            <div v-if="item.documentId && item.counts" style="margin-top:10px">
              <button class="btn sm" @click="emit('open-document', item.documentId)">查看原文</button>
            </div>
          </template>
        </div>
      </div>
    </div>

    <input ref="fileInput" type="file" multiple accept=".md,.markdown,.txt,.html,.htm"
           style="display:none" @change="onFilePick" />
  </div>
</template>

<style scoped>
.dropzone{
  border:2px dashed var(--line);border-radius:16px;padding:26px 20px;text-align:center;
  color:var(--sub);font-size:10px;cursor:pointer;transition:.16s;
  background:rgba(255,255,255,.5);
}
.dropzone:hover{border-color:#c6d3ee;background:#fafcff}
.dropzone.over{border-color:#8fa8f0;background:var(--tint-blue)}
.dropzone.hero{padding:34px 24px;border-radius:20px}
.dzicon{font-size:20px;color:#8b9dc4;line-height:1}
.dztitle{font-size:12.5px;font-weight:650;margin-top:9px}
.dztitle.hero{font-size:16px;letter-spacing:-.01em}
.dzhint{margin-top:7px;line-height:1.75;color:var(--faint)}
.importpanel{margin-bottom:16px}
.impitem{display:flex;gap:11px;padding:12px 4px;border-bottom:1px solid var(--hair)}
.impitem:last-child{border-bottom:none}
.ifsteps{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
.ifstep{display:flex;align-items:center;gap:6px;padding:6px 11px;border-radius:10px;background:var(--surface2);font-size:9.5px;color:var(--sub)}
.ifstep.done{color:#1e8f6b;background:var(--tint-mint)}
.ifstep.active{color:#4a63e8;background:var(--tint-blue)}
.ifstep.failed{color:#c8565f;background:#fff0f1}
.ifdot{font-size:10px;line-height:1}
.ifcounts{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:10px}
.ifcount{padding:11px 13px;background:var(--surface2);border-radius:11px}
.ifcount b{display:block;font-size:20px;letter-spacing:-.03em}
.ifcount span{font-size:9px;color:var(--sub)}
/* 「我从这篇整理出了什么」 */
.learned{margin-top:11px;padding:12px 13px;border:1px solid var(--hair);border-radius:12px;background:#fcfdff}
.lhead{font-size:11.5px;font-weight:650;letter-spacing:-.01em}
.lnames{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.ltriples{margin-top:11px;display:grid;gap:2px}
.ltriple{display:flex;align-items:baseline;gap:8px;padding:6px 8px;border-radius:9px;font-size:11.5px;cursor:pointer;transition:.14s;overflow-wrap:anywhere}
.ltriple:hover{background:var(--tint-blue)}
.ltriple .lp{flex:none;font-size:9.5px;color:#4a63e8;background:var(--tint-blue);border-radius:7px;padding:2px 7px}
.lpending{margin:11px 0 0;font-size:9.5px;color:var(--faint);line-height:1.7}
.lask{display:flex;flex-wrap:wrap;align-items:center;gap:7px;margin-top:11px}
.qchip{font-size:10.5px;color:var(--text);background:#fff;border:1px solid var(--hair);border-radius:99px;padding:5px 11px;cursor:pointer;transition:.14s}
.qchip:hover{border-color:#c6d3ee;background:#fafcff;color:#4a63e8}
</style>
