<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { loadPredicateLabels } from './utils/claim'
import { useAppStore } from './stores/app'
import AppToast from './components/AppToast.vue'
import CommandPalette from './components/CommandPalette.vue'
import RunProgress from './components/RunProgress.vue'

const route = useRoute()
const router = useRouter()
const store = useAppStore()
const cmdOpen = ref(false)

/* 五个空间，对应五种「我要做什么」：
 *
 *   首页（看状态）· 知识（读与改）· 问答（问）· 研究（查未知）· 设置（配置）
 *
 * 纠正 / 审核 / 评测 / Agent 工作台不在其中，因为它们不是*目的地*：纠正长在答案、
 * 卡片和搜索结果旁边（你是在读到错的东西时才想改它）；审核是一次待办，只在真的
 * 有事时出现；后两者只有维护系统本身时才用得上。一级菜单减少不等于功能消失——
 * 每一条都另有去路，见下方注释。
 */
const items = [
  { id: '/', icon: '⌂', text: '首页' },
  { id: '/knowledge', icon: '✦', text: '知识' },
  { id: '/qa', icon: '◎', text: '问答' },
  { id: '/research', icon: '◇', text: '研究' },
  { id: '/settings', icon: '⚙', text: '设置' },
]

/** 维护系统本身时才需要的界面，由设置里的 Developer Mode 决定是否出现。
    关掉只是不在侧栏占位置，路由仍然直连可用。 */
const devItems = [
  { id: '/agent', icon: '◌', text: 'Agent 工作台' },
  { id: '/extraction-experiment', icon: '◆', text: '抽取对比' },
  { id: '/eval', icon: '◈', text: '评测' },
]

const CRUMBS: Record<string, string> = {
  '/': '首页', '/knowledge': '知识', '/qa': '问答', '/research': '研究',
  '/review': '审核', '/correction': '纠正',
  '/agent': 'Agent 工作台', '/settings': '设置', '/settings/database': '设置 / Database',
  '/eval': '评测',
}
const crumb = computed(() => {
  const path = route.path
  if (path.startsWith('/knowledge/object/')) return ['知识', 'Knowledge Object']
  if (path.startsWith('/knowledge/claim/')) return ['知识', 'Claim']
  return (CRUMBS[path] || '首页').split(' / ')
})

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
        <div class="brandmark">✦</div>
        <div><b>LLM-Wiki</b><small>PERSONAL KNOWLEDGE OS</small></div>
      </div>
      <div class="navlbl">空间</div>
      <div v-for="it in items" :key="it.id" class="nav" :class="{ active: route.path === it.id || (it.id !== '/' && route.path.startsWith(it.id)) }" @click="router.push(it.id)">
        <span class="nico">{{ it.icon }}</span><span>{{ it.text }}</span>
      </div>

      <!-- 待确认是一个「状态」，不是一个「目的地」：它长得不像菜单项，也不算一级
           导航，只在真的有事时出现。点击才进入审核。 -->
      <div v-if="store.pendingReview" class="nav due" :class="{ active: route.path === '/review' }"
           :title="`${store.pendingReview} 条候选知识等你确认`" @click="router.push('/review')">
        <span class="nico">⚠</span><span>需要你确认</span>
        <span class="npill">{{ store.pendingReview }}</span>
      </div>

      <div class="spacer"></div>
      <template v-if="store.developerMode">
        <div class="navlbl">开发者</div>
        <div v-for="it in devItems" :key="it.id" class="nav"
             :class="{ active: route.path.startsWith(it.id) }" @click="router.push(it.id)">
          <span class="nico">{{ it.icon }}</span><span>{{ it.text }}</span>
        </div>
      </template>
      <div v-if="store.health" class="healthcard">
        <div class="healthrow"><span class="ok">● 运行正常</span><span>{{ store.health.version }}</span></div>
        <div class="healthrow"><span>SQLite · WAL</span><span>Local-first</span></div>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <div class="crumb">
          <span>{{ crumb[0] }}</span>
          <span v-if="crumb[1]" class="sep">/</span>
          <b v-if="crumb[1]">{{ crumb[1] }}</b>
        </div>
        <div class="grow"></div>
        <div class="omni" @click="cmdOpen = true">
          <span>⌕</span><span>搜索知识，或直接提问…</span><kbd>⌘K</kbd>
        </div>
      </header>
      <div class="content">
        <RouterView />
      </div>
    </main>

    <CommandPalette :open="cmdOpen" @close="cmdOpen = false" />
    <nav class="mobilebar">
      <button v-for="it in MOBILE_ITEMS" :key="it.id" :class="{ active: route.path === it.id || (it.id !== '/' && route.path.startsWith(it.id)) }" @click="router.push(it.id)">
        <span>{{ it.icon }}</span><small>{{ it.text }}</small>
      </button>
      <button @click="cmdOpen = true"><span>⌘</span><small>命令</small></button>
    </nav>
    <RunProgress />
    <AppToast />
  </div>
</template>

<style scoped>
.npill{margin-left:auto;font-size:8.5px;background:var(--tint-amber);color:#b97b22;border-radius:99px;padding:2px 7px;font-weight:700}
/* 待确认：读起来像提醒，不像一个空间 */
.nav.due{color:#b97b22;background:var(--tint-amber)}
.nav.due:hover{background:var(--tint-amber);color:#9c6a12}
.nav.due.active{background:var(--tint-amber);color:#b97b22;font-weight:650}
.mobilebar{display:none}
.side{background:rgba(255,255,255,.72);backdrop-filter:blur(18px);border-right:1px solid var(--hair);padding:20px 13px 14px;display:flex;flex-direction:column;min-height:0;overflow:auto}
.brand{display:flex;align-items:center;gap:11px;padding:4px 8px 22px}
.brandmark{width:34px;height:34px;border-radius:11px;background:linear-gradient(120deg,#5b7cff,#8b67f7);color:#fff;display:grid;place-items:center;font-size:15px;box-shadow:0 8px 20px rgba(91,124,255,.28)}
.brand b{font-size:15.5px;display:block;letter-spacing:-.02em}
.brand small{display:block;font-size:8.5px;color:var(--faint);margin-top:2px;letter-spacing:.06em}
.navlbl{padding:10px 10px 5px;color:var(--faint);font-size:8.5px;letter-spacing:.14em;font-weight:700}
.nav{display:flex;align-items:center;gap:11px;padding:9px 11px;border-radius:11px;margin:2px 0;color:var(--sub);font-size:11.5px;transition:.16s;position:relative;cursor:pointer}
.nav:hover{background:#f2f5fc;color:var(--text)}
.nav.active{background:linear-gradient(90deg,rgba(91,124,255,.10),rgba(139,103,247,.08));color:#4a63e8;font-weight:650}
.nav.active:before{content:"";position:absolute;left:0;top:9px;bottom:9px;width:3px;border-radius:99px;background:linear-gradient(120deg,#5b7cff,#8b67f7)}
.nico{width:19px;text-align:center;font-size:13px}
.topbar{height:62px;display:flex;align-items:center;gap:12px;padding:0 26px;border-bottom:1px solid var(--hair);background:rgba(246,248,252,.8);backdrop-filter:blur(14px)}
.content{flex:1;min-height:0;overflow:auto;padding:26px 30px 40px}
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
  .mobilebar button.active{color:#4a63e8;font-weight:650;background:linear-gradient(90deg,rgba(91,124,255,.10),rgba(139,103,247,.08))}
}
</style>
