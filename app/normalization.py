from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ontology import (
    MODALITIES,
    POLARITIES,
    CLAIM_PREDICATES,
    relation_spec,
    relation_types_allowed,
    normalize_name,
    normalize_predicate,
)

@dataclass(frozen=True)
class RelationCandidate:
    source: str
    predicate: str
    target: str
    context: dict[str, Any]
    confidence: float
    source_chunk: str
    evidence_quote: str
    outcome: str
    reason: str


def _normalized_modality(value: Any) -> str:
    return value if value in MODALITIES else 'asserted'


def _normalized_polarity(value: Any) -> str:
    return value if value in POLARITIES else 'positive'


def _entity_index(entities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for e in entities:
        types = e.get('types', [])
        index[normalize_name(e['name'])] = e
        for alias in e.get('aliases', []):
            index.setdefault(normalize_name(alias), e)
    return index


def _has_material_condition(claim: dict[str, Any]) -> bool:
    ctx = claim.get('context') or {}
    if not ctx:
        return False
    material = {'condition', 'benchmark', 'metric', 'scope', 'time', 'valid_from', 'valid_to', 'population', 'experimental_setup'}
    return any(k in ctx and ctx[k] not in (None, '', [], {}) for k in material)


def normalize_claim(claim: dict[str, Any]) -> dict[str, Any]:
    out = dict(claim)
    out['predicate'] = normalize_predicate(out['predicate'])
    out['polarity'] = _normalized_polarity(out.get('polarity'))
    out['modality'] = _normalized_modality(out.get('modality'))
    out['context'] = out.get('context') if isinstance(out.get('context'), dict) else {}
    return out


def claim_to_relation_candidates(extraction: dict[str, Any]) -> list[RelationCandidate]:
    index = _entity_index(extraction.get('entities', []))
    candidates: list[RelationCandidate] = []
    always_claim_only = {
        'retrieves','provides','receives','generates','produces','extracts','supports','enables',
        'causes','leads_to','improves','reduces','increases','decreases','prevents','addresses',
        'better_than','worse_than','similar_to','different_from'
    }
    for raw in extraction.get('claims', []):
        claim = normalize_claim(raw)
        pred = claim['predicate']
        subject = index.get(normalize_name(claim['subject']))
        obj_raw = claim.get('object')
        target = index.get(normalize_name(obj_raw)) if obj_raw else None
        if not subject or not target:
            outcome = 'REJECTED' if not subject else 'CLAIM_ONLY'
            candidates.append(RelationCandidate(claim['subject'], pred, obj_raw or '', claim.get('context', {}), claim.get('confidence', 0.0), claim['source_chunk'], claim['evidence_quote'], outcome, 'subject_or_object_not_resolved'))
            continue
        if pred not in CLAIM_PREDICATES:
            candidates.append(RelationCandidate(claim['subject'], pred, obj_raw, claim.get('context', {}), claim.get('confidence', 0.0), claim['source_chunk'], claim['evidence_quote'], 'REJECTED', 'claim_predicate_not_registered'))
            continue
        if pred in always_claim_only:
            candidates.append(RelationCandidate(claim['subject'], pred, obj_raw, claim.get('context', {}), claim.get('confidence', 0.0), claim['source_chunk'], claim['evidence_quote'], 'CLAIM_ONLY', 'predicate_is_claim_only'))
            continue
        spec = relation_spec(pred)
        if not spec:
            candidates.append(RelationCandidate(claim['subject'], pred, obj_raw, claim.get('context', {}), claim.get('confidence', 0.0), claim['source_chunk'], claim['evidence_quote'], 'CLAIM_ONLY', 'no_relation_predicate_mapping'))
            continue
        if claim['polarity'] != 'positive' or claim['modality'] != 'asserted':
            candidates.append(RelationCandidate(claim['subject'], pred, obj_raw, claim.get('context', {}), claim.get('confidence', 0.0), claim['source_chunk'], claim['evidence_quote'], 'CONDITIONAL_RELATION', 'non_asserted_or_negative_claim'))
            continue
        if _has_material_condition(claim):
            candidates.append(RelationCandidate(claim['subject'], pred, obj_raw, claim.get('context', {}), claim.get('confidence', 0.0), claim['source_chunk'], claim['evidence_quote'], 'CONDITIONAL_RELATION', 'material_context_requires_claim_only'))
            continue
        if not relation_types_allowed(subject.get('types', []), pred, target.get('types', [])):
            candidates.append(RelationCandidate(claim['subject'], pred, obj_raw, claim.get('context', {}), claim.get('confidence', 0.0), claim['source_chunk'], claim['evidence_quote'], 'CLAIM_ONLY', 'source_target_types_not_allowed'))
            continue
        candidates.append(RelationCandidate(claim['subject'], pred, obj_raw, claim.get('context', {}), claim.get('confidence', 0.0), claim['source_chunk'], claim['evidence_quote'], 'DIRECT_RELATION', 'all_direct_relation_requirements_satisfied'))
    return candidates


def derive_relations(extraction: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            'source': c.source,
            'predicate': c.predicate,
            'target': c.target,
            'context': c.context,
            'confidence': c.confidence,
            'source_chunk': c.source_chunk,
            'evidence_quote': c.evidence_quote,
        }
        for c in claim_to_relation_candidates(extraction)
        if c.outcome == 'DIRECT_RELATION'
    ]
