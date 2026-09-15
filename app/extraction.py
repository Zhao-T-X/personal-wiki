from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator
from .domain.compiler import UNRESOLVED, CompileResult, KnowledgeCompiler
from .ontology import (canonical_entity_type, claim_predicate_spec,
                       match_claim_predicates, normalize_name, normalize_predicate)

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


def _entity_index(entities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Normalized name / alias -> entity record, for the compiler's type gate.

    Aliases are indexed too: a claim may name its subject through an alias the
    entity declared, and the pairing must still be *checked* rather than silently
    pass as "type unknown".
    """
    index: dict[str, dict[str, Any]] = {}
    for e in entities:
        index[normalize_name(e['name'])] = e
        for alias in e.get('aliases', []):
            index.setdefault(normalize_name(alias), e)
    return index


def _resolution_text(claim: dict[str, Any]) -> str:
    """Source text the resolver may read for *suggestions* — never for a verdict."""
    return ' '.join(x for x in (claim.get('content'), claim.get('evidence_quote')) if x)


def _unsupported_predicate(claim: dict[str, Any], result: CompileResult) -> ValueError:
    """The repair-loop contract for a predicate that maps to nothing.

    Registry subset (spec §5): the repair LLM is offered the closest registered
    predicates, never the full registry, and never gets to keep the invented one.
    """
    candidate = claim.get('predicate')
    subset = list(result.resolution.candidates) or match_claim_predicates(candidate, limit=4)
    hint = (f' Closest registered predicates: {", ".join(subset)} - use one of these.'
            if subset else ' Use a predicate from the schema enum.')
    return ValueError(f'Unsupported claim predicate: {normalize_predicate(candidate)}.{hint}')


def _domain_range_rejection(claim: dict[str, Any], predicate: str) -> ValueError:
    """The predicate is registered but the pairing is illegal (e.g. a CEO who is a place)."""
    spec = claim_predicate_spec(predicate)
    declared = (f'domain={", ".join(spec.domain) or "*"}, range={", ".join(spec.range) or "*"}'
                if spec else 'no declaration')
    return ValueError(
        f'Claim violates ontology domain/range: {claim["subject"]} {predicate} '
        f'{claim.get("object") or "(none)"}. `{predicate}` declares {declared}.')


def _compile_claim(claim: dict[str, Any], index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One claim through the single compiler, translated into the repair contract.

    What a claim *means* is not decided here. A refusal comes back as the
    ``ValueError`` the caller's repair loop already knows how to act on.
    """
    result = KnowledgeCompiler().compile_extraction_claim(
        claim, entities=index, resolution_text=_resolution_text(claim))
    if result.ok and result.claim is not None:
        return result.claim.to_dict()
    if result.status == UNRESOLVED:
        raise _unsupported_predicate(claim, result)
    if 'domain_range_violation' in result.reasons:
        raise _domain_range_rejection(claim, result.resolution.predicate or '')
    # claim_type / polarity / modality are canonicalised in the compiler; an unknown
    # value is refused with the very message it produced.
    raise ValueError(result.reasons[0] if result.reasons else 'Claim rejected by the compiler')


def normalize_extraction(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and canonicalise a whole Extraction Envelope.

    This function owns three things and delegates the fourth:

    * the **envelope** — entities, claims, events, ideas, questions;
    * the **errors** — aggregated into one actionable ``ValueError`` per failure;
    * the **repair-loop contract** — the message names the offending value *and*
      the registry candidates worth trying instead, so a repair call fixes the
      real violation rather than guessing.

    What it does **not** own is claim semantics. Predicate resolution, the temporal
    signal, claim_type / polarity / modality canonicalisation and the domain/range
    gate all belong to ``KnowledgeCompiler.compile_extraction_claim`` — the single
    implementation, which Correction, Research and every future producer reach
    through the same ingress (ADR-011). Keeping a second copy here is what let the
    extraction path skip the domain/range gate in the first place.
    """
    out = _legacy_to_v2(data)
    for e in out['entities']:
        e['name'] = ' '.join(e['name'].split())
        e['types'] = list(dict.fromkeys(canonical_entity_type(t) for t in e['types']))
        e['aliases'] = list(dict.fromkeys(' '.join(a.split()) for a in e.get('aliases', []) if a.strip()))
        if not e['name'] or not e['types']:
            raise ValueError('Entity requires name and at least one type')
    index = _entity_index(out['entities'])
    for c in out['claims']:
        c['subject'] = ' '.join(c['subject'].split())
        # The canonical claim is written back onto the envelope entry: the loop owns
        # the envelope, the compiler owns every semantic field in it.
        c.update(_compile_claim(c, index))
    validate_extraction(out)
    return out
