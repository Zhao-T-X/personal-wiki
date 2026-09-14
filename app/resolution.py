from __future__ import annotations

import re
import uuid
from difflib import SequenceMatcher
from .db import loads
from .ontology import normalize_name, canonical_entity_type
from .repositories import EntityRepository

# Legal suffixes carry no identity: "Apple Inc." and "Apple" are the same entity.
# normalize_name only folds whitespace and case, so without this "Apple Inc." vs
# "Apple Computer" scores ~0.67 and slips past a similarity threshold.
_COMPANY_SUFFIXES = {
    'inc', 'incorporated', 'ltd', 'limited', 'llc', 'corp', 'corporation',
    'co', 'gmbh', 'plc', 'sa', 'ag', 'bv', 'nv', 'pte', 'kk',
}


def _loose_name(value: str) -> str:
    """normalize_name + punctuation removal + legal-suffix stripping."""
    text = re.sub(r'[^\w\s]', ' ', normalize_name(value))
    words = [w for w in text.split() if w and w not in _COMPANY_SUFFIXES]
    # Never return empty: a name that is *only* a suffix is still a name.
    return ' '.join(words) or normalize_name(value)


def _same_type_score(name: str, candidate_name: str, requested_types: set[str], candidate_types: set[str]) -> float:
    if not requested_types.intersection(candidate_types):
        return 0.0
    a, b = normalize_name(name), normalize_name(candidate_name)
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def resolve_or_create_entity(conn, *, name: str, entity_types: list[str] | None = None,
                             entity_type: str | None = None, aliases: list[str],
                             description: str | None, properties: dict) -> str:
    entities = EntityRepository(conn)
    types = [canonical_entity_type(t) for t in (entity_types or ([entity_type] if entity_type else []))]
    if not types:
        raise ValueError('Entity requires at least one type')
    types = list(dict.fromkeys(types))
    normalized = normalize_name(name)
    existing_name = entities.by_name(name)
    if existing_name:
        entity_id = existing_name['id']
    else:
        entity_id = entities.by_alias(normalized)
        if not entity_id:
            best = None
            for cand in entities.resolution_candidates(500):
                cand_types = set(loads(cand['types_json'], []))
                score = _same_type_score(name, cand['name'], set(types), cand_types)
                if score >= 0.93 and (best is None or score > best[0]):
                    best = (score, cand['id'])
            if best:
                entity_id = best[1]
            else:
                entity_id = str(uuid.uuid4())
                entities.insert(entity_id, type=types[0], types=types, name=name,
                                aliases=[], description=description, properties=properties or {})
    existing = entities.fetch_types_aliases(entity_id)
    merged_types = list(dict.fromkeys(loads(existing['types_json'], []) + types))
    merged_aliases = list(dict.fromkeys(
        loads(existing['aliases_json'], []) + [a.strip() for a in aliases if a.strip()] + [name]))
    entities.merge(entity_id, types=merged_types, aliases=merged_aliases,
                   description=description, properties=properties or {})
    for alias in merged_aliases:
        entities.insert_alias(entity_id, alias, normalize_name(alias))
    return entity_id


def find_similar_entities(conn, entity_id: str, *, limit: int = 5, threshold: float = 0.65) -> list[dict]:
    """Near-duplicates that resolution deliberately left alone.

    ``resolve_or_create_entity`` auto-merges at >=0.93 similarity. Anything below
    that survives as a separate row, which is exactly the pair a human should
    judge — the goal here is to surface candidates, not to decide for them. That
    is why the threshold is loose and the caller shows a similarity percentage.
    """
    entities = EntityRepository(conn)
    row = entities.get_raw(entity_id)
    if not row:
        return []
    target = _loose_name(row['name'])
    if not target:
        return []
    target_types = set(loads(row['types_json'], [])) or ({row['type']} if row['type'] else set())

    out = []
    for cand in entities.similarity_pool(entity_id, 800):
        cand_types = set(loads(cand['types_json'], [])) or ({cand['type']} if cand['type'] else set())
        if target_types and cand_types and not target_types.intersection(cand_types):
            continue
        name = _loose_name(cand['name'])
        if not name:
            continue
        # Containment first: "apple" vs "apple computer" must not be missed just
        # because the longer name drags the ratio down.
        if target in name or name in target:
            score = 1.0
        else:
            score = SequenceMatcher(None, target, name).ratio()
        if score >= threshold:
            out.append({'id': cand['id'], 'name': cand['name'], 'type': cand['type'],
                        'status': cand['status'], 'similarity': round(score, 3)})
    out.sort(key=lambda x: x['similarity'], reverse=True)
    return out[:limit]
