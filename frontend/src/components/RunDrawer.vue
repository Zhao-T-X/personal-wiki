<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from '../api/client'
import type { Run, RunStep } from '../api/types'
import StatusTag from './StatusTag.vue'
import AppDrawer from './AppDrawer.vue'
import { fmtDateTime } from '../utils/time'

const props = defineProps<{ runId: string | null }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const run = ref<Run | null>(null)
const steps = ref<RunStep[]>([])

watch(() => props.runId, async (id) => {
  run.value = null; steps.value = []
  if (!id) return
  const d = await api<Run & { steps: RunStep[] }>('/api/runs/' + id)
  run.value = d; steps.value = d.steps || []
})

async function copyOutput(text: string | null) {
  if (!text) return
  await navigator.clipboard.writeText(text)
}
</script>

<template>
  <AppDrawer :open="!!runId" :title="'Run 详情'" @close="emit('close')">
    <template v-if="run">
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        <StatusTag :status="run.status" />
        <span class="tag blue">{{ run.task_type }}</span>
        <span class="tag">{{ run.agent_role || '—' }}</span>
        <span class="tag">{{ run.model || '—' }}</span>
      </div>
      <div class="kv">
        <span>目标</span><b>{{ run.document_title || run.agent_role || '—' }}</b>
        <span>开始</span><b>{{ fmtDateTime(run.created_at) }}</b>
        <span>耗时</span><b>{{ run.duration_ms ?? '—' }} ms</b>
        <span>步骤</span><b>{{ run.step_count }}</b>
      </div>
      <div v-if="run.error_message" class="quote" style="border-color:#f3d7d9;background:#fff7f7;color:#c8565f">{{ run.error_message }}</div>
      <h3 style="margin-top:14px">Summary</h3>
      <div class="log" style="height:auto;max-height:180px">{{ JSON.stringify(run.summary, null, 2) }}</div>
      <h3 style="margin-top:14px">Steps</h3>
      <div class="list">
        <div v-for="s in steps" :key="s.id" class="listitem">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <b>#{{ s.step_index }} {{ s.name }}</b>
            <span style="display:flex;gap:6px;align-items:center">
              <StatusTag :status="s.status" />
              <span class="muted" style="font-size:8px">{{ s.duration_ms ?? '—' }} ms</span>
            </span>
          </div>
          <p>{{ s.input_summary || '—' }}</p>
          <div v-if="s.error_message" class="quote" style="border-color:#f3d7d9;background:#fff7f7;color:#c8565f">{{ s.error_message }}</div>
          <details v-if="s.output_text" style="margin-top:6px">
            <summary style="cursor:pointer;font-size:9px;color:var(--sub)">LLM output
              <button class="btn" style="margin-left:8px;padding:2px 7px" @click.stop="copyOutput(s.output_text)">复制</button>
            </summary>
            <div class="log" style="height:auto;max-height:260px;margin-top:6px">{{ s.output_text }}</div>
          </details>
        </div>
      </div>
    </template>
  </AppDrawer>
</template>
