export interface Health {
  ok: boolean; version: string; llm_model: string; embedding_model: string
  llm_configured: boolean; database_path: string
}

export interface DocumentRow {
  id: string; title: string; source_type: string; source_uri: string | null
  created_at: string; updated_at: string
  chunk_count: number; last_run_status: string | null; last_run_at: string | null
  content?: string; metadata?: Record<string, unknown>
}

export interface Chunk { id: string; document_id: string; content: string; chunk_index: number; start_offset: number; end_offset: number; created_at: string }

export interface Entity {
  id: string; type: string; name: string; description: string | null; status: string
  aliases?: string[]; properties?: Record<string, unknown>; created_at?: string; updated_at?: string
  claims?: any[]
}

export interface Claim {
  id: string; subject_id: string; subject_name?: string; object_id: string | null; object_name?: string
  predicate: string; object_text: string | null; content: string | null; claim_type: string
  polarity: string; modality: string; confidence: number; status: string
  source_document_id: string; source_chunk_id: string
  source_start_offset: number; source_end_offset: number; source_quote: string | null
  context?: Record<string, any>
  /** The registry version in force when this claim was compiled (/api/claims/{id}). */
  ontology_version?: string | null
  created_at?: string
}

export interface Relation {
  id: string; source_id: string; target_id: string; predicate: string; confidence: number | null
  status: string; source_name?: string; target_name?: string; source_document_id?: string
}

export interface Run {
  id: string; task_type: string; document_id: string | null; document_title?: string | null
  agent_role: string | null; model: string | null; status: string; step_count: number
  summary: Record<string, any>; error_message: string | null; duration_ms: number | null
  created_at: string; finished_at: string | null
}

export interface RunStep {
  id: string; run_id: string; step_index: number; name: string; status: string
  input_summary: string | null; output_text: string | null; error_message: string | null
  duration_ms: number | null; created_at: string
}

export interface EventRow {
  id: string; event_type: string; description: string; participants: string[]
  time: { start?: string | null; end?: string | null; precision?: string }
  location: string | null; status: string; confidence: number | null
  source_document_id: string; source_quote: string | null; created_at: string
}

export interface IdeaRow { id: string; content: string; status: string; confidence: number | null; source_document_id: string; source_quote: string | null; created_at: string }
export interface QuestionRow { id: string; content: string; status: string; source_document_id: string; source_quote: string | null; created_at: string }

export interface TimeseriesPoint { date: string; documents: number; entities: number; claims: number }

export interface Settings {
  openai_base_url: string; openai_model: string; openai_embedding_model: string
  embedding_dims: number; openai_api_key_configured: boolean; database_path: string
  llm_batch_chunks: number; max_search_results: number
  auto_embed: boolean; agentscope_enabled: boolean
}

export interface PromptProfile {
  id: string; name: string; description: string; skill?: string; editable_scope?: string
  custom_prompt: string; history: { version: number; created_at: string; note: string }[]
  effective_prompt_preview?: string
  /* Context Token panel (P4) */
  context_sections?: { name: string; tokens: number; source: string; policy: string; required: boolean }[]
  context_withheld?: { id: string; name: string; tokens: number; reason: string }[]
  context_tokens?: number; context_budget?: number; context_target_budget?: number
  custom_prompt_tokens?: number; recommended_max_tokens?: number
  prompt_status?: 'efficient' | 'long'; prompt_suggestion?: string | null
}

export interface ContextRun {
  id: string; run_id: string | null; agent_name: string
  budget_tokens: number; actual_tokens: number; trimmed_tokens: number
  efficiency: number; over_budget: number; created_at: string
  optimizations: string[]
  withheld: { id: string; name: string; type?: string; tokens: number; reason: string }[]
}

export interface ContextSection {
  name: string; policy: string; source: string; reason: string | null
  tokens: number; chars: number; trimmed: number
}

export interface GraphPayload {
  nodes: { id: string; type: string; name: string; description: string | null; status: string }[]
  edges: { id: string; source_id: string; target_id: string; predicate: string; confidence: number | null; status: string; source_name?: string; target_name?: string }[]
  claims: any[]
}

/* ───── Evaluation Dashboard (Phase 7) ───── */
export type EvalSuite = 'extraction' | 'qa' | 'retrieval'

/** Aggregated scalar metrics for a run. Keys depend on the suite (see EvalDashboard). */
export type EvalSummary = Record<string, number>

/** One entry in the run-history list (no report). */
export interface EvalRunListItem {
  run_id: string
  suite: EvalSuite
  created_at: string
  summary: EvalSummary
}

/** Full run detail, including the complete report. */
export interface EvalRunDetail {
  run_id: string
  suite: EvalSuite
  created_at: string
  summary: EvalSummary
  report: EvalReport
}

/** The report payload: case_details is an array of per-case metric objects. */
export interface EvalReport {
  case_details?: EvalCaseDetail[]
  [key: string]: unknown
}

/** A single case's metrics. Shape is open because each suite reports different fields. */
export type EvalCaseDetail = Record<string, unknown>

/** Baseline response. With `?suite=`: { suite, metrics }. Without: { suites }. */
export interface EvalBaseline {
  suite?: EvalSuite
  metrics?: Record<string, number>
  suites?: Record<string, Record<string, number>>
}

/** Body for POST /api/eval/baseline/pin */
export interface EvalBaselinePin {
  suite: EvalSuite
  run_id: string
}
