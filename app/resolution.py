from __future__ import annotations

import uuid
from difflib import SequenceMatcher
from .db import dumps, loads
from .ontology import normalize_name, canonical_entity_type


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
