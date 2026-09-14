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
  fabricated. A correct refusal makes no assertion, so it is fully grounded.

Because :func:`validate_citations` also treats a *short* answer with no
citations as a refusal (its heuristic), this module gates ``citation_coverage``
and ``groundedness`` on the stricter contract below: an answer that offers no
evidence and carries no explicit refusal signal is scored as ungrounded
(``0.0``) rather than inheriting the module's lenient shortcut. The raw,
unmodified module result is still exposed under ``dimensions``.
* ``answer_correctness`` — normalised (casefold + whitespace-collapsed) substring
  / exact match against ``expected_answer``.
* ``refusal_correct``    — whether the answer refused exactly when it should.

The §23 ``NO_SUFFICIENT_EVIDENCE`` refusal contract
---------------------------------------------------
An answer counts as a **refusal** (i.e. the system admits it has no sufficient
evidence) if and only if *all* of the following hold:

1. ``citations`` is empty — no evidence is offered as support;
2. it carries an explicit signal: it is empty, **or** it contains one of the
   :data:`REFUSAL_MARKERS` (the same phrases the QA API returns, e.g.
   ``知识库中没有找到与该问题匹配的证据``);
3. it contains no inline citation marker (``[doc:.. chunk:..]``).

Conversely, any substantive answer that is *not* a refusal while the case has no
sufficient evidence (``expected_refusal`` or an empty citation list) counts as an
**unknown-answer hallucination**. The dataset reports that rate; it must be 0.0.

Pure and offline (ADR-011): no FastAPI, no AgentScope, no sqlite3, no LLM.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..domain.citation_validation import validate_citations

# Phrases that signal an explicit "no sufficient evidence" refusal. They mirror
# what the QA API returns when retrieval finds nothing, and the hints used by
# ``domain.citation_validation``.
REFUSAL_MARKERS: tuple[str, ...] = (
    '知识库中没有',
    '没有找到',
    '无法回答',
    'no sufficient evidence',
    'no evidence',
    'insufficient evidence',
    'not found in the knowledge base',
)

_INLINE_CITATION = re.compile(r'\[doc:[^\]]*chunk:[^\]]*\]', re.IGNORECASE)


# --- refusal contract --------------------------------------------------------

def is_refusal(answer: str, citations: list[dict] | None) -> bool:
    """Apply the §23 ``NO_SUFFICIENT_EVIDENCE`` refusal test (see module docstring).

    Deterministic and side-effect free: the three conditions are checked in
    order and a single ``False`` (evidence offered, inline markers present, or no
    explicit signal) is enough to classify the answer as substantive.
    """
    if citations:
        return False
    if _INLINE_CITATION.search(answer or ''):
        return False
    text = (answer or '').strip()
    if not text:
        return True
    return any(marker in text for marker in REFUSAL_MARKERS)


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
    """Per-case scores plus the deterministic citation report."""

    id: str
    citation_coverage: float
    groundedness: float
    answer_correctness: float
    refusal_correct: bool
    is_refusal: bool
    support: float | None
    flags: list[str] = field(default_factory=list)
    dimensions: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'citation_coverage': self.citation_coverage,
            'groundedness': self.groundedness,
            'answer_correctness': self.answer_correctness,
            'refusal_correct': self.refusal_correct,
            'is_refusal': self.is_refusal,
            'support': self.support,
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


def _answer_correctness(case: QaCase, refusal: bool) -> float:
    expected = _normalize_text(case.expected_answer)
    if expected:
        answer = _normalize_text(case.answer)
        return 1.0 if (expected == answer or expected in answer) else 0.0
    # Nothing substantive is expected: correctness is decided by the refusal.
    if case.expected_refusal:
        return 1.0 if refusal else 0.0
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

    refusal = is_refusal(case.answer, case.citations)
    refusal_correct = refusal == bool(case.expected_refusal)

    if case.expected_refusal and refusal:
        # A correct refusal asserts nothing, so there is nothing that could be
        # ungrounded: coverage and groundedness are perfect by construction.
        coverage = 1.0
        groundedness = 1.0
    elif not case.citations:
        # Not a refusal, yet no evidence was offered at all. ``validate_citations``
        # would score a short answer like this as a refusal; the stricter §23
        # contract does not, so the answer is ungrounded and untraceable.
        coverage = 0.0
        groundedness = 0.0
    else:
        coverage = float(dimensions.get('coverage') or 0.0)
        groundedness = float(report.overall)

    return QaCaseResult(
        id=case.id,
        citation_coverage=round(coverage, 4),
        groundedness=round(groundedness, 4),
        answer_correctness=round(_answer_correctness(case, refusal), 4),
        refusal_correct=refusal_correct,
        is_refusal=refusal,
        support=dimensions.get('support'),  # None without an LLM — never faked
        flags=list(report.flags),
        dimensions=dimensions,
    )


def _has_no_evidence(case: QaCase) -> bool:
    """No case-declared sufficiency: it should refuse, or it has no citations."""
    return bool(case.expected_refusal) or not case.citations


def evaluate_qa_dataset(cases: list[QaCase], *,
                        validator: Callable[..., Any] | None = None) -> dict:
    """Aggregate report over the whole golden set.

    ``citation_coverage`` / ``groundedness`` / ``answer_correctness`` are means
    over cases. ``unknown_answer_hallucination_rate`` is the fraction of
    no-sufficient-evidence cases that still produced a substantive (non-refusal)
    answer, and is expected to be ``0.0``.
    """
    results = [evaluate_qa_case(case, validator=validator) for case in cases]
    count = len(results)

    def mean(attribute: str) -> float:
        if not count:
            return 0.0
        return sum(getattr(r, attribute) for r in results) / count

    hallucinations = sum(
        1 for case, result in zip(cases, results)
        if _has_no_evidence(case) and not result.is_refusal
    )

    return {
        'citation_coverage': mean('citation_coverage'),
        'groundedness': mean('groundedness'),
        'answer_correctness': mean('answer_correctness'),
        'unknown_answer_hallucination_rate': (hallucinations / count) if count else 0.0,
        'case_count': count,
        'case_details': [result.to_dict() for result in results],
    }
