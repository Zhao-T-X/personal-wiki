<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api/client'
import { useAppStore } from './stores/app'
import AppToast from './components/AppToast.vue'
import CommandPalette from './components/CommandPalette.vue'
import RunProgress from './components/RunProgress.vue'

const route = useRoute()
const router = useRouter()
const store = useAppStore()
const cmdOpen = ref(false)
const pendingReview = ref(0)

async function loadBadges() {
  try {
    const rv = await api<any>('/api/review?limit=1')
    pendingReview.value = (rv.entities?.length || 0) + (rv.claims?.length || 0) + (rv.relations?.length || 0)
  } catch { pendingReview.value = 0 }
}

const items = [
  { id: '/', icon: '⌂', text: '首页' },
  { id: '/knowledge', icon: '✦', text: '知识' },
  { id: '/qa', icon: '◎', text: '问答' },
  { id: '/review', icon: '✓', text: '审核' },
  { id: '/correction', icon: '✎', text: '纠正' },
  { id: '/research', icon: '◇', text: '研究' },
  { id: '/agent', icon: '◌', text: 'Agent 工作台' },
  { id: '/settings', icon: '⚙', text: '设置' },
  { id: '/eval', icon: '◈', text: '评测' },
]

const CRUMBS: Record<string, string> = {
  '/': '首页', '/knowledge': '知识', '/qa': '问答', '/review': '审核', '/research': '研究',
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
onMounted(() => { window.addEventListener('keydown', onKey); store.loadHealth(); loadBadges() })
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

const MOBILE_ITEMS = [
  { id: '/', icon: '⌂', text: '首页' },
  { id: '/knowledge', icon: '✦', text: '知识' },
  { id: '/qa', icon: '◎', text: '问答' },
  { id: '/research', icon: '◇', text: '研究' },
  { id: '/agent', icon: '◌', text: 'Agent' },
  { id: '/eval', icon: '◈', text: '评测' },
]
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
        <span v-if="it.id === '/review' && pendingReview" class="npill" :title="`${pendingReview} 条待审候选`">{{ pendingReview }}</span>
      </div>
      <div class="spacer"></div>
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
