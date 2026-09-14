import { api, post } from './client'
import type {
  EvalBaseline,
  EvalBaselinePin,
  EvalRunDetail,
  EvalRunListItem,
  EvalSuite,
} from './types'

/** Kick off an evaluation run for a suite. */
export const runEval = (suite: EvalSuite) =>
  post<EvalRunDetail>('/api/eval/run', { suite })

/** List recent runs (summary only). */
export const listEvalRuns = (limit = 20) =>
  api<EvalRunListItem[]>('/api/eval/runs?limit=' + limit)

/** Fetch a single run's full detail (summary + report). */
export const getEvalRun = (runId: string) =>
  api<EvalRunDetail>('/api/eval/runs/' + encodeURIComponent(runId))

/** Fetch baseline metrics. Pass a suite for a single suite, omit for all suites. */
export const getEvalBaseline = (suite?: EvalSuite) =>
  api<EvalBaseline>('/api/eval/baseline' + (suite ? '?suite=' + suite : ''))

/** Pin a run as the baseline for its suite (optional affordance). */
export const pinEvalBaseline = (payload: EvalBaselinePin) =>
  post('/api/eval/baseline/pin', payload)
