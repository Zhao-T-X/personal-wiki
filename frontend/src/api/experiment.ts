import { api, post } from './client'

export interface ExperimentCorpusDoc {
  id: string
  name: string
  source: string
  expected_entities: string[]
  expected_non_entities: string[]
  proposed_entities: string[]
}

export interface ExperimentSnapshotView {
  kept: string[]
  total_entities: number | null
  dropped_entities: number
  review_entities: number
  unsupported_entity_rate: number | null
  claims: number | null
  experiment_tag: string | null
  eligibility_on: boolean | null
  extraction_version: string | null
}

export interface ExperimentSnapshot {
  run_id: string
  document_id: string
  created_at: string
  view: ExperimentSnapshotView
}

export interface ExperimentCompare {
  before: ExperimentSnapshotView
  after: ExperimentSnapshotView
  removed: string[]
  added: string[]
  kept_both: string[]
}

export const getExperimentCorpus = () => api<{ version: string; documents: ExperimentCorpusDoc[] }>('/api/experiments/corpus')
export const runCorpusExperiment = () => post<unknown>('/api/experiments/run-corpus')
export const getExperimentSnapshots = (tag?: string) =>
  api<ExperimentSnapshot[]>('/api/experiments/snapshots' + (tag ? '?tag=' + encodeURIComponent(tag) : ''))
export const compareExperiments = (before: string, after: string) =>
  api<ExperimentCompare>('/api/experiments/compare?before=' + encodeURIComponent(before) + '&after=' + encodeURIComponent(after))
export const runDocumentExperiment = (content: string, title: string, tag?: string) =>
  post<unknown>('/api/experiments/run', { content, title, tag })
