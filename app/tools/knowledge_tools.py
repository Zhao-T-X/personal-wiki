"""Knowledge tools for the agents (Context Runtime P2b: compressed results).

These functions are the boundary between the SQLite knowledge base and the
ReAct conversation: whatever they return is appended to the model's context
verbatim. Every return value therefore goes through ``app.tools.compression``,
which keeps each result a *decision card* (ids + short quotes + trust fields)
instead of a raw row dump, and states what was cut when it cuts.

 Drill-down contract across tools:
 - ``search_knowledge``  -> chunk_id / document_id (quote only, never the chunk)
 - ``get_entities``      -> several compact entity cards in one call
 - ``get_entity``        -> the full-ish profile of ONE entity
 - ``get_entity_graph``  -> neighbourhood names/predicates, no descriptions
"""
from __future__ import annotations

import json

from .compression import _limit, compress_chunks, compress_entity, compress_graph
from ..db import connect, loads
from ..graph import neighborhood
from ..retrieval import search

# ``max_search_results`` is the existing hard cap in settings; the tool-level
# cap (20) stays stricter than it so a tool call can never fan out wider than
# the QA pipeline's own retrieval.
_MAX_TOOL_SEARCH = 20


def search_knowledge(query: str, limit: int = 8) -> str:
    """Search the user's LLM-Wiki knowledge base.

    Returns quote cards (ids + score + a short quote), not whole chunks. Use a
    chunk's document_id to inspect its document, or re-search with sharper terms.

    :param query: Natural language query.
    :param limit: Maximum number of results (1-20).
    """
    rows = search(query, max(1, min(int(limit), _MAX_TOOL_SEARCH)), semantic=True)
    cards = compress_chunks(rows)
    return json.dumps({'results': cards, 'count': len(cards)}, ensure_ascii=False)


def get_entities(entity_ids: list[str], include_claims: int = 2) -> str:
    """Get several entities at once as compact summary cards.

    Use this instead of repeated get_entity calls when scanning or comparing
    multiple entities. Each card carries the entity's core fields plus its
    strongest claims; call get_entity for the full profile of a single entity.

    :param entity_ids: Entity UUIDs, at most 8 per call.
    :param include_claims: Claims to show per entity (0-5).
    """
    ids = [i for i in (entity_ids or []) if i]
    if not ids:
        return json.dumps({'error': 'entity_ids must be a non-empty list'}, ensure_ascii=False)
    max_ids = _limit('tool_batch_size', 8)
    capped, overflow = ids[:max_ids], ids[max_ids:]
    per_entity = max(0, min(int(include_claims), 5))

    conn = connect()
    cards, not_found = [], []
    for entity_id in capped:
        row = conn.execute('SELECT * FROM entities WHERE id=?', (entity_id,)).fetchone()
        if not row:
            not_found.append(entity_id)
            continue
        e = dict(row)
        e['aliases'] = loads(e.pop('aliases_json'), [])
        e['properties'] = loads(e.pop('properties_json'), {})
        total = conn.execute('SELECT COUNT(*) FROM claims WHERE subject_id=? OR object_id=?',
                             (entity_id, entity_id)).fetchone()[0]
        claims = []
        if per_entity:
            claims = [dict(r) for r in conn.execute(
                '''SELECT c.id,c.predicate,c.content,c.object_text,c.confidence,c.status,
                          s.name subject_name,o.name object_name
                   FROM claims c JOIN entities s ON s.id=c.subject_id
                       LEFT JOIN entities o ON o.id=c.object_id
                   WHERE c.subject_id=? OR c.object_id=?
                   ORDER BY (c.status='verified') DESC, c.confidence DESC, c.created_at DESC
                   LIMIT ?''', (entity_id, entity_id, per_entity)).fetchall()]
        cards.append(compress_entity(e, claims, max_claims=per_entity, claims_total=total))
    conn.close()

    out: dict = {'results': cards, 'count': len(cards)}
    if not_found:
        out['not_found'] = not_found
    if overflow:
        out['truncated'] = len(overflow)
        out['hint'] = (f'{len(overflow)} ids dropped by the batch limit ({max_ids}) - '
                       'request the rest in another call')
    return json.dumps(out, ensure_ascii=False)


def get_entity(entity_id: str) -> str:
    """Get one entity and its strongest claims.

    Claims are ranked by the program (verified first, then confidence); the
    total count is reported when more exist. Use get_entities to scan several.

    :param entity_id: Entity UUID.
    """
    max_claims = _limit('tool_entity_claims', 8)
    conn = connect()
    e = conn.execute('SELECT * FROM entities WHERE id=?', (entity_id,)).fetchone()
    if not e:
        conn.close()
        return json.dumps({'error': 'Entity not found'}, ensure_ascii=False)
    total = conn.execute('SELECT COUNT(*) FROM claims WHERE subject_id=? OR object_id=?',
                         (entity_id, entity_id)).fetchone()[0]
    claims = [dict(r) for r in conn.execute(
        '''SELECT c.id,c.predicate,c.content,c.object_text,c.confidence,c.status,
                  c.source_document_id,c.source_chunk_id,s.name subject_name,o.name object_name
           FROM claims c JOIN entities s ON s.id=c.subject_id
               LEFT JOIN entities o ON o.id=c.object_id
           WHERE c.subject_id=? OR c.object_id=?
           ORDER BY (c.status='verified') DESC, c.confidence DESC, c.created_at DESC
           LIMIT ?''', (entity_id, entity_id, max_claims)).fetchall()]
    conn.close()

    row = dict(e)
    row['aliases'] = loads(row.pop('aliases_json'), [])
    row['properties'] = loads(row.pop('properties_json'), {})
    card = compress_entity(row, claims, max_claims=max_claims, claims_total=total)
    return json.dumps(card, ensure_ascii=False)


def get_entity_graph(entity_id: str, depth: int = 1) -> str:
    """Get the graph neighborhood around an entity.

    Returns node names/types and edge predicates only - no descriptions. Node
    ids are included so single entities can be expanded with get_entity.

    :param entity_id: Entity UUID.
    :param depth: Graph traversal depth, usually 1 or 2.
    """
    result = neighborhood(entity_id, max(1, min(int(depth), 2)), 100)
    return json.dumps(compress_graph(result or {'error': 'Entity not found'}),
                      ensure_ascii=False)
