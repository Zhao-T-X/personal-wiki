from __future__ import annotations

from .domain.claim_state import resolve
from .repositories import ClaimRepository, EntityRepository, RelationRepository


def _node(entity: dict) -> dict:
    return {'id': entity['id'], 'type': entity['type'], 'name': entity['name'],
            'description': entity['description'], 'status': entity['status']}


def neighborhood(entity_id: str, depth: int = 1, limit: int = 100):
    entities = EntityRepository()
    root = entities.get_raw(entity_id)
    if not root:
        return None
    nodes: dict = {root['id']: _node(root)}
    edges: list = []
    frontier = {entity_id}
    relations = RelationRepository()
    for _ in range(max(1, min(depth, 3))):
        if not frontier:
            break
        rows = relations.incident(list(frontier), limit)
        nxt = set()
        for r in rows:
            edges.append(dict(r))
            for nid in (r['source_id'], r['target_id']):
                if nid not in nodes:
                    related = entities.get_raw(nid)
                    if related:
                        nodes[nid] = _node(related)
                        nxt.add(nid)
        frontier = nxt
    # Claim edges, resolved so a superseded claim is not shown as current.
    for claim in resolve(ClaimRepository().edges_for_entity(entity_id, limit)):
        edges.append(dict(claim) | {'kind': 'claim'})
    return {'nodes': list(nodes.values()), 'edges': edges}
