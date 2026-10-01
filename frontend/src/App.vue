<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  BookOpen, Bot, CircleCheck, Clock, Gauge, GitCompare, House,
  MessagesSquare, Pencil, Settings, Telescope,
  Command as CommandIcon,
} from 'lucide-vue-next'
import { loadPredicateLabels } from './utils/claim'
import { useAppStore } from './stores/app'
import { setLocale, type AppLocale } from './i18n'
import { Button } from '@/components/ui/button'
import AppToast from './components/AppToast.vue'
import CommandPalette from './components/CommandPalette.vue'
import RunProgress from './components/RunProgress.vue'

const route = useRoute()
const router = useRouter()
const store = useAppStore()
const { t, locale } = useI18n()
const cmdOpen = ref(false)

/** 语言切换：显示的是「将切换到」的目标语言，持久化 + 同步 <html lang>。 */
function toggleLocale() {
  const next: AppLocale = locale.value === 'zh-CN' ? 'en' : 'zh-CN'
  setLocale(next)
}
const localeToggleLabel = computed(() => (locale.value === 'zh-CN' ? 'EN' : '中文'))

/* 五个空间，对应五种「我要做什么」：
 *
 *   首页（看状态）· 知识（读与改）· 问答（问）· 研究（查未知）· 设置（配置）
 *
 * 纠正 / 审核 / 评测 / Agent 工作台不在其中，因为它们不是*目的地*：纠正长在答案、
 * 卡片和搜索结果旁边（你是在读到错的东西时才想改它）；审核是一次待办，只在真的
 * 有事时出现；后两者只有维护系统本身时才用得上。一级菜单减少不等于功能消失——
 * 每一条都另有去路，见下方注释。
 */
const items = computed(() => [
  { id: '/', icon: House, text: t('nav.home') },
  { id: '/knowledge', icon: BookOpen, text: t('nav.knowledge') },
  { id: '/qa', icon: MessagesSquare, text: t('nav.qa') },
  { id: '/research', icon: Telescope, text: t('nav.research') },
  { id: '/settings', icon: Settings, text: t('nav.settings') },
])

/** 维护系统本身时才需要的界面，由设置里的 Developer Mode 决定是否出现。
    关掉只是不在侧栏占位置，路由仍然直连可用。 */
const devItems = computed(() => [
  { id: '/agent', icon: Bot, text: t('nav.agent') },
  { id: '/extraction-experiment', icon: GitCompare, text: t('nav.extraction') },
  { id: '/eval', icon: Gauge, text: t('nav.eval') },
  { id: '/review', icon: CircleCheck, text: t('nav.review') },
  { id: '/correction', icon: Pencil, text: t('nav.correction') },
])

function onKey(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); cmdOpen.value = !cmdOpen.value }
  if (e.key === 'Escape') cmdOpen.value = false
}
onMounted(() => {
  window.addEventListener('keydown', onKey)
  store.loadHealth()
  void store.loadPendingReview()
  void loadPredicateLabels()
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

/** 底栏和侧栏说的是同一件事：五个空间（命令面板是它们的兜底入口）。 */
const MOBILE_ITEMS = items
</script>

<template>
  <div class="app">
    <aside class="side">
      <div class="brand">
        <div class="brandmark">W</div>
        <div><b>LLM-Wiki</b><small>Personal Knowledge</small></div>
      </div>
      <div v-for="it in items" :key="it.id" class="nav" :class="{ active: route.path === it.id || (it.id !== '/' && route.path.startsWith(it.id)) }" @click="router.push(it.id)">
        <span class="nico"><component :is="it.icon" :size="15" /></span><span>{{ it.text }}</span>
      </div>

      <!-- 待确认是一个「状态」，不是一个「目的地」：用普通导航样式，不抢戏；
           只有真正重要的冲突才用强调色。点击才进入审核。 -->
      <div v-if="store.pendingReview" class="nav" :class="{ active: route.path === '/review' }"
           :title="t('nav.pendingTitle', { count: store.pendingReview })" @click="router.push('/review')">
        <span class="nico"><Clock :size="15" /></span><span>{{ t('nav.pending') }}</span>
        <span class="pill">{{ store.pendingReview }}</span>
      </div>

      <div class="spacer"></div>
      <template v-if="store.developerMode">
        <div class="navlbl">{{ t('nav.devLabel') }}</div>
        <div v-for="it in devItems" :key="it.id" class="nav"
             :class="{ active: route.path.startsWith(it.id) }" @click="router.push(it.id)">
          <span class="nico"><component :is="it.icon" :size="15" /></span><span>{{ it.text }}</span>
        </div>
      </template>
      <div v-if="store.health" class="healthcard">
        <div class="healthrow"><span class="ok">● {{ t('health.ok') }}</span><span>{{ store.health.version }}</span></div>
        <div class="healthrow"><span>{{ t('health.db') }}</span><span>{{ t('health.local') }}</span></div>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="omni" @click="cmdOpen = true">
          <span>⌕</span><span>{{ t('topbar.searchPlaceholder') }}</span><kbd>⌘K</kbd>
        </div>
        <div class="grow"></div>
        <div class="top-actions">
          <Button variant="ghost" size="sm" class="px-2 text-xs font-semibold" :title="localeToggleLabel === 'EN' ? t('topbar.switchToEn') : t('topbar.switchToZh')" @click="toggleLocale">{{ localeToggleLabel }}</Button>
          <Button variant="ghost" size="icon" :title="t('topbar.palette')" @click="cmdOpen = true"><CommandIcon /></Button>
          <Button variant="ghost" size="icon" :title="t('topbar.settings')" @click="router.push('/settings')"><Settings /></Button>
        </div>
      </header>
      <div class="content">
        <RouterView />
      </div>
    </main>

    <CommandPalette :open="cmdOpen" @close="cmdOpen = false" />
    <nav class="mobilebar">
      <button v-for="it in MOBILE_ITEMS" :key="it.id" :class="{ active: route.path === it.id || (it.id !== '/' && route.path.startsWith(it.id)) }" @click="router.push(it.id)">
        <span><component :is="it.icon" :size="18" /></span><small>{{ it.text }}</small>
      </button>
      <button @click="cmdOpen = true"><span>⌘</span><small>{{ t('nav.command') }}</small></button>
    </nav>
    <RunProgress />
    <AppToast />
  </div>
</template>

<style scoped>
.mobilebar{display:none}
.side{background:rgba(250,249,246,.75);border-right:1px solid rgba(0,0,0,.045);padding:20px 13px 14px;display:flex;flex-direction:column;min-height:0;overflow:auto}
.brand{display:flex;align-items:center;gap:11px;padding:4px 8px 22px}
.brandmark{width:30px;height:30px;border-radius:9px;background:var(--text);color:#fff;display:grid;place-items:center;font-size:12px;font-weight:700}
.brand b{font-size:15.5px;display:block;letter-spacing:-.02em}
.brand small{display:block;font-size:8.5px;color:var(--faint);margin-top:2px;letter-spacing:.06em}
.navlbl{padding:10px 10px 5px;color:var(--faint);font-size:8.5px;letter-spacing:.14em;font-weight:700}
.nav{display:flex;align-items:center;gap:11px;padding:9px 11px;border-radius:11px;margin:2px 0;color:var(--sub);font-size:11.5px;transition:.16s;position:relative;cursor:pointer}
.nav:hover{background:#efede8;color:var(--text)}
.nav.active{background:#ebe8e2;color:var(--text);font-weight:650}
.nico{width:19px;text-align:center;font-size:13px}
.topbar{height:64px;display:flex;align-items:center;gap:12px;padding:0 26px;border-bottom:1px solid rgba(0,0,0,.04);background:transparent}
.top-actions{display:flex;gap:7px}
.iconbtn{border:1px solid var(--line);background:#fff;border-radius:8px;width:34px;height:34px;color:var(--muted);font-size:13px;display:grid;place-items:center;transition:.15s}
.iconbtn:hover{color:var(--text);border-color:#d5cec3}
.content{flex:1;min-height:0;overflow:auto;padding:26px 30px 40px}
/* §10：主内容不铺满，限制阅读宽度，营造工作台留白感 */
.content > *{max-width:1120px;width:100%;margin-inline:auto}
@media(max-width:920px){
  .app{grid-template-columns:64px 1fr}
  .brand b,.brand small,.navlbl,.nav span:not(.nico),.npill,.healthcard{display:none}
  .brand{justify-content:center;padding-left:0;padding-right:0}
  .content{padding:18px 16px 30px}
}
/* Mobile: keep Ask / Knowledge / Research / Agent reachable from a bottom bar. */
@media(max-width:700px){
  .app{grid-template-columns:1fr}
  .side{display:none}
  .content{padding:14px 12px 84px}
  .mobilebar{
    display:flex;position:fixed;left:0;right:0;bottom:0;z-index:50;
    background:rgba(255,255,255,.94);backdrop-filter:blur(14px);
    border-top:1px solid var(--hair);padding:6px 4px calc(6px + env(safe-area-inset-bottom));
  }
  .mobilebar button{
    flex:1;display:flex;flex-direction:column;align-items:center;gap:2px;
    padding:6px 0;color:var(--sub);font-size:15px;border-radius:10px;
  }
  .mobilebar button small{font-size:8.5px}
  .mobilebar button.active{color:var(--blue);font-weight:650;background:var(--blue2)}
}
</style>
