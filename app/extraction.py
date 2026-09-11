from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator
from .ontology import (
    ENTITY_TYPES, CLAIM_PREDICATES, match_claim_predicates,
    EVENT_TYPES, EVENT_STATUSES, QUESTION_TYPES, QUESTION_STATUSES,
    IDEA_STATUSES, normalize_name, normalize_predicate, canonical_entity_type,
    canonical_claim_type, canonical_polarity, canonical_modality,
)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'schemas' / 'extraction.schema.json'
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
VALIDATOR = Draft202012Validator(SCHEMA)


def validate_extraction(data: dict[str, Any]) -> None:
    errors = sorted(VALIDATOR.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        detail = '; '.join(f'{list(e.path)}: {e.message}' for e in errors[:8])
        raise ValueError(f'Extraction JSON failed schema validation: {detail}')


def _legacy_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(data or {}, ensure_ascii=False))
    # Loss-minimizing compatibility adapter. It never invents semantics.
    for key in ('entities','claims','events','ideas','questions'):
        if not isinstance(out.get(key), list):
            out[key] = []
    for e in out['entities']:
        if not isinstance(e, dict):
            continue
        e.pop('id', None)
        if 'types' not in e and 'type' in e:
            e['types'] = [canonical_entity_type(e.pop('type'))]
        e.setdefault('aliases', [])
        e.setdefault('description', None)
        e.setdefault('properties', {})
    for c in out['claims']:
        if not isinstance(c, dict):
            continue
        c.pop('id', None)
        c.setdefault('object', None)
        c.setdefault('claim_type', 'factual')
        c.setdefault('polarity', 'positive')
        c.setdefault('modality', 'asserted')
        c.setdefault('context', {})
        if c.get('confidence') == 'unstated': c['confidence'] = 0.5
        if isinstance(c.get('context'), str): c['context'] = {'note': c['context']}
        if not c.get('evidence_quote'):
            raise ValueError('v2.0 requires evidence_quote for every Claim')
    # Legacy relations are validated as an input convenience, but they are not part of LLM v2.0 output.
    out.pop('relations', None)
    for e in out['events']:
        if not isinstance(e, dict): continue
        e.setdefault('participants', [])
        e.setdefault('location', None)
        e.setdefault('time', {'start': None, 'end': None, 'precision': 'unknown'})
        e.setdefault('status', 'unknown')
        e.setdefault('confidence', 0.5)
    for i in out['ideas']:
        if not isinstance(i, dict): continue
        i.setdefault('status', 'candidate'); i.setdefault('confidence', 0.5)
    for q in out['questions']:
        if not isinstance(q, dict): continue
        q.setdefault('question_type', 'knowledge'); q.setdefault('status', 'open'); q.setdefault('confidence', 0.5)
    return out


def normalize_extraction(data: dict[str, Any]) -> dict[str, Any]:
    out = _legacy_to_v2(data)
    for e in out['entities']:
        e['name'] = ' '.join(e['name'].split())
        e['types'] = list(dict.fromkeys(canonical_entity_type(t) for t in e['types']))
        e['aliases'] = list(dict.fromkeys(' '.join(a.split()) for a in e.get('aliases', []) if a.strip()))
        if not e['name'] or not e['types']:
            raise ValueError('Entity requires name and at least one type')
    for c in out['claims']:
        c['subject'] = ' '.join(c['subject'].split())
        c['predicate'] = normalize_predicate(c['predicate'])
        if c['predicate'] not in CLAIM_PREDICATES:
            # Registry subset (spec §5): the repair LLM sees the 2-5 closest
            # registered predicates, never the full registry.
            subset = match_claim_predicates(c['predicate'], limit=4)
            hint = (f' Closest registered predicates: {", ".join(subset)} - use one of these.'
                    if subset else ' Use a predicate from the schema enum.')
            raise ValueError(f'Unsupported claim predicate: {c["predicate"]}.{hint}')
        c['claim_type'] = canonical_claim_type(c.get('claim_type'))
        c['polarity'] = canonical_polarity(c.get('polarity'))
        c['modality'] = canonical_modality(c.get('modality'))
    validate_extraction(out)
    return out
