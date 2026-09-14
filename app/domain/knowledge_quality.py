"""KnowledgeQualityScore — the quality-engineering layer v0.2 asked for.

Not another LLM opinion. Seven deterministic dimensions, each 0..1, combined
into a single ``overall`` score (and a human ``grade``). The scorer is pure:
it takes a :class:`ClaimQualityInput` and returns a :class:`ClaimQuality`.
No IO, no model, so it is trivially testable and cannot drift from the data.

The seven dimensions map 1:1 to the brief:

* ``schema``           — Extraction v2 required fields are all present/valid
* ``evidence``         — the claim is grounded by a quote + source chunk
* ``quote``            — the evidence quote actually localizes in the chunk
* ``entity_resolution``— subject/object resolve to entity records (with descriptions)
* ``predicate``        — the predicate is in the registered ontology
* ``conflict``         — the claim is not in an unresolved contradiction
* ``provenance``       — it traces to a document + chunk + quote

A DB-backed assembler (:func:`score_claim_by_id`) builds the input from the
repositories so the API and the Review queue can score real claims.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from .claim_state import SUPERSEDED, is_current

# Extraction v2 schema: the fields a claim must carry. ``object`` is optional
# (it may be free text), ``content`` is optional prose, so they are not scored.
_REQUIRED = ('subject_name', 'predicate', 'claim_type', 'polarity', 'modality',
             'context', 'confidence', 'source_document_id', 'source_quote')

DIMENSIONS = ('schema', 'evidence', 'quote', 'entity_resolution', 'predicate',
              'conflict', 'provenance')

# Weights sum to 1.0. Evidence + quote + predicate carry the most signal
# because they are the cheapest to get wrong (hallucinated quotes, bad predicates).
WEIGHTS: dict[str, float] = {
    'schema': 0.15,
    'evidence': 0.20,
    'quote': 0.15,
    'entity_resolution': 0.15,
    'predicate': 0.15,
    'conflict': 0.10,
    'provenance': 0.10,
}

# A conflict relation to a *current* claim is an open contradiction worth human review.
_LIVE_CONFLICT_RELATIONSHIPS = ('contradicts', 'duplicate')


@dataclass
class ClaimQualityInput:
    """Everything needed to score one claim. Pure data — build it from DB or tests."""
    claim_id: str
    subject_name: str
    predicate: str
    claim_type: str = 'factual'
    polarity: str = 'positive'
    modality: str = 'asserted'
    context: dict = field(default_factory=dict)
    confidence: float | None = None
    object_name: str | None = None          # resolved entity (claim.object_id set)
    object_text: str | None = None          # free-text object (claim.object_id NULL)
    source_document_id: str | None = None
    source_chunk_id: str | None = None
    source_quote: str | None = None
    source_chunk_content: str | None = None  # the chunk text, to verify the quote
    subject_entity: dict | None = None       # entity row (may carry 'description')
    object_entity: dict | None = None
    predicate_registered: bool = True
    is_superseded: bool = False
    conflict_live: bool = False               # contradicts/duplicate a current claim


@dataclass
class ClaimQuality:
    claim_id: str
    dimensions: dict[str, float]
    overall: float
    grade: str
    flags: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            'claim_id': self.claim_id,
            'dimensions': self.dimensions,
            'overall': round(self.overall, 4),
            'grade': self.grade,
            'flags': self.flags,
        }


# --------------------------------------------------------------------------- #
# quote localization ladder (mirrors knowledge._locate_quote, kept local so
# this module stays a pure, dependency-free scorer)
# --------------------------------------------------------------------------- #
def _norm_text(s: str) -> str:
    s = unicodedata.normalize('NFKC', s).casefold()
    return re.sub(r'[^0-9a-z\u4e00-\u9fff]+', '', s)


def _quote_localized(content: str | None, quote: str | None) -> bool:
    quote = (quote or '').strip()
    if not quote or not content:
        return False
    if quote in content:
        return True
    compact = ' '.join(content.split())
    compact_quote = ' '.join(quote.split())
    if compact_quote and compact_quote in compact:
        return True
    n_content, n_quote = _norm_text(content), _norm_text(quote)
    if n_quote and n_quote in n_content:
        return True
    best = 0.0
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        ratio = SequenceMatcher(None, _norm_text(line), n_quote).ratio()
        if ratio > best:
            best = ratio
    return n_quote != '' and best >= 0.85


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ''
    if isinstance(value, dict):
        return True
    return True


def _schema_score(d: ClaimQualityInput) -> float:
    present = sum(1 for k in _REQUIRED if _present(getattr(d, k)))
    return present / len(_REQUIRED)


def _evidence_score(d: ClaimQualityInput) -> float:
    if d.source_quote and d.source_chunk_id:
        return 1.0
    if d.source_quote:
        return 0.6
    return 0.0


def _quote_score(d: ClaimQualityInput) -> float:
    if not d.source_quote:
        return 0.0
    if _quote_localized(d.source_chunk_content, d.source_quote):
        return 1.0
    return 0.2  # quote present but could not be verified against the chunk


def _entity_resolution_score(d: ClaimQualityInput) -> float:
    subj = 0.6 if d.subject_entity else 0.1
    if d.object_name or d.object_text:
        obj = 0.3 if d.object_entity else 0.15
    else:
        obj = 0.0
    desc = 0.1 if (d.subject_entity and (d.subject_entity.get('description') or '').strip()) else 0.0
    return min(1.0, subj + obj + desc)


def _predicate_score(d: ClaimQualityInput) -> float:
    return 1.0 if d.predicate_registered else 0.0


def _conflict_score(d: ClaimQualityInput) -> float:
    if d.is_superseded:
        return 1.0  # conflict already resolved; it is history
    if d.conflict_live:
        return 0.3  # open contradiction with a current claim
    return 0.9


def _provenance_score(d: ClaimQualityInput) -> float:
    return (0.5 * _present(d.source_document_id)
            + 0.3 * _present(d.source_chunk_id)
            + 0.2 * _present(d.source_quote))


def _grade(overall: float) -> str:
    if overall >= 0.85:
        return 'A'
    if overall >= 0.70:
        return 'B'
    if overall >= 0.50:
        return 'C'
    return 'D'


def score_claim(d: ClaimQualityInput) -> ClaimQuality:
    """Pure, deterministic quality score for one claim."""
    dims = {
        'schema': _schema_score(d),
        'evidence': _evidence_score(d),
        'quote': _quote_score(d),
        'entity_resolution': _entity_resolution_score(d),
        'predicate': _predicate_score(d),
        'conflict': _conflict_score(d),
        'provenance': _provenance_score(d),
    }
    overall = sum(WEIGHTS[k] * dims[k] for k in DIMENSIONS)

    flags: list[str] = []
    if not d.predicate_registered:
        flags.append('predicate_unregistered')
    if dims['evidence'] == 0.0:
        flags.append('no_evidence')
    if d.source_quote and dims['quote'] < 1.0:
        flags.append('quote_not_localized')
    if d.conflict_live:
        flags.append('unresolved_conflict')
    if d.is_superseded:
        flags.append('superseded')
    if not (d.object_name or d.object_text):
        flags.append('missing_object')
    if d.confidence is not None and d.confidence < 0.5:
        flags.append('low_confidence')

    return ClaimQuality(claim_id=d.claim_id, dimensions=dims,
                        overall=round(overall, 4), grade=_grade(overall), flags=flags)


# Critical flags that should force a claim into the review queue regardless of
# the numeric threshold.
_CRITICAL_FLAGS = {'predicate_unregistered', 'unresolved_conflict', 'no_evidence'}


def suggest_review(scores: list[ClaimQuality], *, threshold: float = 0.70) -> list[str]:
    """Claim ids that should be sent to review: below threshold or carrying a
    critical flag. Order is preserved (caller sorts by overall first)."""
    out: list[str] = []
    for s in scores:
        if s.overall < threshold or (set(s.flags) & _CRITICAL_FLAGS):
            out.append(s.claim_id)
    return out


# --------------------------------------------------------------------------- #
# DB-backed assembler: build ClaimQualityInput from the repositories
# --------------------------------------------------------------------------- #
def score_claim_by_id(claim_id: str, conn=None) -> ClaimQuality | None:
    from ..ontology import claim_predicate_allowed
    from ..repositories import (ClaimRepository, EntityRepository, EvidenceRepository)

    claims = ClaimRepository(conn)
    claim = claims.get(claim_id)
    if claim is None:
        return None

    subject_entity = EntityRepository(conn).get(claim['subject_id']) if claim.get('subject_id') else None
    object_entity = EntityRepository(conn).get(claim['object_id']) if claim.get('object_id') else None
    predicate_registered = claim_predicate_allowed(claim['predicate'])

    ev = EvidenceRepository(conn).provenance(claim['source_chunk_id']) if claim.get('source_chunk_id') else None
    source_chunk_content = ev['content'] if ev else None

    conflict_live = False
    for rel in claims.relations_for_claim(claim_id):
        if rel.get('relationship') in _LIVE_CONFLICT_RELATIONSHIPS:
            other_id = rel['source_claim_id'] if rel['source_claim_id'] != claim_id else rel['target_claim_id']
            other_status = claims.status_of(other_id)
            # A conflict is only live while the other claim is still current
            # (domain.claim_state is the single definition of that, §30).
            if other_status is not None and other_status != SUPERSEDED:
                conflict_live = True
                break

    return score_claim(ClaimQualityInput(
        claim_id=claim_id,
        subject_name=claim.get('subject_name') or '',
        predicate=claim['predicate'],
        claim_type=claim.get('claim_type', 'factual'),
        polarity=claim.get('polarity', 'positive'),
        modality=claim.get('modality', 'asserted'),
        context=claim.get('context') or {},
        confidence=claim.get('confidence'),
        object_name=claim.get('object_name'),
        object_text=claim.get('object_text'),
        source_document_id=claim.get('source_document_id'),
        source_chunk_id=claim.get('source_chunk_id'),
        source_quote=claim.get('source_quote'),
        source_chunk_content=source_chunk_content,
        subject_entity=subject_entity,
        object_entity=object_entity,
        predicate_registered=predicate_registered,
        is_superseded=not is_current(claim),
        conflict_live=conflict_live,
    ))


def review_queue(*, limit: int = 50, threshold: float = 0.70, conn=None) -> list[dict]:
    """Score current claims and return the weakest ones for human review.

    Sorted ascending by overall (worst first); each entry carries its grade and
    flags so the UI can show *why* a claim is queued.
    """
    from ..repositories import ClaimRepository
    claims = ClaimRepository(conn).list(limit * 4 or 200)
    scored: list[ClaimQuality] = []
    for c in claims:
        q = score_claim_by_id(c['id'], conn)
        if q is not None:
            scored.append(q)
    queued = [s for s in scored if s.overall < threshold or (set(s.flags) & _CRITICAL_FLAGS)]
    queued.sort(key=lambda s: s.overall)
    return [s.to_dict() for s in queued[:limit]]
