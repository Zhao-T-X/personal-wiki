/**
 * Shared graph helpers. The global knowledge graph and the per-object local
 * graph must agree on colours, so the mapping lives here rather than in a view.
 */
export const TYPE_COLORS: Record<string, string> = {
  Technology: '#5b7cff', Concept: '#8a69e9', Dataset: '#45c49a', Software: '#62c6e4',
  Method: '#8b67f7', Model: '#f1a456', Person: '#ef7f9c', Organization: '#5ac7e8',
}

/** Fallback for entity types without a dedicated colour. */
export const DEFAULT_NODE_COLOR = '#f1a456'

/**
 * Normalise a backend edge to its endpoints. `/api/graph` and `/api/entities/{id}/graph`
 * carry `source_id/target_id` for relations but `subject_id/object_id` for claims.
 */
export function edgeEndpoints(e: any): { source: string | null; target: string | null } {
  return {
    source: e?.source_id || e?.subject_id || null,
    target: e?.target_id || e?.object_id || null,
  }
}
