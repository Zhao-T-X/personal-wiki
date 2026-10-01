<script setup lang="ts">
/* ⌘K 命令面板 —— 内部实现已迁移到 shadcn Command（Reka UI）。
 *
 * 对外 API 不变：`open` prop + `close` 事件。
 * 换来的能力：焦点圈定、↑↓/Enter/Esc 键盘导航、无障碍标注、
 * 分组自动隐藏（组内全部被过滤掉时标题一起消失）都不再自己维护。
 * 视觉沿用现有 ico-badge / 暖色主题（shadcn 变量已映射 v3 色板）。 */
import { computed, ref, watch, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  Bot, BookOpen, Box, CircleCheck, Database, FileText, Gauge, HeartPulse,
  History, MessageCircle, Network, Pencil, Search, Settings, SquarePen,
  Telescope, TriangleAlert,
} from 'lucide-vue-next'
import { api } from '../api/client'
import { useAppStore } from '../stores/app'
import { entityTypeLabel } from '../utils/entityType'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const router = useRouter()
const store = useAppStore()
const { t } = useI18n()
const q = ref('')
const entities = ref<{ id: string; name: string; type: string }[]>([])

interface Cmd { group: string; icon: Component; cls: string; title: string; sub?: string; run: () => void }

/** 普通用户只看到「问 / 搜 / 看知识 / 纠正 / 研究 / 审核 / 设置」——不再被迫理解 Agent，
    也不指向开发者才看得懂的 Tab（冲突中心 / 知识健康在普通模式被隐藏）。 */
const coreCommands = computed<Cmd[]>(() => [
  { group: t('palette.groups.ask'), icon: MessageCircle, cls: 'ib-violet', title: t('palette.askKnowledgeBase'), run: () => done({ path: '/qa' }) },
  { group: t('palette.groups.ask'), icon: Search, cls: 'ib-blue', title: t('palette.searchContent'), run: () => done({ path: '/knowledge' }) },
  { group: t('palette.groups.knowledge'), icon: BookOpen, cls: 'ib-blue', title: t('palette.openKnowledge'), run: () => done({ path: '/knowledge' }) },
  { group: t('palette.groups.knowledge'), icon: Network, cls: 'ib-blue', title: t('palette.graph'), run: () => done({ path: '/knowledge', query: { tab: 'graph' } }) },
  { group: t('palette.groups.knowledge'), icon: History, cls: 'ib-blue', title: t('palette.timeline'), run: () => done({ path: '/knowledge', query: { tab: 'timeline' } }) },
  // 纠正不再是一级菜单，所以它更需要在这里：说得出「哪里不对」的人不该先找到页面。
  { group: t('palette.groups.knowledge'), icon: Pencil, cls: 'ib-violet', title: t('palette.correct'), run: () => done({ path: '/correction' }) },
  { group: t('palette.groups.research'), icon: Telescope, cls: 'ib-amber', title: t('palette.researchSpace'), run: () => done({ path: '/research' }) },
  { group: t('palette.groups.research'), icon: CircleCheck, cls: 'ib-mint', title: t('palette.reviewCandidates'), run: () => done({ path: '/review' }) },
  { group: t('palette.groups.system'), icon: Settings, cls: 'ib-blue', title: t('palette.settings'), run: () => done({ path: '/settings' }) },
])

/** 维护系统本身用得到的入口。跟着 Developer Mode 走，但仍直达可用——
    关掉的只是"顺手看得见"，不是"够不着"。 */
const devCommands = computed<Cmd[]>(() => [
  { group: t('palette.groups.ask'), icon: Bot, cls: 'ib-violet', title: t('palette.askAgent'), run: () => done({ path: '/qa', query: { mode: 'agent' } }) },
  { group: t('palette.groups.dev'), icon: Bot, cls: 'ib-violet', title: t('palette.agentWorkbench'), run: () => done({ path: '/agent' }) },
  { group: t('palette.groups.dev'), icon: SquarePen, cls: 'ib-violet', title: t('palette.editPrompt'), run: () => done({ path: '/agent', query: { tab: 'prompt' } }) },
  { group: t('palette.groups.dev'), icon: Gauge, cls: 'ib-violet', title: t('palette.evalPanel'), run: () => done({ path: '/eval' }) },
  { group: t('palette.groups.dev'), icon: Database, cls: 'ib-violet', title: t('palette.database'), run: () => done({ path: '/settings/database' }) },
  { group: t('palette.groups.research'), icon: TriangleAlert, cls: 'ib-amber', title: t('palette.conflictCenter'), run: () => done({ path: '/research', query: { tab: 'conflicts' } }) },
  { group: t('palette.groups.research'), icon: HeartPulse, cls: 'ib-mint', title: t('palette.knowledgeHealth'), run: () => done({ path: '/research', query: { tab: 'health' } }) },
])

const baseCommands = computed<Cmd[]>(() =>
  store.developerMode ? [...coreCommands.value, ...devCommands.value] : coreCommands.value)

const entityCommands = computed<Cmd[]>(() => entities.value.map(e => ({
  group: t('palette.groups.objects'), icon: Box, cls: 'ib-blue',
  title: t('palette.openObject', { name: e.name, type: entityTypeLabel(e.type) }),
  run: () => done({ path: '/knowledge/object/' + e.id }),
})))

/** 分组保持 baseCommands 的声明顺序（提问与搜索 → 知识 → 研究 → 系统 → 开发者）。 */
const commandGroups = computed(() => {
  const order: { name: string; items: Cmd[] }[] = []
  const byName = new Map<string, Cmd[]>()
  for (const c of baseCommands.value) {
    let g = byName.get(c.group)
    if (!g) { g = []; byName.set(c.group, g); order.push({ name: c.group, items: g }) }
    g.push(c)
  }
  return order
})

/** 空查询时对象只露前 5 个（原行为）；有查询词时全部交给 Command 过滤。 */
const shownEntities = computed(() =>
  q.value.trim() ? entityCommands.value : entityCommands.value.slice(0, 5))

/* Knowledge hits: ⌘K must search the wiki itself, not just command titles. */
const hits = ref<any[]>([])
let searchTimer: number | undefined
/** Monotonic guard so a slow response can never overwrite a newer query's hits. */
let searchSeq = 0

watch(q, () => {
  window.clearTimeout(searchTimer)
  const s = q.value.trim()
  if (s.length < 2) { hits.value = []; return }
  const seq = ++searchSeq
  searchTimer = window.setTimeout(async () => {
    try {
      const r = await api<any[]>(`/api/search?q=${encodeURIComponent(s)}&limit=6&semantic=true`)
      if (seq === searchSeq) hits.value = r
    } catch { if (seq === searchSeq) hits.value = [] }
  }, 220)
})

const hitCommands = computed<Cmd[]>(() => hits.value.map(h => ({
  group: '知识命中', icon: FileText, cls: 'ib-blue',
  title: h.title || '未命名文档',
  sub: (h.content || '').slice(0, 90),
  run: () => done({ path: '/knowledge', query: { doc: h.document_id, chunk: h.id } }),
})))

watch(() => props.open, async (v) => {
  if (!v) return
  q.value = ''; hits.value = []
  try {
    const list = await api<any[]>('/api/entities?limit=30')
    entities.value = list.map(e => ({ id: e.id, name: e.name, type: e.type }))
  } catch { entities.value = [] }
})

/** Command 用 value 做过滤，把标题和摘要都放进匹配域。 */
function itemValue(c: Cmd) { return `${c.title} ${c.sub ?? ''}` }

function done(loc: any) { emit('close'); router.push(loc) }
function ask(text: string) { emit('close'); store.searchTerm = text; router.push({ path: '/qa', query: { q: text } }) }
function search(text: string) { emit('close'); router.push({ path: '/knowledge', query: { q: text } }) }
</script>

<template>
  <Dialog :open="open" @update:open="(v: boolean) => { if (!v) emit('close') }">
    <DialogContent class="overflow-hidden p-0 sm:max-w-[600px]" :show-close-button="false">
      <DialogHeader class="sr-only">
        <DialogTitle>{{ t('palette.dialogTitle') }}</DialogTitle>
        <DialogDescription>{{ t('palette.dialogDesc') }}</DialogDescription>
      </DialogHeader>
      <Command>
        <CommandInput v-model="q" :placeholder="t('palette.placeholder')" />
        <CommandList class="max-h-[380px]">
          <CommandGroup v-if="hitCommands.length" :heading="t('palette.groups.hits')">
            <CommandItem v-for="(c, i) in hitCommands" :key="'hit' + i" :value="itemValue(c)" @select="c.run()">
              <span class="ico-badge ib-blue flex-none"><component :is="c.icon" :size="14" /></span>
              <span class="min-w-0">
                <span class="block truncate">{{ c.title }}</span>
                <span v-if="c.sub" class="block truncate text-xs text-muted-foreground">{{ c.sub }}</span>
              </span>
            </CommandItem>
          </CommandGroup>

          <CommandGroup v-for="g in commandGroups" :key="g.name" :heading="g.name">
            <CommandItem v-for="c in g.items" :key="c.title" :value="itemValue(c)" @select="c.run()">
              <span class="ico-badge flex-none" :class="c.cls"><component :is="c.icon" :size="14" /></span>
              <span class="truncate">{{ c.title }}</span>
            </CommandItem>
          </CommandGroup>

          <CommandGroup v-if="shownEntities.length" :heading="t('palette.groups.objects')">
            <CommandItem v-for="c in shownEntities" :key="c.title" :value="itemValue(c)" @select="c.run()">
              <span class="ico-badge ib-blue flex-none"><component :is="c.icon" :size="14" /></span>
              <span class="truncate">{{ c.title }}</span>
            </CommandItem>
          </CommandGroup>

          <CommandGroup v-if="q.trim()" :heading="t('palette.groups.useThis')">
            <CommandItem :value="`${t('palette.askWith', { q: q.trim() })} ${q.trim()}`" @select="ask(q.trim())">
              <span class="ico-badge ib-violet flex-none"><MessageCircle :size="14" /></span>
              <span class="truncate">{{ t('palette.askWith', { q: q.trim() }) }}</span>
            </CommandItem>
            <CommandItem :value="`${t('palette.searchWith', { q: q.trim() })} ${q.trim()}`" @select="search(q.trim())">
              <span class="ico-badge ib-blue flex-none"><Search :size="14" /></span>
              <span class="truncate">{{ t('palette.searchWith', { q: q.trim() }) }}</span>
            </CommandItem>
          </CommandGroup>

          <CommandEmpty>{{ t('palette.noMatch') }}</CommandEmpty>
        </CommandList>
        <div class="border-t px-4 py-2.5 text-xs text-muted-foreground">{{ t('palette.footerHint') }}</div>
      </Command>
    </DialogContent>
  </Dialog>
</template>
