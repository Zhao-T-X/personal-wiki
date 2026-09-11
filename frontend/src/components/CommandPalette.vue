<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import { useAppStore } from '../stores/app'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const router = useRouter()
const store = useAppStore()
const q = ref('')
const hot = ref(0)
const entities = ref<{ id: string; name: string; type: string }[]>([])
const inputEl = ref<HTMLInputElement | null>(null)

interface Cmd { group: string; icon: string; cls: string; title: string; run: () => void }

const baseCommands: Cmd[] = [
  { group: '提问与搜索', icon: '◎', cls: 'ib-violet', title: '提问知识库（知识库问答）', run: () => done({ path: '/qa' }) },
  { group: '提问与搜索', icon: '◌', cls: 'ib-violet', title: '提问 Agent（ReAct Agent）', run: () => done({ path: '/qa', query: { mode: 'agent' } }) },
  { group: '提问与搜索', icon: '⌕', cls: 'ib-blue', title: '搜索知识（正文检索）', run: () => done({ path: '/knowledge' }) },
  { group: '知识', icon: '✦', cls: 'ib-blue', title: '打开知识库', run: () => done({ path: '/knowledge' }) },
  { group: '知识', icon: '⌘', cls: 'ib-blue', title: '关系图谱', run: () => done({ path: '/knowledge', query: { tab: 'graph' } }) },
  { group: '知识', icon: '◈', cls: 'ib-blue', title: '时间线', run: () => done({ path: '/knowledge', query: { tab: 'timeline' } }) },
  { group: '研究', icon: '◇', cls: 'ib-amber', title: '研究空间', run: () => done({ path: '/research' }) },
  { group: '研究', icon: '✓', cls: 'ib-mint', title: '审核候选知识', run: () => done({ path: '/research', query: { tab: 'review' } }) },
  { group: '研究', icon: '⚠', cls: 'ib-amber', title: '冲突中心', run: () => done({ path: '/research', query: { tab: 'conflicts' } }) },
  { group: '研究', icon: '♥', cls: 'ib-mint', title: '知识健康', run: () => done({ path: '/research', query: { tab: 'health' } }) },
  { group: 'Agent', icon: '◌', cls: 'ib-violet', title: 'Agent 工作台', run: () => done({ path: '/agent' }) },
  { group: 'Agent', icon: '✎', cls: 'ib-violet', title: '编辑 Prompt', run: () => done({ path: '/agent', query: { tab: 'prompt' } }) },
  { group: '系统', icon: '⚙', cls: 'ib-blue', title: '设置', run: () => done({ path: '/settings' }) },
  { group: '系统', icon: '◉', cls: 'ib-violet', title: 'Database', run: () => done({ path: '/settings/database' }) },
]

const entityCommands = computed<Cmd[]>(() => entities.value.map(e => ({
  group: '实体', icon: '✦', cls: 'ib-blue', title: `打开实体：${e.name}（${e.type}）`,
  run: () => done({ path: '/knowledge/object/' + e.id }),
})))

const filtered = computed(() => {
  const s = q.value.trim().toLowerCase()
  const all = [...baseCommands, ...entityCommands.value]
  if (!s) return [...baseCommands, ...entityCommands.value.slice(0, 5)]
  return all.filter(c => c.title.toLowerCase().includes(s))
})

watch(() => props.open, async (v) => {
  if (!v) return
  q.value = ''; hot.value = 0
  await nextTick(); inputEl.value?.focus()
  try {
    const list = await api<any[]>('/api/entities?limit=30')
    entities.value = list.map(e => ({ id: e.id, name: e.name, type: e.type }))
  } catch { entities.value = [] }
})
watch(q, () => { hot.value = 0 })

function move(delta: number) {
  if (!filtered.value.length) return
  hot.value = (hot.value + delta + filtered.value.length) % filtered.value.length
}
function activate() {
  const c = filtered.value[hot.value]
  if (c) c.run()
  else if (q.value.trim()) ask(q.value.trim())
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') { e.preventDefault(); move(1) }
  else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1) }
  else if (e.key === 'Enter') { e.preventDefault(); activate() }
  else if (e.key === 'Escape') emit('close')
}
function done(loc: any) { emit('close'); router.push(loc) }
function ask(text: string) { emit('close'); store.searchTerm = text; router.push({ path: '/qa', query: { q: text } }) }
function search(text: string) { emit('close'); router.push({ path: '/knowledge', query: { q: text } }) }
</script>

<template>
  <div v-if="open" class="cmdk" @click.self="emit('close')">
    <div class="cmdbox">
      <input ref="inputEl" v-model="q" placeholder="输入命令、实体名，或直接输入问题…" @keydown="onKey" />
      <div class="cmdlist">
        <template v-for="(c, i) in filtered" :key="c.title + i">
          <div v-if="i === 0 || filtered[i - 1].group !== c.group" class="cmdgroup">{{ c.group }}</div>
          <div class="cmditem" :class="{ hot: i === hot }" @mouseenter="hot = i" @click="c.run()">
            <div class="ico-badge" :class="c.cls">{{ c.icon }}</div>
            <span class="grow">{{ c.title }}</span>
          </div>
        </template>
        <template v-if="q.trim()">
          <div class="cmdgroup">用这个问题</div>
          <div class="cmditem" @click="ask(q.trim())"><div class="ico-badge ib-violet">◎</div><span class="grow">提问：「{{ q.trim() }}」（知识库问答）</span></div>
          <div class="cmditem" @click="search(q.trim())"><div class="ico-badge ib-blue">⌕</div><span class="grow">搜索：「{{ q.trim() }}」</span></div>
        </template>
        <div v-if="!filtered.length && !q.trim()" class="empty" style="margin:10px">没有匹配的命令</div>
      </div>
      <div class="cmdgroup" style="padding:6px 14px 10px;color:var(--faint)">↑↓ 选择 · Enter 执行 · Esc 关闭</div>
    </div>
  </div>
</template>

<style scoped>
.cmditem.hot{background:var(--tint-blue)}
</style>
