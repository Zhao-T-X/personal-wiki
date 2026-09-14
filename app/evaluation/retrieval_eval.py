"""Retrieval evaluation over a recorded golden set (task §16/§18).

QA is first a *retrieval* problem: if the right chunk is never retrieved, no
amount of answer polishing can help. This module scores **already-recorded**
retrieval results against a hand-checked set of relevant chunk ids. It never
embeds, never opens a database and never calls a model, so the numbers are
reproducible offline (no API key, no service).

Definitions (``R`` = relevant ids, ``ret`` = retrieved ids in rank order)::

    recall@k    = |R ∩ set(ret[:k])| / |R|
    precision@k = |R ∩ set(ret[:k])| / min(k, len(ret))
    MRR         = 1 / (rank of the first relevant hit), 0.0 when none

Dataset-level metrics are **micro-averaged**: the raw true/false counts are
summed across every case and the ratio is taken once, using the shared
:mod:`app.evaluation.metrics` primitives (empty denominators return ``0.0``
rather than ``nan``). A case whose relevant set is empty therefore contributes
``0`` to both numerator and denominator and cannot distort the aggregate.

Pure and offline (ADR-011): no FastAPI, no AgentScope, no sqlite3, no LLM.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .metrics import precision, recall


@dataclass
class RetrievalCase:
    """One golden sample: a question plus its recorded retrieval result.

    ``relevant_chunk_ids`` is the hand-checked ground truth; the
    ``retrieved_chunk_ids`` list is what the system actually returned, in rank
    order (index 0 = rank 1). Nothing is retrieved at evaluation time.
    """

    id: str
    question: str
    relevant_chunk_ids: list[str] = field(default_factory=list)
    retrieved_chunk_ids: list[str] = field(default_factory=list)


def load_retrieval_cases(path: str | Path) -> list[RetrievalCase]:
    """Load ``{"cases": [...]}`` from ``path`` into ``RetrievalCase`` objects."""
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    cases: list[RetrievalCase] = []
    for item in raw.get('cases', []) or []:
        cases.append(RetrievalCase(
            id=str(item.get('id') or ''),
            question=str(item.get('question') or ''),
            relevant_chunk_ids=[str(x) for x in (item.get('relevant_chunk_ids') or [])],
            retrieved_chunk_ids=[str(x) for x in (item.get('retrieved_chunk_ids') or [])],
        ))
    return cases


# --- metric primitives -------------------------------------------------------

def _hits_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> int:
    """Distinct relevant ids inside the first ``k`` retrieved ids."""
    if k <= 0:
        return 0
    return len(set(retrieved_ids[:k]) & relevant_ids)


def recall_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Fraction of the relevant ids that appear in the top ``k`` results."""
    relevant = set(relevant_ids)
    tp = _hits_at_k(retrieved_ids, relevant, k)
    return recall(tp, len(relevant) - tp)


def precision_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Fraction of the top ``k`` results that are relevant.

    The denominator is ``min(k, len(retrieved))`` so a short result list is not
    punished for results it never had the chance to return.
    """
    window = list(retrieved_ids[:k]) if k > 0 else []
    denominator = min(k, len(window))
    tp = len(set(window) & set(relevant_ids))
    return precision(tp, denominator - tp)


def reciprocal_rank(retrieved_ids: list[str], relevant_ids: set[str]) -> float:
    """1 / rank of the first relevant hit (0.0 when nothing relevant was found)."""
    relevant = set(relevant_ids)
    for rank, chunk_id in enumerate(retrieved_ids, start=1):
        if chunk_id in relevant:
            return 1.0 / rank
    return 0.0


# --- scoring -----------------------------------------------------------------

def evaluate_retrieval_case(case: RetrievalCase) -> dict:
    """Per-case recall@5/recall@10/precision@5 plus the counts behind them."""
    relevant = set(case.relevant_chunk_ids)
    retrieved = list(case.retrieved_chunk_ids)
    tp5 = _hits_at_k(retrieved, relevant, 5)
    tp10 = _hits_at_k(retrieved, relevant, 10)
    return {
        'id': case.id,
        'question': case.question,
        'recall_at_5': recall_at_k(retrieved, relevant, 5),
        'recall_at_10': recall_at_k(retrieved, relevant, 10),
        'precision_at_5': precision_at_k(retrieved, relevant, 5),
        'mrr': reciprocal_rank(retrieved, relevant),
        'relevant_count': len(relevant),
        'retrieved_count': len(retrieved),
        'hits_at_5': tp5,
        'hits_at_10': tp10,
    }


def evaluate_retrieval_dataset(cases: list[RetrievalCase]) -> dict:
    """Micro-averaged report over a whole golden set.

    Counts are summed first and the ratios derived from the sums, so that a
    case with an empty relevant set never forces a meaningless ``0.0`` into the
    mean. ``mrr`` has no meaningful micro-average, so it is reported as the
    plain mean over cases.
    """
    results = [evaluate_retrieval_case(case) for case in cases]

    totals = {
        'hits_at_5': sum(r['hits_at_5'] for r in results),
        'hits_at_10': sum(r['hits_at_10'] for r in results),
        'relevant': sum(r['relevant_count'] for r in results),
        'window_at_5': sum(min(5, r['retrieved_count']) for r in results),
    }

    report = {
        'recall_at_5': recall(totals['hits_at_5'], totals['relevant'] - totals['hits_at_5']),
        'recall_at_10': recall(totals['hits_at_10'], totals['relevant'] - totals['hits_at_10']),
        'precision_at_5': precision(totals['hits_at_5'],
                                    totals['window_at_5'] - totals['hits_at_5']),
        'mrr': (sum(r['mrr'] for r in results) / len(results)) if results else 0.0,
        'totals': totals,
        'case_count': len(results),
        'case_details': results,
    }
    return report
