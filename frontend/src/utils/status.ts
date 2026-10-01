/**
 * One place that turns backend status enums into words a user reads.
 *
 * The backend legitimately has three status machines (knowledge objects, ideas,
 * questions) plus run statuses, and several of them mean the same thing to a
 * person ("candidate" and "open" are both "waiting for you"). Every surface
 * renders through this map instead of printing the raw enum, so the vocabulary
 * stays consistent as new statuses appear.
 *
 * 标签文案已迁到 i18n（`status.<enum>` 键）；这里只保留色调与兜底。
 * 翻译助手直接用 i18n 全局实例（组合式模式下全局 scope 对 locale 变化是响应式的）。
 */
import { i18n } from '@/i18n'

export type StatusTone = 'green' | 'amber' | 'red' | 'blue' | ''

const STATUS_TONES: Record<string, StatusTone> = {
  // 知识对象：entity / claim / relation
  draft: '',
  candidate: 'amber',
  verified: 'green',
  rejected: 'red',
  archived: '',
  superseded: 'blue',

  // 想法
  accepted: 'green',
  implemented: 'green',

  // 问题
  open: 'amber',
  answered: 'green',
  partially_answered: 'amber',
  resolved: 'green',

  // 运行 / 任务
  started: 'blue',
  running: 'blue',
  queued: 'blue',
  processing: 'blue',
  ongoing: 'blue',
  success: 'green',
  completed: 'green',
  processed: 'green',
  failed: 'red',
  invalid: 'red',
  missing: 'red',

  // 事件
  cancelled: '',
  unknown: '',

  // 其它
  ok: 'green',
  valid: 'green',
  grounded: 'green',
  partial: 'amber',
  review: 'amber',
  planned: '',
}

export interface StatusStyle { key: string; tone: StatusTone }

/** Unknown values keep the raw enum as the key — never invent a label for it. */
export function statusStyle(status: string | null | undefined): StatusStyle {
  if (!status) return { key: '—', tone: '' }
  return { key: status, tone: STATUS_TONES[status] ?? '' }
}

/** 翻译后的状态词。未知枚举原样显示，绝不编造。 */
export function statusLabel(status: string | null | undefined): string {
  if (!status) return '—'
  const key = `status.${status}`
  return i18n.global.te(key) ? i18n.global.t(key) : status
}
