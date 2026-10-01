<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api, put } from '../api/client'
import type { Settings } from '../api/types'
import PageHead from '../components/PageHead.vue'
import { useAppStore } from '../stores/app'
import { fmtDate } from '../utils/time'

const router = useRouter()
const { t } = useI18n()

const store = useAppStore()
const s = ref<Settings | null>(null)
const apiKey = ref('')
/** 内部键稳定，标签走 i18n。 */
const section = ref('basic')
const SECTIONS = computed(() => ([
  { key: 'basic', label: t('settings.nav.basic') },
  { key: 'llm', label: t('settings.nav.llm') },
  { key: 'retrieval', label: t('settings.nav.retrieval') },
  { key: 'runtime', label: t('settings.nav.runtime') },
]))
const health = ref<Record<string, any> | null>(null)

onMounted(async () => { s.value = await api<Settings>('/api/settings') })

function toggle(key: 'auto_embed' | 'agentscope_enabled') {
  if (s.value) (s.value as any)[key] = !(s.value as any)[key]
}

async function save() {
  if (!s.value) return
  const payload: Record<string, unknown> = {
    openai_base_url: s.value.openai_base_url,
    openai_model: s.value.openai_model,
    openai_embedding_model: s.value.openai_embedding_model,
    embedding_dims: Number(s.value.embedding_dims),
    database_path: s.value.database_path,
    llm_batch_chunks: Number(s.value.llm_batch_chunks),
    max_search_results: Number(s.value.max_search_results),
    auto_embed: s.value.auto_embed,
    agentscope_enabled: s.value.agentscope_enabled,
  }
  if (apiKey.value.trim()) payload.openai_api_key = apiKey.value.trim()
  try {
    s.value = await put<Settings>('/api/settings', payload)
    apiKey.value = ''
    store.toast(t('settings.saved')); await store.loadHealth()
  } catch (e: any) { store.toast(e.message) }
}

async function test() {
  try { health.value = await api('/api/health') } catch (e: any) { store.toast(e.message) }
}

async function exportAll() {
  try {
    const data = await api<any>('/api/export')
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `llm-wiki-export-${fmtDate(new Date())}.json`
    a.click()
    URL.revokeObjectURL(url)
    store.toast(t('settings.exported'))
  } catch (e: any) { store.toast(e.message) }
}
</script>

<template>
  <div class="page">
    <PageHead :title="t('settings.title')" :subtitle="t('settings.subtitle')">
      <template #actions>
        <button class="btn" @click="test">{{ t('settings.test') }}</button>
        <button class="btn primary" @click="save">{{ t('settings.save') }}</button>
      </template>
    </PageHead>
    <div class="card settings" v-if="s">
      <div class="settingnav">
        <div v-for="sec in SECTIONS" :key="sec.key" :class="{ active: section === sec.key }" @click="section = sec.key">{{ sec.label }}</div>
      </div>
      <div class="settingbody">
        <template v-if="section === 'basic'">
          <div class="setting"><div><b>{{ t('settings.database') }}</b><small>{{ t('settings.databaseHint') }}</small></div><input v-model="s.database_path" class="field" /></div>
        </template>
        <template v-if="section === 'llm'">
          <div class="setting"><div><b>{{ t('settings.baseUrl') }}</b><small>{{ t('settings.baseUrlHint') }}</small></div><input v-model="s.openai_base_url" class="field" /></div>
          <div class="setting"><div><b>{{ t('settings.model') }}</b><small>{{ t('settings.modelHint') }}</small></div><input v-model="s.openai_model" class="field" /></div>
          <div class="setting"><div><b>{{ t('settings.apiKey') }}</b><small>{{ s.openai_api_key_configured ? t('settings.apiKeyConfigured') : t('settings.apiKeyNot') }}</small></div><input v-model="apiKey" type="password" class="field" :placeholder="t('settings.apiKeyPlaceholder')" /></div>
        </template>
        <template v-if="section === 'retrieval'">
          <div class="setting"><div><b>{{ t('settings.embedModel') }}</b><small>{{ t('settings.embedModelHint') }}</small></div><input v-model="s.openai_embedding_model" class="field" /></div>
          <div class="setting"><div><b>{{ t('settings.dims') }}</b><small>{{ t('settings.dimsHint') }}</small></div><input v-model.number="s.embedding_dims" type="number" class="field" /></div>
          <div class="setting"><div><b>{{ t('settings.batchChunks') }}</b><small>{{ t('settings.batchChunksHint') }}</small></div><input v-model.number="s.llm_batch_chunks" type="number" class="field" /></div>
          <div class="setting"><div><b>{{ t('settings.maxResults') }}</b><small>{{ t('settings.maxResultsHint') }}</small></div><input v-model.number="s.max_search_results" type="number" class="field" /></div>
        </template>
        <template v-if="section === 'runtime'">
          <div class="setting"><div><b>{{ t('settings.agentscope') }}</b><small>{{ t('settings.agentscopeHint') }}</small></div><div class="switch" :class="{ on: s.agentscope_enabled }" @click="toggle('agentscope_enabled')"></div></div>
          <div class="setting"><div><b>{{ t('settings.autoEmbed') }}</b><small>{{ t('settings.autoEmbedHint') }}</small></div><div class="switch" :class="{ on: s.auto_embed }" @click="toggle('auto_embed')"></div></div>
        </template>
        <div v-if="health" class="panel pad" style="margin-top:12px">
          <template v-if="store.developerMode">
            <pre class="log" style="height:auto;max-height:200px;margin:0">{{ JSON.stringify(health, null, 2) }}</pre>
          </template>
          <template v-else>
            <span class="faint" style="font-size:10.5px">{{ t('settings.healthLabel') }}：<b style="color:#1e8f6b">{{ t('settings.healthOk') }}</b>{{ t('settings.healthOkText') }}</span>
          </template>
        </div>
      </div>
    </div>

    <div class="sechead" style="max-width:760px"><h3>{{ t('settings.advanced') }}</h3><span class="faint" style="font-size:9px;font-weight:400">{{ t('settings.advancedSub') }}</span></div>
    <div class="panel pad" style="padding:6px;max-width:760px">
      <!-- 日常使用不需要它；排障和调 Prompt 时需要。关掉只是不占位置，功能仍在。 -->
      <div class="setting" style="padding:11px 12px">
        <div><b>{{ t('settings.devMode') }}</b><small>{{ t('settings.devModeHint') }}</small></div>
        <div class="switch" :class="{ on: store.developerMode }" @click="store.setDeveloperMode(!store.developerMode)"></div>
      </div>
      <div class="item" @click="router.push('/settings/database')">
        <div class="ico-badge ib-violet">◉</div>
        <div class="grow"><b>{{ t('settings.databaseLink') }}</b><p>{{ t('settings.databaseLinkDesc') }}</p></div><span class="faint">→</span>
      </div>
      <div class="item" @click="exportAll">
        <div class="ico-badge ib-blue">⇩</div>
        <div class="grow"><b>{{ t('settings.export') }}</b><p>{{ t('settings.exportDesc') }}</p></div><span class="faint">→</span>
      </div>
    </div>
  </div>
</template>
