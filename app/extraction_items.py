"""Item-level compilation of an Extraction Envelope (Step 11).

The old contract had one granularity: the document. ``normalize_extraction`` raised on
the first bad claim and the caller rolled the whole document back, so a single
uncompilable claim cost every *valid* claim that shared the envelope.

This module keeps the same single compiler and the same error messages, but reports a
result **per item** instead of throwing the envelope away:

    LLM draft  ->  compile every item  ->  ACCEPTED / REJECTED / NEEDS_REVIEW
                                        ->  accepted items make the envelope
                                        ->  persistence only ever sees accepted items

Two invariants make that safe rather than merely convenient:

1. **Only accepted claims contribute support.** ``supported`` names for Entity
   eligibility are read from ``outcome.envelope``, which by construction holds only
   compiled claims — so a rejected claim can never promote an Entity to KEEP.
2. **A rejection is never silence.** Every rejected item keeps its code, its stage, its
   original payload and its source location, and travels back to the caller.

Nothing here is fatal: the return value describes what happened. Genuine
infrastructure faults (a broken connection, a failed transaction) are raised by the
caller's own layer and are deliberately *not* modelled as item results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .extraction import (SCHEMA_INVALID, _legacy_to_v2, item_error, normalize_claim,
                         normalize_entity, schema_invalid_paths)
from .ontology import normalize_name

ACCEPTED = 'ACCEPTED'
REJECTED = 'REJECTED'
NEEDS_REVIEW = 'NEEDS_REVIEW'

COMPLETED = 'COMPLETED'
COMPLETED_WITH_WARNINGS = 'COMPLETED_WITH_WARNINGS'
FAILED = 'FAILED'

SECTIONS: tuple[str, ...] = ('entities', 'claims', 'events', 'ideas', 'questions')


@dataclass
class ExtractionItemResult:
    """What happened to ONE extracted item. Never just ``str(exception)``."""

    item_index: int
    item_type: str
    status: str
    input: Any = None
    compiled_output: Any = None
    error_code: str | None = None
    error_message: str | None = None
    source_location: dict | None = None
    attempts: int = 1
    # Every earlier attempt, so a repaired item shows both the error it had and the
    # one it was fixed from rather than only the last.
    prior_errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'item_index': self.item_index, 'item_type': self.item_type,
            'status': self.status, 'error_code': self.error_code,
            'error_message': self.error_message, 'source_location': self.source_location,
            'attempts': self.attempts,
            # The original payload is kept so a failure can be replayed offline.
            'input': self.input,
            'compiled_output': self.compiled_output,
        }

    def failure_view(self) -> dict:
        """The traceable shape stored in the run log (no compiled payload needed)."""
        return {
            'item_index': self.item_index, 'item_type': self.item_type,
            'status': self.status, 'error_code': self.error_code,
            'error_message': self.error_message, 'source_location': self.source_location,
            'attempts': self.attempts, 'input': self.input,
            'prior_errors': self.prior_errors,
        }


@dataclass
class CompilationOutcome:
    """The whole envelope's verdict: accepted work plus every refusal."""

    results: list[ExtractionItemResult] = field(default_factory=list)
    envelope: dict = field(default_factory=lambda: {s: [] for s in SECTIONS})
    raw_envelope: dict = field(default_factory=lambda: {s: [] for s in SECTIONS})

    @property
    def accepted(self) -> list[ExtractionItemResult]:
        return [r for r in self.results if r.status == ACCEPTED]

    @property
    def rejected(self) -> list[ExtractionItemResult]:
        return [r for r in self.results if r.status == REJECTED]

    @property
    def needs_review(self) -> list[ExtractionItemResult]:
        return [r for r in self.results if r.status == NEEDS_REVIEW]

    @property
    def failures(self) -> list[ExtractionItemResult]:
        return [r for r in self.results if r.status != ACCEPTED]

    @property
    def status(self) -> str:
        """COMPLETED / COMPLETED_WITH_WARNINGS / FAILED, from accepted knowledge.

        A rejected claim is *not* a failed document; a document that kept nothing at
        all is, so the caller cannot mistake "all items were bad" for success.
        """
        if self.accepted:
            return COMPLETED_WITH_WARNINGS if self.failures else COMPLETED
        return FAILED if self.rejected else COMPLETED

    def to_dict(self) -> dict:
        return {
            'status': self.status,
            'accepted': len(self.accepted), 'rejected': len(self.rejected),
            'needs_review': len(self.needs_review),
            'rejected_items': [r.failure_view() for r in self.failures],
        }


def _location(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    quote = item.get('evidence_quote')
    return {'source_chunk': item.get('source_chunk'),
            'evidence_quote': (quote or '')[:200] or None}


def _index_entity(index: dict[str, dict], entity: dict) -> None:
    index[normalize_name(entity['name'])] = entity
    for alias in entity.get('aliases', []):
        index.setdefault(normalize_name(alias), entity)


def _enforce_schema(kept: dict[str, list], *, rounds: int = 6) -> None:
    """Drop exactly the items the envelope schema rejects, and keep the rest.

    Compilation is per item, so a single malformed field (a missing event_type, an
    unknown question status) must cost that item — not the document. Each round
    demotes the offending items and re-validates until the envelope is clean.
    """
    for _ in range(rounds):
        envelope = {s: [canon for _, canon, _ in kept[s]] for s in SECTIONS}
        bad = schema_invalid_paths(envelope)
        if not bad:
            return
        removed = False
        for section, idx in bad:
            if section not in kept or idx >= len(kept[section]):
                # Not localisable to a single item: refuse the whole section rather
                # than pretend a broken envelope is a valid one.
                for s in SECTIONS:
                    while kept[s]:
                        _, _, result = kept[s].pop()
                        result.status = REJECTED
                        result.error_code = SCHEMA_INVALID
                        result.error_message = 'envelope failed schema validation'
                return
            _, _, result = kept[section].pop(idx)
            result.status = REJECTED
            result.error_code = SCHEMA_INVALID
            result.error_message = 'rejected by extraction schema validation'
            removed = True
        if not removed:
            return


def compile_extraction_items(data: dict[str, Any]) -> CompilationOutcome:
    """Compile every item of an envelope; refuse the bad ones individually.

    Returns an outcome whose ``envelope`` is the canonical, schema-valid, **accepted
    only** envelope — the shape persistence and Entity eligibility are allowed to read.
    ``raw_envelope`` is the same selection in the payload the model produced, which is
    what the extraction layer hands on so previously-working batches are unchanged.
    """
    try:
        raw = _legacy_to_v2(data)
    except Exception as exc:  # noqa: BLE001 — an unusable envelope is one result, not a crash
        err = item_error(exc, item_type='envelope')
        return CompilationOutcome(results=[ExtractionItemResult(
            0, 'envelope', REJECTED, input=data, error_code=err.code,
            error_message=str(err))])

    results: list[ExtractionItemResult] = []
    kept: dict[str, list[tuple[dict, dict, ExtractionItemResult]]] = {s: [] for s in SECTIONS}
    index: dict[str, dict] = {}

    for i, entity in enumerate(raw['entities']):
        result = ExtractionItemResult(i, 'entity', REJECTED, input=entity)
        try:
            canonical = normalize_entity(dict(entity))
        except Exception as exc:  # noqa: BLE001
            err = item_error(exc, item_type='entity')
            result.error_code, result.error_message = err.code, str(err)
        else:
            result.status, result.compiled_output = ACCEPTED, canonical
            kept['entities'].append((entity, canonical, result))
            _index_entity(index, canonical)
        results.append(result)

    for i, claim in enumerate(raw['claims']):
        result = ExtractionItemResult(i, 'claim', REJECTED, input=claim,
                                      source_location=_location(claim))
        try:
            canonical = normalize_claim(dict(claim), index)
        except Exception as exc:  # noqa: BLE001
            err = item_error(exc)
            result.error_code, result.error_message = err.code, str(err)
        else:
            result.status, result.compiled_output = ACCEPTED, canonical
            kept['claims'].append((claim, canonical, result))
        results.append(result)

    # Events / ideas / questions carry no ontology semantics to compile; they are
    # accepted provisionally and the schema pass below demotes the malformed ones.
    for section in ('events', 'ideas', 'questions'):
        for i, item in enumerate(raw[section]):
            result = ExtractionItemResult(i, section[:-1], ACCEPTED, input=item,
                                          compiled_output=item, source_location=_location(item))
            kept[section].append((item, item, result))
            results.append(result)

    _enforce_schema(kept)
    return CompilationOutcome(
        results=results,
        envelope={s: [canonical for _, canonical, _ in kept[s]] for s in SECTIONS},
        raw_envelope={s: [original for original, _, _ in kept[s]] for s in SECTIONS})
