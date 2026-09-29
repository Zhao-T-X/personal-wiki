/**
 * 本体类型：数据库里存的是英文受控词（Person / Concept …），普通界面一律显示中文。
 *
 * 这是「系统语言」与「产品语言」的分界（PRD §11）：类型 id 是后端契约，永不改变；
 * 用户看到的永远是「人物 / 概念 / 组织」。凡是把 `e.type` 原样渲染的地方，都是漏出。
 */
export const ENTITY_TYPES: { id: string; label: string }[] = [
  { id: 'Person', label: '人物' },
  { id: 'Organization', label: '组织' },
  { id: 'Product', label: '产品' },
  { id: 'Software', label: '软件' },
  { id: 'Technology', label: '技术' },
  { id: 'Method', label: '方法' },
  { id: 'Concept', label: '概念' },
  { id: 'Theory', label: '理论' },
  { id: 'Dataset', label: '数据集' },
  { id: 'Model', label: '模型' },
  { id: 'Standard', label: '标准' },
  { id: 'Protocol', label: '协议' },
  { id: 'Resource', label: '资源' },
  { id: 'Location', label: '地点' },
]

const LABELS: Record<string, string> = Object.fromEntries(ENTITY_TYPES.map(t => [t.id, t.label]))

/** 未知类型原样返回：宁可显示后端给的新类型，也不假装它不存在。 */
export function entityTypeLabel(type?: string | null): string {
  if (!type) return ''
  return LABELS[type] || type
}
