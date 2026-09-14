"""Answer-quality evaluation over a recorded golden set (task §16/§18).

The QA harness must not be satisfied by "the answer looks right". It scores four
things, all deterministically, over answers the system *already produced* (the
golden file records the answer, its ``citations`` and the cited chunk contents):

* ``citation_coverage``  — the answer inline-marks its sources
  (``[doc:.. chunk:..]``) and the marked chunks are exactly the cited ones.
* ``groundedness``       — the deterministic citation report. The answer is
  validated with :func:`app.domain.citation_validation.validate_citations`
  **without an ``llm``**, so only locatability / coverage / currentness run and
  the semantic ``support`` dimension is honestly left as ``None`` — it is never
  fabricated. A safety stop makes no assertion, so it is fully grounded.
* ``answer_correctness`` — normalised (casefold + whitespace-collapsed) substring
  / exact match against ``expected_answer``.
* ``refusal_kind``       — the answer's kind under the shared refusal semantics.

Refusal semantics (ADR-014)
---------------------------
This module no longer decides for itself what a refusal is. It consumes the one
definition in :mod:`app.domain.refusal` through the **evaluation** detector, so a
dataset label and a runtime judgement can never disagree about the same answer:

* ``NON_REFUSAL``           — a substantive answer. **No citations is not a
  refusal**; it is an answer that happens to be untraceable.
* ``INSUFFICIENT_EVIDENCE`` — the answer says the knowledge base lacks evidence.
  This is the behaviour §23 *wants*, so it counts as a correct safety stop (it is
  deliberately not the same kind as a refusal).
* ``REFUSAL``               — the answer declines to answer.
* ``UNKNOWN``               — no signal (e.g. an empty answer).

``expected_refusal`` in the golden file means "expected no substantive answer", so
either kind of safety stop satisfies it. A case that offered no evidence yet still
asserted something (``NON_REFUSAL``) is an **unknown-answer hallucination**; an
empty ``UNKNOWN`` answer asserts nothing and is therefore not one.

Pure and offline (ADR-011): no FastAPI, no AgentScope, no sqlite3, no LLM.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..domain.citation_validation import validate_citations
from ..domain.refusal import RefusalDecision, RefusalKind, classify_refusal

# How this module consumes the shared semantics; recorded on every decision so a
# disagreement is attributable to a detector rather than to the definition.
DETECTOR = 'evaluation'


def refusal_decision(answer: str, citations: list[dict] | None = None) -> RefusalDecision:
    """Classify one answer with the shared semantics, as the evaluation detector."""
    return classify_refusal(answer, citations=citations, detector=DETECTOR)


def refusal_kind(answer: str, citations: list[dict] | None = None) -> str:
    """The :class:`RefusalKind` value for one answer (a plain string)."""
    return refusal_decision(answer, citations).kind.value


# --- data model --------------------------------------------------------------

@dataclass
class QaCase:
    """One golden sample: the produced answer and everything needed to score it."""

    id: str
    question: str
    answer: str
    citations: list[dict] = field(default_factory=list)
    chunks: dict[str, str] = field(default_factory=dict)
    expected_answer: str = ''
    expected_refusal: bool = False


@dataclass
class QaCaseResult:
    """Per-case scores plus the deterministic citation and refusal verdicts."""

    id: str
    citation_coverage: float
    groundedness: float
    answer_correctness: float
    refusal_correct: bool
    refusal_kind: str
    is_safety_stop: bool
    support: float | None
    refusal: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    dimensions: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'citation_coverage': self.citation_coverage,
            'groundedness': self.groundedness,
            'answer_correctness': self.answer_correctness,
            'refusal_correct': self.refusal_correct,
            'refusal_kind': self.refusal_kind,
            'is_safety_stop': self.is_safety_stop,
            'support': self.support,
            'refusal': dict(self.refusal),
            'flags': list(self.flags),
            'dimensions': dict(self.dimensions),
        }


# --- loading -----------------------------------------------------------------

def _normalize_chunks(raw: Any) -> dict[str, str]:
    """Accept either ``{chunk_id: content}`` or ``[{chunk_id, content}, ...]``."""
    chunks: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            chunks[str(key)] = str(value)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get('chunk_id'):
                chunks[str(item['chunk_id'])] = str(item.get('content') or '')
    return chunks


def load_qa_cases(path: str | Path) -> list[QaCase]:
    """Load ``{"cases": [...]}`` from ``path`` into ``QaCase`` objects."""
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    cases: list[QaCase] = []
    for item in raw.get('cases', []) or []:
        cases.append(QaCase(
            id=str(item.get('id') or ''),
            question=str(item.get('question') or ''),
            answer=str(item.get('answer') or ''),
            citations=list(item.get('citations') or []),
            chunks=_normalize_chunks(item.get('chunks')),
            expected_answer=str(item.get('expected_answer') or ''),
            expected_refusal=bool(item.get('expected_refusal')),
        ))
    return cases


# --- scoring -----------------------------------------------------------------

def _normalize_text(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '')).strip().casefold()


def _answer_correctness(case: QaCase, answered: bool) -> float:
    expected = _normalize_text(case.expected_answer)
    if expected:
        answer = _normalize_text(case.answer)
        return 1.0 if (expected == answer or expected in answer) else 0.0
    # Nothing substantive is expected: correctness is decided by whether the
    # system stopped safely instead of answering.
    if case.expected_refusal:
        return 1.0 if not answered else 0.0
    return 1.0


def evaluate_qa_case(case: QaCase, *,
                     validator: Callable[..., Any] | None = None) -> QaCaseResult:
    """Score one recorded answer against its expectations.

    ``validator`` defaults to :func:`validate_citations` and is injectable for
    testing. It is always called **without** an ``llm``, so the report keeps the
    three deterministic dimensions and leaves ``support`` as ``None``.
    """
    validator = validator or validate_citations
    report = validator(case.question, case.answer, list(case.citations or []))
    dimensions = dict(report.dimensions)

    decision = refusal_decision(case.answer, case.citations)
    safety_stop = decision.is_safety_stop
    refusal_correct = safety_stop == bool(case.expected_refusal)
    answered = decision.kind is RefusalKind.NON_REFUSAL

    if safety_stop:
        # A safety stop asserts nothing, so there is nothing that could be
        # ungrounded: coverage and groundedness are perfect by construction.
        coverage = 1.0
        groundedness = 1.0
    elif not case.citations:
        # An asserted answer with no evidence at all. ``validate_citations`` would
        # score every citationless answer as untraceable (coverage 0.2); the
        # evaluation contract is stricter still: nothing can be traced, so 0.0.
        coverage = 0.0
        groundedness = 0.0
    else:
        coverage = float(dimensions.get('coverage') or 0.0)
        groundedness = float(report.overall)

    return QaCaseResult(
        id=case.id,
        citation_coverage=round(coverage, 4),
        groundedness=round(groundedness, 4),
        answer_correctness=round(_answer_correctness(case, answered), 4),
        refusal_correct=refusal_correct,
        refusal_kind=decision.kind.value,
        is_safety_stop=safety_stop,
        support=dimensions.get('support'),  # None without an LLM — never faked
        refusal=decision.to_dict(),
        flags=list(report.flags),
        dimensions=dimensions,
    )


def _expects_no_answer(case: QaCase) -> bool:
    """The case declares (or shows) that no substantive answer was available."""
    return bool(case.expected_refusal) or not case.citations


def evaluate_qa_dataset(cases: list[QaCase], *,
                        validator: Callable[..., Any] | None = None) -> dict:
    """Aggregate report over the whole golden set.

    ``citation_coverage`` / ``groundedness`` / ``answer_correctness`` are means
    over cases. ``unknown_answer_hallucination_rate`` is the fraction of
    no-answer-expected cases that nonetheless **asserted** something, and is
    expected to be ``0.0``. An ``UNKNOWN`` answer is not counted: asserting
    nothing is not hallucinating.
    """
    results = [evaluate_qa_case(case, validator=validator) for case in cases]
    count = len(results)

    def mean(attribute: str) -> float:
        if not count:
            return 0.0
        return sum(getattr(r, attribute) for r in results) / count

    hallucinations = sum(
        1 for case, result in zip(cases, results)
        if _expects_no_answer(case) and result.refusal_kind == RefusalKind.NON_REFUSAL.value
    )

    return {
        'citation_coverage': mean('citation_coverage'),
        'groundedness': mean('groundedness'),
        'answer_correctness': mean('answer_correctness'),
        'unknown_answer_hallucination_rate': (hallucinations / count) if count else 0.0,
        'case_count': count,
        'case_details': [result.to_dict() for result in results],
    }
