/**
 * One place that turns backend status enums into words a user reads.
 *
 * The backend legitimately has three status machines (knowledge objects, ideas,
 * questions) plus run statuses, and several of them mean the same thing to a
 * person ("candidate" and "open" are both "waiting for you"). Every surface
 * renders through this map instead of printing the raw enum, so the vocabulary
 * stays consistent as new statuses appear.
 */
export type StatusTone = 'green' | 'amber' | 'red' | 'blue' | ''

export interface StatusStyle { label: string; tone: StatusTone }

const STATUS: Record<string, StatusStyle> = {
  // 知识对象：entity / claim / relation
  draft: { label: '草稿', tone: '' },
  candidate: { label: '待审', tone: 'amber' },
  verified: { label: '已验证', tone: 'green' },
  rejected: { label: '已拒绝', tone: 'red' },
  archived: { label: '已归档', tone: '' },
  superseded: { label: '已被取代', tone: 'blue' },

  // 想法
  accepted: { label: '已采纳', tone: 'green' },
  implemented: { label: '已实现', tone: 'green' },

  // 问题
  open: { label: '待研究', tone: 'amber' },
  answered: { label: '已回答', tone: 'green' },
  partially_answered: { label: '部分回答', tone: 'amber' },
  resolved: { label: '已解决', tone: 'green' },

  // 运行 / 任务
  started: { label: '进行中', tone: 'blue' },
  running: { label: '进行中', tone: 'blue' },
  queued: { label: '排队中', tone: 'blue' },
  processing: { label: '处理中', tone: 'blue' },
  ongoing: { label: '进行中', tone: 'blue' },
  success: { label: '成功', tone: 'green' },
  completed: { label: '已完成', tone: 'green' },
  processed: { label: '已处理', tone: 'green' },
  failed: { label: '失败', tone: 'red' },
  invalid: { label: '无效', tone: 'red' },
  missing: { label: '缺失', tone: 'red' },

  // 事件
  cancelled: { label: '已取消', tone: '' },
  unknown: { label: '未知', tone: '' },

  // 其它
  ok: { label: '正常', tone: 'green' },
  valid: { label: '有效', tone: 'green' },
  grounded: { label: '有据可依', tone: 'green' },
  partial: { label: '部分完成', tone: 'amber' },
  review: { label: '待审', tone: 'amber' },
  planned: { label: '已计划', tone: '' },
}

/** Unknown values fall through to the raw enum — never invent a label for it. */
export function statusStyle(status: string | null | undefined): StatusStyle {
  if (!status) return { label: '—', tone: '' }
  return STATUS[status] || { label: status, tone: '' }
}
