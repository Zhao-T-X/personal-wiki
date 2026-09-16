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


def name_similarity(a: str, b: str) -> float:
    """How much two entity names *look* like the same thing (0-1) — for review only.

    Deliberately looser than the score ``resolve_or_create_entity`` merges on by
    itself (0.93, computed on ``normalize_name``). This one exists to show a human a
    candidate pair, so it strips legal suffixes and accepts containment: 苹果 and
    苹果公司 must be *offered*, even though they would never be auto-merged.

    Both the per-entity endpoint and the workspace-wide scan call this one function,
    so the pair a scan surfaces cannot drift from the pair a detail page shows.
    """
    x, y = _loose_name(a), _loose_name(b)
    if not x or not y:
        return 0.0
    # Containment first: "apple" vs "apple computer" must not be missed just because
    # the longer name drags the ratio down.
    if x in y or y in x:
        return 1.0
    return SequenceMatcher(None, x, y).ratio()


def types_compatible(a: set[str], b: set[str]) -> bool:
    """Whether two entities could be the same thing at all.

    An empty type set counts as compatible: "unknown type" is not evidence against,
    and treating it as a mismatch would hide exactly the pairs worth reviewing.
    """
    return not (a and b) or bool(set(a) & set(b))


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


def find_entity_id(conn, *, name: str) -> str | None:
    """Read-only resolution: which entity does this name refer to (or ``None``)?

    The same lookup order as :func:`resolve_or_create_entity` — exact name first,
    then the alias table — minus the creation. That matters for planners, which
    must be able to answer "where would this land?" *without* writing anything.

    Routing candidate retrieval through this (instead of a bare name lookup) is
    what makes "苹果" and "苹果公司" one subject *before* the search: otherwise a
    correction phrased with an alias finds no existing claim at all and is filed
    as brand-new knowledge, which is precisely the corruption the alias table
    exists to prevent.
    """
    needle = str(name or '').strip()
    if not needle:
        return None
    entities = EntityRepository(conn)
    existing = entities.by_name(needle)
    if existing:
        return existing['id']
    return entities.by_alias(normalize_name(needle))


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
    target_types = set(loads(row['types_json'], [])) or ({row['type']} if row['type'] else set())

    out = []
    for cand in entities.similarity_pool(entity_id, 800):
        cand_types = set(loads(cand['types_json'], [])) or ({cand['type']} if cand['type'] else set())
        if not types_compatible(target_types, cand_types):
            continue
        score = name_similarity(row['name'], cand['name'])
        if score >= threshold:
            out.append({'id': cand['id'], 'name': cand['name'], 'type': cand['type'],
                        'status': cand['status'], 'similarity': round(score, 3)})
    out.sort(key=lambda x: x['similarity'], reverse=True)
    return out[:limit]


def scan_duplicate_entities(conn, *, threshold: float = 0.65, entity_limit: int = 500,
                            pair_limit: int = 20) -> list[dict]:
    """Near-duplicate pairs across the whole workspace, in one pass.

    ``find_similar_entities`` answers "what looks like *this* entity?" and costs a
    query per call; a workspace scan cannot become one of those per entity. So the
    pool is loaded once and compared in memory — with the same scoring rules, so a
    pair found here is the pair the detail page would have shown.

    Bounded by ``entity_limit`` because the comparison is quadratic: a personal wiki
    holds hundreds of entities, and this is an explicit action rather than a hot path.
    Archived entities are skipped — a row kept as a merge tombstone would otherwise
    keep proposing the merge that already happened.
    """
    pool = EntityRepository(conn).similarity_pool(None, entity_limit)
    # Duplicate Detection never judges whether something deserves to be an Entity: it
    # only compares entities that already passed Entity Eligibility. Unsupported
    # (``review``) entities are excluded, so the detector's input stays clean.
    rows_ = [(r['id'], r['name'], set(loads(r['types_json'], [])) or {r['type']})
             for r in pool if r.get('status') != 'archived'
             and loads(r.get('properties_json') or '{}', {}).get('eligibility') != 'review']

    pairs: list[dict] = []
    for i, (a_id, a_name, a_types) in enumerate(rows_):
        for b_id, b_name, b_types in rows_[i + 1:]:
            if not types_compatible(a_types, b_types):
                continue
            score = name_similarity(a_name, b_name)
            if score >= threshold:
                pairs.append({'entity_a': {'id': a_id, 'name': a_name},
                              'entity_b': {'id': b_id, 'name': b_name},
                              'similarity': round(score, 3)})
    pairs.sort(key=lambda p: p['similarity'], reverse=True)
    return pairs[:pair_limit]
