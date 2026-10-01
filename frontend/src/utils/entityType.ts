import { i18n } from '../i18n'

/**
 * Ontology types: the database stores English controlled vocabulary (Person / Concept …);
 * the UI always shows the product language via i18n.
 *
 * This is the boundary between "system language" and "product language" (PRD §11): the type id
 * is a backend contract and never changes; what the user sees is localized. Anywhere that renders
 * `e.type` raw is a leak.
 */
export const ENTITY_TYPES: { id: string; key: string }[] = [
  { id: 'Person', key: 'entityType.Person' },
  { id: 'Organization', key: 'entityType.Organization' },
  { id: 'Product', key: 'entityType.Product' },
  { id: 'Software', key: 'entityType.Software' },
  { id: 'Technology', key: 'entityType.Technology' },
  { id: 'Method', key: 'entityType.Method' },
  { id: 'Concept', key: 'entityType.Concept' },
  { id: 'Theory', key: 'entityType.Theory' },
  { id: 'Dataset', key: 'entityType.Dataset' },
  { id: 'Model', key: 'entityType.Model' },
  { id: 'Standard', key: 'entityType.Standard' },
  { id: 'Protocol', key: 'entityType.Protocol' },
  { id: 'Resource', key: 'entityType.Resource' },
  { id: 'Location', key: 'entityType.Location' },
]

const KEYS: Record<string, string> = Object.fromEntries(ENTITY_TYPES.map(t => [t.id, t.key]))

/** 未知类型原样返回：宁可显示后端给的新类型，也不假装它不存在。 */
export function entityTypeLabel(type?: string | null): string {
  if (!type) return ''
  const key = KEYS[type]
  if (!key) return type
  try { return i18n.global.t(key) } catch { return type }
}
