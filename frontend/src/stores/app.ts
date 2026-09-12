import { defineStore } from 'pinia'
import { api } from '../api/client'
import type { Health } from '../api/types'

/** Optional affordance attached to a toast, so destructive actions stay reversible. */
export interface ToastAction { label: string; run: () => unknown }

/** A document extraction currently running in the backend. */
export interface ExtractionProgress {
  documentId: string
  title: string
  startedAt: number
  runId: string | null
  status: string
  stage: string
  batch: { done: number; total: number } | null
  stepCount: number
}

/** Single-flight toast timer; module scope keeps it out of reactive state. */
let toastTimer: number | undefined

/* Extraction polling lives here rather than in a view: analysing a document can
   take a while, and the user must be able to switch pages meanwhile. */
let extractionTimer: number | undefined
/** Consecutive polls where the backend run had already finished. */
let settledTicks = 0
/** Absolute ceiling so a stuck request can never poll forever. */
const EXTRACTION_POLL_MAX_MS = 15 * 60_000

type ExtractionStore = {
  extraction: ExtractionProgress | null
  clearExtraction: () => void
  toast: (msg: string) => void
}

function stopExtractionPolling() {
  window.clearInterval(extractionTimer)
  extractionTimer = undefined
}

/** Map the backend's extraction steps to language a user can follow. */
async function pollExtraction(store: ExtractionStore) {
  const active = store.extraction
  // Defensive: state was cleared but the timer survived (e.g. a hot reload) —
  // stop ourselves instead of polling nothing forever.
  if (!active) { stopExtractionPolling(); return }

  if (Date.now() - active.startedAt > EXTRACTION_POLL_MAX_MS) {
    store.clearExtraction()
    store.toast(`《${active.title}》抽取跟踪超时，已停止`)
    return
  }

  try {
    const runs = await api<any[]>('/api/runs?task_type=extract&limit=5')
    const run = runs.find((r: any) => r.document_id === active.documentId)
    if (!run) { active.stage = '正在分块文档'; settledTicks = 0; return }
    active.runId = run.id
    active.status = run.status
    active.stepCount = run.step_count || 0
    if (run.status !== 'started') {
      active.stage = '正在整理结果…'
      // The caller's POST normally clears this state. If that request hangs (or
      // the page was reloaded mid-extraction), polling must converge on its own.
      settledTicks += 1
      if (settledTicks >= 3) {
        store.clearExtraction()
        store.toast(`《${active.title}》抽取已结束`)
      }
      return
    }
    settledTicks = 0
    const detail = await api<{ steps: any[] }>('/api/runs/' + run.id)
    const steps = detail.steps || []
    const batchStep = [...steps].reverse().find(s => s.name === 'extract_batch')
    if (batchStep) {
      // `input_summary` is "batch 3/8 · 4 chunks · 5120 chars" — a real denominator.
      const m = /batch (\d+)\/(\d+)/.exec(batchStep.input_summary || '')
      if (m) active.batch = { done: Number(m[1]), total: Number(m[2]) }
      active.stage = '正在抽取知识'
    } else if (steps[steps.length - 1]?.name === 'extract_repair') {
      active.stage = '修正格式后重试'
    } else {
      active.stage = '正在分块文档'
    }
  } catch { /* transient failure — keep polling */ }
}

export const useAppStore = defineStore('app', {
  state: () => ({
    health: null as Health | null,
    toastMsg: '',
    toastAction: null as ToastAction | null,
    searchTerm: '',
    extraction: null as ExtractionProgress | null,
  }),
  actions: {
    async loadHealth() {
      try { this.health = await api<Health>('/api/health') } catch { this.health = null }
    },
    /**
     * Show a transient message. Pass `action` to make the result reversible —
     * an actionable toast lingers longer so the user can actually hit Undo.
     */
    toast(msg: string, action?: ToastAction) {
      window.clearTimeout(toastTimer)
      this.toastMsg = msg
      this.toastAction = action ?? null
      toastTimer = window.setTimeout(() => this.dismissToast(), action ? 6000 : 1800)
    },
    dismissToast() {
      window.clearTimeout(toastTimer)
      this.toastMsg = ''
      this.toastAction = null
    },
    /** Begin showing progress for an extraction. Call before POST /index. */
    beginExtraction(documentId: string, title: string) {
      stopExtractionPolling()
      settledTicks = 0
      this.extraction = {
        documentId, title, startedAt: Date.now(),
        runId: null, status: 'started', stage: '正在读取文档', batch: null, stepCount: 0,
      }
      extractionTimer = window.setInterval(() => void pollExtraction(this), 2500)
    },
    /** Stop showing progress. Call once the index request settles, success or not. */
    clearExtraction() {
      stopExtractionPolling()
      settledTicks = 0
      this.extraction = null
    },
  },
})
