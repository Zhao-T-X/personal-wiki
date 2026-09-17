from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from jsonschema import Draft202012Validator
from .domain.compiler import UNRESOLVED, CompileResult, KnowledgeCompiler
from .ontology import (canonical_entity_type, claim_predicate_spec,
                       match_claim_predicates, normalize_name, normalize_predicate,
                       relation_spec)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'schemas' / 'extraction.schema.json'
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding='utf-8'))
VALIDATOR = Draft202012Validator(SCHEMA)

# Canonical item-level error codes. They name *what kind of thing went wrong*, so a
# failure can be isolated, reported and retried per item instead of aborting a whole
# document (Step 11). The message stays the human-readable one the repair loop uses.
ENTITY_INVALID = 'ENTITY_INVALID'
SCHEMA_INVALID = 'SCHEMA_INVALID'
EVIDENCE_INVALID = 'EVIDENCE_INVALID'
UNSUPPORTED_PREDICATE = 'UNSUPPORTED_PREDICATE'
DOMAIN_RANGE_INVALID = 'DOMAIN_RANGE_INVALID'
CLAIM_INVALID = 'CLAIM_INVALID'


class ExtractionItemError(ValueError):
    """A rejection of ONE item, carrying its code and the item's kind.

    It *is* a ``ValueError``: every existing caller that only catches ``ValueError``
    (the repair loop, the strict normaliser, the API error mapping) keeps working
    unchanged. The code is the extra fact that lets a caller isolate the item instead
    of the document.
    """

    def __init__(self, message: str, *, code: str, item_type: str = 'claim'):
        super().__init__(message)
        self.code = code
        self.item_type = item_type


def item_error(exc: Exception, *, item_type: str = 'claim') -> ExtractionItemError:
    """Fold any exception into a coded item error, without inventing a code."""
    if isinstance(exc, ExtractionItemError):
        return exc
    return ExtractionItemError(str(exc), code=CLAIM_INVALID if item_type == 'claim'
                               else ENTITY_INVALID, item_type=item_type)


def validate_extraction(data: dict[str, Any]) -> None:
    errors = sorted(VALIDATOR.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        detail = '; '.join(f'{list(e.path)}: {e.message}' for e in errors[:8])
        raise ExtractionItemError(
            f'Extraction JSON failed schema validation: {detail}',
            code=SCHEMA_INVALID, item_type='envelope')


def schema_invalid_paths(data: dict[str, Any]) -> list[tuple[str, int]]:
    """``(section, index)`` of every item the schema would reject.

    Used by the partial-compilation path to drop exactly the offending items and
    keep the rest, instead of treating one malformed item as a malformed document.
    """
    out: list[tuple[str, int]] = []
    for err in VALIDATOR.iter_errors(data):
        path = list(err.path)
        if len(path) >= 2 and isinstance(path[1], int) and path[0] in (
                'entities', 'claims', 'events', 'ideas', 'questions'):
            out.append((path[0], path[1]))
        elif path and path[0] in ('entities', 'claims', 'events', 'ideas', 'questions'):
            out.append((path[0], 0))
        else:
            out.append(('<envelope>', 0))
    return out


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
        # A missing evidence_quote is a rejection of THIS claim, not of the document:
        # it is raised by ``normalize_claim`` so the partial path can isolate it.
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


def _unsupported_predicate(claim: dict[str, Any], result: CompileResult) -> ExtractionItemError:
    """The repair-loop contract for a predicate that maps to nothing.

    Registry subset (spec §5): the repair LLM is offered the closest registered
    predicates, never the full registry, and never gets to keep the invented one.
    """
    candidate = claim.get('predicate')
    subset = list(result.resolution.candidates) or match_claim_predicates(candidate, limit=4)
    hint = (f' Closest registered predicates: {", ".join(subset)} - use one of these.'
            if subset else ' Use a predicate from the schema enum.')
    return ExtractionItemError(f'Unsupported claim predicate: {normalize_predicate(candidate)}.{hint}',
                               code=UNSUPPORTED_PREDICATE)


def _domain_range_rejection(claim: dict[str, Any], predicate: str) -> ExtractionItemError:
    """The predicate is registered but the pairing is illegal (e.g. a CEO who is a place).

    Diagnostics only (Step 12 §9): the *validation* is unchanged, but the message now
    reports the declaration that actually rejected the pairing. A predicate registered
    as both a claim predicate and a relation predicate (``creates``) is checked against
    the relation's ``source_types``/``target_types``, so printing the claim spec's empty
    ``domain``/``range`` used to be actively misleading.
    """
    spec = claim_predicate_spec(predicate)
    relation = relation_spec(predicate)
    if relation:
        declared = (f'source_types={", ".join(relation.get("source_types") or []) or "*"}, '
                    f'target_types={", ".join(relation.get("target_types") or []) or "*"}')
    elif spec:
        declared = f'domain={", ".join(spec.domain) or "*"}, range={", ".join(spec.range) or "*"}'
    else:
        declared = 'no declaration'
    return ExtractionItemError(
        f'Claim violates ontology domain/range: {claim["subject"]} {predicate} '
        f'{claim.get("object") or "(none)"}. `{predicate}` declares {declared}.',
        code=DOMAIN_RANGE_INVALID)


def _compile_claim(claim: dict[str, Any], index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One claim through the single compiler, translated into the repair contract.

    What a claim *means* is not decided here. A refusal comes back as a coded
    ``ExtractionItemError`` — still a ``ValueError``, so the repair loop and the strict
    normaliser are unchanged, but now carrying the code the partial path isolates on.
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
    raise ExtractionItemError(result.reasons[0] if result.reasons else 'Claim rejected by the compiler',
                              code=CLAIM_INVALID)


# ---------------------------------------------------------------------------------
# Per-item normalisers — the single implementation the whole envelope and the
# partial-commit path share. Each one refuses exactly one item and says why.
# ---------------------------------------------------------------------------------

def normalize_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """Canonicalise one entity; refuse it (and only it) when it has no identity."""
    entity['name'] = ' '.join(str(entity.get('name') or '').split())
    entity['types'] = list(dict.fromkeys(canonical_entity_type(t) for t in entity.get('types') or []))
    entity['aliases'] = list(dict.fromkeys(' '.join(a.split()) for a in entity.get('aliases', []) if a.strip()))
    if not entity['name'] or not entity['types']:
        raise ExtractionItemError('Entity requires name and at least one type',
                                  code=ENTITY_INVALID, item_type='entity')
    return entity


def normalize_claim(claim: dict[str, Any], index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Canonicalise one claim; refuse it (and only it) when it does not compile."""
    if not claim.get('evidence_quote'):
        raise ExtractionItemError('v2.0 requires evidence_quote for every Claim',
                                  code=EVIDENCE_INVALID)
    claim['subject'] = ' '.join(str(claim.get('subject') or '').split())
    # The canonical claim is written back onto the envelope entry: the loop owns
    # the envelope, the compiler owns every semantic field in it.
    claim.update(_compile_claim(claim, index))
    return claim


def normalize_entity_list(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Canonicalise every entity, refusing the offending one only."""
    out = []
    for e in entities:
        out.append(normalize_entity(e))
    return out


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
        normalize_entity(e)
    index = _entity_index(out['entities'])
    for c in out['claims']:
        normalize_claim(c, index)
    validate_extraction(out)
    return out


def is_item_recoverable(code: str) -> bool:
    """Is this error code an *item* problem (isolate it) or a *document* problem?

    Item-level: the payload itself is bad (schema, ontology, domain/range, evidence.
    A handful of such items must never cost the accepted ones their persistence.

    Everything else — a database failure, a broken transaction, an invariant that
    cannot hold — is fatal: the caller must let it roll back rather than half-write.
    """
    return code in {ENTITY_INVALID, SCHEMA_INVALID, EVIDENCE_INVALID,
                    UNSUPPORTED_PREDICATE, DOMAIN_RANGE_INVALID, CLAIM_INVALID}
