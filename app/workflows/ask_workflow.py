"""Direct fact lookup: answer a simple question from a stored Claim, with 0 LLM.

QA is first a retrieval / structured-query problem, and only then a generation
problem. When a question names exactly one known entity, maps to exactly one
registered predicate, and the wiki already holds a Claim for that
``(subject, predicate)``, there is nothing for a model to do — the answer is
already stored. Sending it to an LLM to "read the knowledge back" is slower and
can only add hallucination.

    question -> Query Router -> FACT_LOOKUP -> this workflow -> DirectAnswer

The router (``app.domain.query_router``) is deterministic and owns the *route*;
this module owns the *execution* of the FACT_LOOKUP route:

- :func:`extract_signals` resolves the router's inputs (subject, registered
  predicate candidates, whether a direct claim path exists) without a model.
- :func:`plan_question` pairs those signals with the router's verdict.
- :func:`try_direct_answer` returns a :class:`DirectAnswer` for a FACT_LOOKUP
  question, or ``None`` so the caller can fall back to the ordinary
  retrieval + generation path.

Direct answers never paraphrase: ``answer`` is the stored claim content (or the
stored subject/predicate/object) presented verbatim. A lookup that rewrote the
sentence would be a generation step wearing a lookup's name.

Honesty rule (task §23): when the stored knowledge cannot answer *certainly* —
no current claim, or more than one current claim for the same
``(subject, predicate)`` — the workflow reports ``no_sufficient_evidence``
instead of picking the value that merely looks right.

Application layer: it imports repositories / domain, never FastAPI and never SQL
(ADR-002, ADR-004).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.claim_state import resolve, select_current
from ..domain.query_router import (FACT_LOOKUP, QueryPlan, QuerySignals,
                                   classify, has_multi_hop_intent,
                                   has_temporal_intent)
from ..ontology import match_claim_predicates
from ..repositories import ClaimRepository, DocumentRepository, EntityRepository

NO_SUFFICIENT_EVIDENCE = 'no_sufficient_evidence'
ANSWERED = 'answered'

# An entity shorter than two characters is too ambiguous to anchor a lookup
# ("AI" or a single letter would match half the questions in the wiki).
_MIN_SUBJECT_LEN = 2
_PREDICATE_LIMIT = 5
_RELATED_LIMIT = 10


@dataclass
class DirectAnswer:
    """The outcome of a 0-LLM lookup.

    ``status`` is either ``answered`` or ``no_sufficient_evidence``. On the
    latter, ``answer`` / ``answer_value`` / ``claim`` are ``None`` and ``reason``
    explains why; ``citations`` and ``evidence`` stay lists so the shape the
    caller sees never changes between success and honest failure.
    """

    status: str
    route: str
    answer: str | None = None
    answer_value: str | None = None
    claim: dict | None = None
    citations: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    reason: str | None = None

    def to_dict(self) -> dict:
        return {
            'status': self.status,
            'route': self.route,
            'answer': self.answer,
            'answer_value': self.answer_value,
            'claim': self.claim,
            'citations': self.citations,
            'evidence': self.evidence,
            'reason': self.reason,
        }


def _resolve_subject(question: str) -> dict | None:
    """The longest known entity name that literally occurs in the question.

    Deterministic and model-free. "苹果" is a substring of "苹果公司", so when both
    entities exist the *longest* match wins: answering about the wrong, shorter
    entity would be worse than answering nothing. Equal-length ties are broken by
    name, so the same question always resolves the same way.
    """
    haystack = str(question or '').casefold()
    candidates = [c for c in EntityRepository().resolution_candidates(500)
                  if c.get('name') and len(str(c['name'])) >= _MIN_SUBJECT_LEN]
    matches = [c for c in candidates if str(c['name']).casefold() in haystack]
    if not matches:
        return None
    matches.sort(key=lambda c: (-len(str(c['name'])), str(c['name'])))
    return matches[0]


def _related_claims(subject_id: str, predicate: str) -> list[dict]:
    """Non-rejected / non-archived claims for one ``(subject, predicate)`` pair."""
    return ClaimRepository().related(subject_id=subject_id, predicate=predicate,
                                     exclude_id='', limit=_RELATED_LIMIT)


def extract_signals(question: str) -> QuerySignals:
    """Resolve the router's inputs from the knowledge base, without a model.

    The subject is the longest entity name occurring in the question; the
    predicate candidates are the registered predicates the question maps to
    (:func:`app.ontology.match_claim_predicates`); temporal / multi-hop intent
    come from the router's own deterministic markers.

    ``direct_claim_available`` is True when the wiki holds a claim row for one of
    the candidate ``(subject, predicate)`` pairs. ``related`` already drops
    rejected / archived rows, and a superseded-only pair still *is* a structured
    answer path: it is exactly the case where the workflow must answer "no
    sufficient evidence" rather than fall back to a model (§23). Whether the
    stored claim is *current* and *unambiguous* is decided at answer time by
    ``app.domain.claim_state``.
    """
    question = str(question or '')
    match = _resolve_subject(question)
    predicates = tuple(match_claim_predicates(question, limit=_PREDICATE_LIMIT))
    direct_claim_available = False
    if match:
        for predicate in predicates:
            # `resolve` is the domain's single definition of claim lifecycle, so
            # the workflow never invents its own notion of "there is a claim".
            if resolve(_related_claims(match['id'], predicate)):
                direct_claim_available = True
                break
    return QuerySignals(
        question=question,
        subject=str(match['name']) if match else None,
        predicates=predicates,
        direct_claim_available=direct_claim_available,
        temporal=has_temporal_intent(question),
        multi_hop=has_multi_hop_intent(question),
    )


def plan_question(question: str) -> tuple[QuerySignals, QueryPlan]:
    """Resolved signals together with the router's verdict for one question."""
    signals = extract_signals(question)
    return signals, classify(signals)


def _provenance(claim: dict) -> tuple[dict | None, list[dict]]:
    """Citation + evidence for a claim, in the shape ``/api/ask`` already uses."""
    document_id = claim.get('source_document_id')
    chunk_id = claim.get('source_chunk_id')
    if not document_id or not chunk_id:
        return None, []
    document = DocumentRepository().get(document_id)
    title = str((document or {}).get('title') or '')
    start = int(claim.get('source_start_offset') or 0)
    end = int(claim.get('source_end_offset') or 0)
    citation = {'document_id': document_id, 'chunk_id': chunk_id, 'title': title,
                'start_offset': start, 'end_offset': end}
    evidence = {**citation, 'quote': str(claim.get('source_quote') or '')}
    return citation, [evidence]


def _answered(plan: QueryPlan, subject_name: str, predicate: str,
              claim: dict) -> DirectAnswer | None:
    """Present one current claim verbatim. ``None`` when it carries no object."""
    object_value = claim.get('object_name') or claim.get('object_text')
    if not object_value:
        return None
    object_value = str(object_value)
    citation, evidence = _provenance(claim)
    # No rewriting: the stored content is the answer. Only when a claim has no
    # content do we fall back to the stored subject/predicate/object.
    answer = claim.get('content') or f'{subject_name} {predicate} {object_value}'
    return DirectAnswer(
        status=ANSWERED,
        route=plan.route,
        answer=str(answer),
        answer_value=object_value,
        claim={
            'claim_id': claim.get('id'),
            'subject': subject_name,
            'predicate': predicate,
            'object': object_value,
            'content': claim.get('content'),
            'status': claim.get('status'),
            'source_document_id': claim.get('source_document_id'),
            'source_chunk_id': claim.get('source_chunk_id'),
        },
        citations=[citation] if citation else [],
        evidence=evidence,
    )


def try_direct_answer(question: str) -> DirectAnswer | None:
    """Answer ``question`` from stored knowledge with no LLM, or return ``None``.

    ``None`` means "not a direct lookup — let the normal retrieval + generation
    path handle it". It is also the universal error answer: this function never
    raises, because a failed lookup must degrade to the LLM path, not break the
    request.

    Under the FACT_LOOKUP route the current claim for ``(subject, predicate)``
    decides the outcome:

    - exactly one, with an object -> ``answered``, verbatim;
    - none -> ``no_sufficient_evidence`` (``no_current_claim``);
    - two or more -> ``no_sufficient_evidence``
      (``ambiguous_multiple_current_claims``): §23 prefers an honest "not enough
      evidence" over guessing one of several competing values.
    """
    try:
        signals, plan = plan_question(question)
        if plan.route != FACT_LOOKUP:
            return None
        if not signals.subject or len(signals.predicates) != 1:
            return None
        predicate = signals.predicates[0]
        match = _resolve_subject(signals.question)
        if not match:
            return None
        claims = select_current(_related_claims(match['id'], predicate))
        if not claims:
            return DirectAnswer(status=NO_SUFFICIENT_EVIDENCE, route=plan.route,
                                reason='no_current_claim')
        if len(claims) > 1:
            return DirectAnswer(status=NO_SUFFICIENT_EVIDENCE, route=plan.route,
                                reason='ambiguous_multiple_current_claims')
        answered = _answered(plan, str(signals.subject), predicate, claims[0])
        if answered is None:
            return DirectAnswer(status=NO_SUFFICIENT_EVIDENCE, route=plan.route,
                                reason='no_object_value')
        return answered
    except Exception:
        # A lookup is an optimisation, never a dependency: any failure falls back
        # to the LLM path instead of surfacing as an error.
        return None


def explain(question: str) -> dict:
    """Why this question routes the way it does (task §26).

    Exposes the routing *inputs* and verdict — not the answer — so an API or UI
    can show "this was answered without a model because ...".
    """
    signals, plan = plan_question(question)
    return {
        'signals': {
            'question': signals.question,
            'subject': signals.subject,
            'predicates': list(signals.predicates),
            'direct_claim_available': signals.direct_claim_available,
            'temporal': signals.temporal,
            'multi_hop': signals.multi_hop,
        },
        'plan': plan.to_dict(),
    }
