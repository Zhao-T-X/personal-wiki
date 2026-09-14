"""Knowledge Compilation Pipeline — the one door from a draft to canonical knowledge.

Every agent that produces ontology values (Extraction, Research, Correction,
Curator, Review) goes through here. There is no second opinion about what a
predicate means and no per-agent rule set:

    LLM draft            ClaimDraft          "I think this expresses a CEO relation"
      -> resolve         PredicateResolver   candidate -> registered predicate | unresolved
      -> validate        Domain rules        domain/range, claim_type, polarity, modality
      -> gate            QualityGate         accept / review / reject from real evidence
      -> compile         CanonicalClaim      the only shape persistence accepts

The LLM only ever touches ``ClaimDraft``. ``CanonicalClaim`` is constructed by
this module, and only when resolution produced exactly one registered predicate.
An unresolved candidate keeps its legal failure exit: it is reported, never
compiled, so it can never reach a Repository (ONTOLOGY MUTATION POLICY rule 8).

Pure logic: no database, no framework, no LLM (docs/adr/ADR-011).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..ontology import (canonical_claim_type, canonical_modality, canonical_polarity,
                        normalize_name, normalize_predicate, relation_endpoint_allowed,
                        relation_spec)
from .predicate_resolver import PredicateResolution, resolve_predicate
from .quality_gate import (REJECT, REVIEW, QualityAssessment, QualitySignals,
                           evaluate as evaluate_quality)

COMPILED = 'compiled'
REJECTED = 'rejected'
UNRESOLVED = 'unresolved'


@dataclass
class ClaimDraft:
    """What an LLM is allowed to produce: candidates and signals, never canon.

    The field is named ``predicate_candidate`` on purpose. Naming it
    ``predicate`` invites the model (and the next reader) to believe the value
    is already ontology; naming it a *candidate* makes the compile step in
    between impossible to forget.
    """

    subject: str
    predicate_candidate: str | None
    object: str | None = None
    claim_type: str = 'factual'
    polarity: str = 'positive'
    modality: str = 'asserted'
    temporal_signal: str | None = None
    context: dict = field(default_factory=dict)
    confidence: float | None = None
    subject_types: list[str] = field(default_factory=list)
    object_types: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CanonicalClaim:
    """Compiled knowledge: only this shape may be persisted."""

    subject: str
    predicate: str
    object: str | None
    claim_type: str
    polarity: str
    modality: str
    temporal_signal: str | None
    context: dict
    confidence: float | None

    def to_dict(self) -> dict:
        return {
            'subject': self.subject,
            'predicate': self.predicate,
            'object': self.object,
            'claim_type': self.claim_type,
            'polarity': self.polarity,
            'modality': self.modality,
            'temporal_signal': self.temporal_signal,
            'context': self.context,
            'confidence': self.confidence,
        }


@dataclass(frozen=True)
class CompileResult:
    """The pipeline outcome. ``claim`` is non-null only when ``ok`` is true."""

    status: str
    claim: CanonicalClaim | None
    resolution: PredicateResolution
    reasons: tuple[str, ...] = ()
    # Present only when the caller supplied evidence signals; carrying it here
    # lets Review route on the gate verdict without re-deriving it (§14).
    quality: QualityAssessment | None = None

    @property
    def ok(self) -> bool:
        return self.status == COMPILED and self.claim is not None

    def to_dict(self) -> dict:
        return {
            'status': self.status,
            'claim': self.claim.to_dict() if self.claim else None,
            'resolution': self.resolution.to_dict(),
            'reasons': list(self.reasons),
            'quality': asdict(self.quality) if self.quality is not None else None,
        }


class KnowledgeCompiler:
    """Compile Drafts into Canonical knowledge, or refuse with a reason."""

    def compile_claim(self, draft: ClaimDraft, *,
                      quality: QualitySignals | None = None) -> CompileResult:
        """Compile one draft, optionally passing a Quality Gate verdict.

        When ``quality`` is supplied the deterministic gate is consulted and it
        can **veto** acceptance: a ``reject`` verdict makes the compile fail even
        though the ontology was legal (a claim can be well-formed and still not
        be knowledge). A ``review`` verdict still compiles but is flagged, which
        is what sends the claim to human review rather than auto-accept (§14).
        """
        assessment = evaluate_quality(quality) if quality is not None else None

        resolution = resolve_predicate(
            draft.predicate_candidate,
            text=self._context_text(draft),
            limit=5,
        )
        if not resolution.resolved or not resolution.predicate:
            # No registered predicate: report, do not compile, do not invent.
            return CompileResult(status=UNRESOLVED, claim=None, resolution=resolution,
                                 reasons=(resolution.reason,), quality=assessment)

        try:
            claim_type = canonical_claim_type(draft.claim_type)
            polarity = canonical_polarity(draft.polarity)
            modality = canonical_modality(draft.modality)
        except ValueError as exc:
            return CompileResult(status=REJECTED, claim=None, resolution=resolution,
                                 reasons=(str(exc),), quality=assessment)

        # Domain/range: a registered predicate can still be used illegally
        # (e.g. an Organization value where a Person is required). Each declared
        # endpoint is checked on its own — a side we know nothing about is
        # unconstrained, not a violation.
        spec = relation_spec(resolution.predicate)
        if spec and (draft.subject_types or draft.object_types):
            try:
                source_ok = relation_endpoint_allowed(
                    draft.subject_types, resolution.predicate)
                target_ok = relation_endpoint_allowed(
                    draft.object_types, resolution.predicate, target=True)
            except ValueError as exc:
                return CompileResult(status=REJECTED, claim=None, resolution=resolution,
                                     reasons=(str(exc),), quality=assessment)
            if not (source_ok and target_ok):
                return CompileResult(
                    status=REJECTED, claim=None, resolution=resolution,
                    reasons=('domain_range_violation',), quality=assessment)

        if assessment is not None and assessment.verdict == REJECT:
            # Legal ontology, unacceptable quality: still not knowledge.
            return CompileResult(status=REJECTED, claim=None, resolution=resolution,
                                 reasons=('quality_gate_rejected', *assessment.reasons),
                                 quality=assessment)

        signal = draft.temporal_signal or resolution.temporal_signal
        claim = CanonicalClaim(
            subject=' '.join(str(draft.subject).split()),
            predicate=resolution.predicate,
            object=(draft.object or None),
            claim_type=claim_type,
            polarity=polarity,
            modality=modality,
            temporal_signal=signal,
            context=draft.context if isinstance(draft.context, dict) else {},
            confidence=draft.confidence,
        )
        reasons = ('quality_gate_review',) if (
            assessment is not None and assessment.verdict == REVIEW) else ()
        return CompileResult(status=COMPILED, claim=claim, resolution=resolution,
                             reasons=reasons, quality=assessment)

    def compile_extraction_claim(self, raw: dict[str, Any],
                                 *, entities: dict[str, dict] | None = None,
                                 quality: QualitySignals | None = None) -> CompileResult:
        """Compile one claim from an extraction payload (the extraction path).

        ``entities`` optionally maps a normalized entity name to its record so
        the domain/range gate can run with the declared types.
        """
        index = entities or {}
        subject = str(raw.get('subject') or '').strip()
        object_text = raw.get('object')
        subject_record = index.get(normalize_name(subject), {})
        object_record = index.get(normalize_name(object_text or ''), {}) if object_text else {}
        draft = ClaimDraft(
            subject=subject,
            predicate_candidate=raw.get('predicate'),
            object=object_text,
            claim_type=raw.get('claim_type') or 'factual',
            polarity=raw.get('polarity') or 'positive',
            modality=raw.get('modality') or 'asserted',
            temporal_signal=raw.get('temporal_signal'),
            context=raw.get('context') if isinstance(raw.get('context'), dict) else {},
            confidence=raw.get('confidence'),
            subject_types=list(subject_record.get('types') or []),
            object_types=list(object_record.get('types') or []),
        )
        return self.compile_claim(draft, quality=quality)

    @staticmethod
    def _context_text(draft: ClaimDraft) -> str:
        """Context offered to the resolver for *suggestions only*."""
        parts = [draft.subject, draft.predicate_candidate or '', draft.object or '']
        note = draft.context.get('note') if isinstance(draft.context, dict) else None
        if note:
            parts.append(str(note))
        return ' '.join(p for p in parts if p).strip()


def predicate_candidate(value: str | None) -> str | None:
    """Fold a raw predicate-shaped value into candidate form (formatting only)."""
    if value in (None, ''):
        return None
    return normalize_predicate(str(value)) or None
