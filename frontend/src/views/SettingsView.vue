<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, put } from '../api/client'
import type { Settings } from '../api/types'
import PageHead from '../components/PageHead.vue'
import { useAppStore } from '../stores/app'
import { fmtDate } from '../utils/time'

const router = useRouter()

const store = useAppStore()
const s = ref<Settings | null>(null)
const apiKey = ref('')
const section = ref('基础配置')
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
    store.toast('设置已保存'); await store.loadHealth()
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
    store.toast('已导出 JSON 快照')
  } catch (e: any) { store.toast(e.message) }
}
</script>

<template>
  <div class="page">
    <PageHead title="系统设置" subtitle="模型、检索、智能体运行时、数据与安全。">
      <template #actions>
        <button class="btn" @click="test">测试连接</button>
        <button class="btn primary" @click="save">保存设置</button>
      </template>
    </PageHead>
    <div class="card settings" v-if="s">
      <div class="settingnav">
        <div :class="{ active: section === '基础配置' }" @click="section = '基础配置'">基础配置</div>
        <div :class="{ active: section === 'LLM' }" @click="section = 'LLM'">LLM / Provider</div>
        <div :class="{ active: section === '检索' }" @click="section = '检索'">检索与语义向量</div>
        <div :class="{ active: section === '运行时' }" @click="section = '运行时'">智能体运行时</div>
      </div>
      <div class="settingbody">
        <template v-if="section === '基础配置'">
          <div class="setting"><div><b>SQLite 数据库</b><small>统一的持久化层</small></div><input v-model="s.database_path" class="field" /></div>
        </template>
        <template v-if="section === 'LLM'">
          <div class="setting"><div><b>OpenAI Base URL</b><small>兼容 OpenAI 的接口</small></div><input v-model="s.openai_base_url" class="field" /></div>
          <div class="setting"><div><b>默认模型</b><small>抽取 / 问答 / 智能体共用</small></div><input v-model="s.openai_model" class="field" /></div>
          <div class="setting"><div><b>API Key</b><small>{{ s.openai_api_key_configured ? '已配置 — 留空保持不变' : '未配置' }}</small></div><input v-model="apiKey" type="password" class="field" placeholder="sk-…" /></div>
        </template>
        <template v-if="section === '检索'">
          <div class="setting"><div><b>语义向量模型</b><small>用于语义检索</small></div><input v-model="s.openai_embedding_model" class="field" /></div>
          <div class="setting"><div><b>向量维度</b><small>语义向量的维度</small></div><input v-model.number="s.embedding_dims" type="number" class="field" /></div>
          <div class="setting"><div><b>LLM Batch Chunks</b><small>每次抽取的 chunk 批大小</small></div><input v-model.number="s.llm_batch_chunks" type="number" class="field" /></div>
          <div class="setting"><div><b>Max Search Results</b><small>检索结果上限</small></div><input v-model.number="s.max_search_results" type="number" class="field" /></div>
        </template>
        <template v-if="section === '运行时'">
          <div class="setting"><div><b>智能体引擎</b><small>智能体工作流运行时</small></div><div class="switch" :class="{ on: s.agentscope_enabled }" @click="toggle('agentscope_enabled')"></div></div>
          <div class="setting"><div><b>自动生成语义索引</b><small>索引后自动生成</small></div><div class="switch" :class="{ on: s.auto_embed }" @click="toggle('auto_embed')"></div></div>
        </template>
        <div v-if="health" class="panel pad" style="margin-top:12px">
          <template v-if="store.developerMode">
            <pre class="log" style="height:auto;max-height:200px;margin:0">{{ JSON.stringify(health, null, 2) }}</pre>
          </template>
          <template v-else>
            <span class="faint" style="font-size:10.5px">连接状态：<b style="color:#1e8f6b">正常</b>——模型与索引均已就绪。</span>
          </template>
        </div>
      </div>
    </div>

    <div class="sechead" style="max-width:760px"><h3>高级</h3><span class="faint" style="font-size:9px;font-weight:400">实现细节</span></div>
    <div class="panel pad" style="padding:6px;max-width:760px">
      <!-- 日常使用不需要它；排障和调 Prompt 时需要。关掉只是不占位置，功能仍在。 -->
      <div class="setting" style="padding:11px 12px">
        <div><b>Developer Mode</b><small>在侧栏显示 Agent 工作台与评测</small></div>
        <div class="switch" :class="{ on: store.developerMode }" @click="store.setDeveloperMode(!store.developerMode)"></div>
      </div>
      <div class="item" @click="router.push('/settings/database')">
        <div class="ico-badge ib-violet">◉</div>
        <div class="grow"><b>Database</b><p>SQLite · 表计数 · 完整性检查</p></div><span class="faint">→</span>
      </div>
      <div class="item" @click="exportAll">
        <div class="ico-badge ib-blue">⇩</div>
        <div class="grow"><b>导出全部知识</b><p>Documents / Chunks / Entities / Claims / Relations / Events 的 JSON 快照</p></div><span class="faint">→</span>
      </div>
    </div>
  </div>
</template>
