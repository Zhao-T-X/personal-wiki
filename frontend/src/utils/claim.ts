/**
 * One place that turns any backend claim shape into what a KnowledgeCard shows.
 *
 * Three endpoints hand out claims and none of them agrees on key names
 * (`/api/claims/{id}`, `/api/documents/{id}/knowledge`, `/api/review`). Mapping them
 * here means the card — and therefore every surface that uses it — reads one shape,
 * and supporting a new endpoint is a line in this file rather than a new layout.
 *
 * Two rules this file keeps:
 *
 * * **A field the backend did not send stays empty**, and the card hides that part.
 *   The mapping never fills a gap with a plausible value.
 * * **Predicate names become words only through the registry's labels**
 *   (`/api/ontology/predicates`). A table of translations here would be a second
 *   ontology living in the frontend, and it would drift the first time a predicate
 *   is added.
 */
import { ref } from 'vue'
import { api } from '../api/client'

export interface KnowledgeCardClaim {
  id: string
  subject: string
  predicate: string
  object: string
  status: string
  quote: string
  documentId: string
  chunkId: string
  createdAt: string
  /** The product's word for the lifecycle state, when the caller has one — the
      search/QA projections send it (`current` / `candidate` / `disputed` /
      `historical`). Absent elsewhere, and then the raw status is shown instead. */
  stateLabel?: string
}

/** Registry labels, loaded once per session. They change only when a maintainer
    edits `schemas/`, so re-fetching them per card would be pure noise. */
export const predicateLabels = ref<Record<string, string>>({})

export async function loadPredicateLabels(): Promise<void> {
  try {
    const body = await api<{ labels: Record<string, string | null> }>('/api/ontology/predicates')
    const named: Record<string, string> = {}
    for (const [predicate, label] of Object.entries(body.labels || {})) {
      if (label) named[predicate] = label
    }
    predicateLabels.value = named
  } catch {
    // A missing display layer must not blank the knowledge out: the predicate
    // itself is an honest fallback, and every caller gets it from `labelOf`.
    predicateLabels.value = {}
  }
}

/** The human name for a predicate, or the predicate itself when the registry
    declares none. Never a guess, never a second vocabulary. */
export function labelOf(predicate: string | null | undefined): string {
  if (!predicate) return ''
  return predicateLabels.value[predicate] || predicate
}

/** Whether the registry actually names this predicate — which `labelOf` cannot
    tell you, since it falls back to the identifier. Callers that build *sentences*
    ("X 的 首席执行官 是什么？") must ask this first: `X 的 has_ceo 是什么？` is worse
    than saying nothing. */
export function hasLabel(predicate: string | null | undefined): boolean {
  return !!predicate && !!predicateLabels.value[predicate]
}

/** First non-empty value among ``keys``. Dotted keys reach into nested objects,
    which is how a projection's `evidence.document_id` is read without flattening
    the endpoint's shape just to suit the card. */
function pick(raw: any, ...keys: string[]): string {
  for (const key of keys) {
    const value = key.split('.').reduce((acc: any, part) => (acc == null ? acc : acc[part]), raw)
    if (value !== null && value !== undefined && String(value) !== '') return String(value)
  }
  return ''
}

/** ``opts.documentId`` covers the shapes that list a document's own claims and so
    have no reason to repeat the document id on every row. */
export function toCard(raw: any, opts: { documentId?: string } = {}): KnowledgeCardClaim {
  return {
    id: pick(raw, 'id', 'claim_id'),
    subject: pick(raw, 'subject_name', 'subject_label', 'subject'),
    predicate: pick(raw, 'predicate'),
    object: pick(raw, 'object_name', 'object_label', 'object_text', 'object'),
    status: pick(raw, 'status') || 'candidate',
    stateLabel: pick(raw, 'status_label') || undefined,
    quote: pick(raw, 'source_quote', 'evidence_preview', 'quote', 'content'),
    documentId: pick(raw, 'source_document_id', 'evidence.document_id') || opts.documentId || '',
    chunkId: pick(raw, 'source_chunk_id', 'evidence.chunk_id'),
    createdAt: pick(raw, 'created_at', 'updated_at'),
  }
}
