from __future__ import annotations

import re
import uuid
from difflib import SequenceMatcher
from .db import dumps, loads
from .ontology import normalize_name, canonical_entity_type

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


def resolve_or_create_entity(conn, *, name: str, entity_types: list[str] | None = None, entity_type: str | None = None, aliases: list[str], description: str | None, properties: dict) -> str:
    types = [canonical_entity_type(t) for t in (entity_types or ([entity_type] if entity_type else []))]
    if not types:
        raise ValueError('Entity requires at least one type')
    types = list(dict.fromkeys(types))
    normalized = normalize_name(name)
    row = conn.execute('SELECT id,types_json FROM entities WHERE lower(name)=lower(?)', (name,)).fetchone()
    if row:
        entity_id = row['id']
    else:
        alias_row = conn.execute('SELECT entity_id FROM entity_aliases WHERE alias_normalized=? LIMIT 1', (normalized,)).fetchone()
        if alias_row:
            entity_id = alias_row['entity_id']
        else:
            best = None
            for cand in conn.execute('SELECT id,types_json,name FROM entities ORDER BY updated_at DESC LIMIT 500'):
                cand_types = set(loads(cand['types_json'], [])) or ({cand['type']} if 'type' in cand.keys() else set())
                score = _same_type_score(name, cand['name'], set(types), cand_types)
                if score >= 0.93 and (best is None or score > best[0]):
                    best = (score, cand['id'])
            if best:
                entity_id = best[1]
            else:
                entity_id = str(uuid.uuid4())
                conn.execute('INSERT INTO entities(id,type,types_json,name,aliases_json,description,properties_json,status) VALUES(?,?,?,?,?,?,?,?)',
                             (entity_id, types[0], dumps(types), name, dumps([]), description, dumps(properties or {}), 'candidate'))
    existing = conn.execute('SELECT types_json,aliases_json,description,properties_json FROM entities WHERE id=?', (entity_id,)).fetchone()
    merged_types = list(dict.fromkeys(loads(existing['types_json'], []) + types))
    merged_aliases = list(dict.fromkeys(loads(existing['aliases_json'], []) + [a.strip() for a in aliases if a.strip()] + [name]))
    conn.execute('UPDATE entities SET type=?, types_json=?, aliases_json=?, description=COALESCE(?,description), properties_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                 (merged_types[0], dumps(merged_types), dumps(merged_aliases), description, dumps(properties or {}), entity_id))
    for alias in merged_aliases:
        conn.execute('INSERT OR IGNORE INTO entity_aliases(entity_id,alias,alias_normalized) VALUES(?,?,?)', (entity_id, alias, normalize_name(alias)))
    return entity_id


def find_similar_entities(conn, entity_id: str, *, limit: int = 5, threshold: float = 0.65) -> list[dict]:
    """Near-duplicates that resolution deliberately left alone.

    ``resolve_or_create_entity`` auto-merges at >=0.93 similarity. Anything below
    that survives as a separate row, which is exactly the pair a human should
    judge — the goal here is to surface candidates, not to decide for them. That
    is why the threshold is loose and the caller shows a similarity percentage.
    """
    row = conn.execute('SELECT id,name,type,types_json FROM entities WHERE id=?', (entity_id,)).fetchone()
    if not row:
        return []
    target = _loose_name(row['name'])
    if not target:
        return []
    target_types = set(loads(row['types_json'], [])) or ({row['type']} if row['type'] else set())

    out = []
    for cand in conn.execute(
            'SELECT id,name,type,types_json,status FROM entities WHERE id != ? ORDER BY updated_at DESC LIMIT 800',
            (entity_id,)):
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
