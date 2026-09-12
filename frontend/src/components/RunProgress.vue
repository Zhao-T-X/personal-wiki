<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { post } from '../api/client'
import { useAppStore } from '../stores/app'

const store = useAppStore()
const elapsed = ref(0)
let ticker: number | undefined

watch(() => store.extraction, (v) => {
  window.clearInterval(ticker)
  if (!v) { elapsed.value = 0; return }
  elapsed.value = Math.floor((Date.now() - v.startedAt) / 1000)
  ticker = window.setInterval(() => {
    if (store.extraction) elapsed.value = Math.floor((Date.now() - store.extraction.startedAt) / 1000)
  }, 1000)
}, { immediate: true })
onBeforeUnmount(() => window.clearInterval(ticker))

/** Real progress when the backend reports batches; an indeterminate bar otherwise. */
const percent = computed(() => {
  const b = store.extraction?.batch
  if (!b || !b.total) return null
  return Math.max(4, Math.min(100, Math.round((b.done / b.total) * 100)))
})

async function cancel() {
  const id = store.extraction?.runId
  if (!id) return
  try { await post(`/api/runs/${id}/cancel`); store.toast('已请求取消（当前批次结束后生效）') }
  catch (e: any) { store.toast(e.message) }
}
</script>

<template>
  <div v-if="store.extraction" class="runprog">
    <div class="row">
      <span class="rpdot"></span>
      <b class="rptitle">{{ store.extraction.stage }}</b>
      <div class="grow"></div>
      <span class="faint" style="font-size:9px">{{ elapsed }}s</span>
    </div>
    <div class="rpdoc">{{ store.extraction.title }}</div>
    <div class="rpbar">
      <i :class="{ indet: percent === null }" :style="percent === null ? {} : { width: percent + '%' }"></i>
    </div>
    <div class="row" style="margin-top:8px">
      <span class="faint" style="font-size:9px">
        {{ store.extraction.batch ? `批次 ${store.extraction.batch.done}/${store.extraction.batch.total}` : '准备中' }}
        <template v-if="store.extraction.stepCount"> · 已完成 {{ store.extraction.stepCount }} 步</template>
      </span>
      <div class="grow"></div>
      <button class="btn sm" :disabled="!store.extraction.runId" @click="cancel">取消</button>
    </div>
  </div>
</template>

<style scoped>
.runprog{
  position:fixed;top:74px;right:18px;width:min(320px,92vw);z-index:25;
  background:rgba(255,255,255,.97);border:1px solid var(--line);border-radius:15px;
  box-shadow:0 16px 40px rgba(31,50,90,.14);padding:13px 15px;backdrop-filter:blur(14px);
  animation:fade .18s;
}
.rpdot{width:7px;height:7px;border-radius:50%;background:var(--blue);box-shadow:0 0 0 3px var(--tint-blue);animation:pulse 1.4s ease-in-out infinite}
.rptitle{font-size:11.5px}
.rpdoc{margin-top:6px;font-size:9.5px;color:var(--sub);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rpbar{margin-top:10px;height:5px;border-radius:99px;background:#eef2f9;overflow:hidden}
.rpbar i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,#5b7cff,#8b67f7);transition:width .4s ease}
.rpbar i.indet{width:35%;animation:slide 1.3s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
@keyframes slide{0%{transform:translateX(-100%)}100%{transform:translateX(320%)}}
</style>
