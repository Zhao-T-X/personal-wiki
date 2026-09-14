"""Wire the pure evaluation harness to bundled golden sets and the run store.

This module is part of the backend wiring, so it is *allowed* to touch the
database (via :mod:`app.evaluation.store`); the pure harness in ``*_eval.py`` and
``metrics.py`` must never be imported into anything that talks to a DB or a web
framework. Keep this file free of direct FastAPI / AgentScope / sqlite3 /
``app.llm`` imports — it only goes through :mod:`app.db` and the pure harness.
"""
from __future__ import annotations

from pathlib import Path

from .extraction_eval import (load_cases as _load_extraction_cases,
                              evaluate_dataset as _evaluate_extraction)
from .qa_eval import (load_qa_cases as _load_qa_cases,
                      evaluate_qa_dataset as _evaluate_qa)
from .retrieval_eval import (load_retrieval_cases as _load_retrieval_cases,
                             evaluate_retrieval_dataset as _evaluate_retrieval)
from .store import save_run, get_run

# Bundled golden sets shipped with the package so the API needs no `tests/`.
_GOLDEN_DIR = Path(__file__).resolve().parent / 'data' / 'golden'

# Per-suite loader + evaluator. A suite name not in this map is invalid.
_SUITES = {
    'extraction': ('extraction.json', _load_extraction_cases, _evaluate_extraction),
    'qa': ('qa.json', _load_qa_cases, _evaluate_qa),
    'retrieval': ('retrieval.json', _load_retrieval_cases, _evaluate_retrieval),
}

# Keys that are aggregate *details*, excluded from the scalar summary.
_DETAIL_KEYS = {'cases', 'case_details'}


def _run_suite(suite: str) -> dict:
    if suite not in _SUITES:
        raise ValueError(f'Unknown evaluation suite: {suite!r}')
    filename, loader, evaluate = _SUITES[suite]
    cases = loader(_GOLDEN_DIR / filename)
    return evaluate(cases)


def evaluate_suite(suite: str) -> dict:
    """Return only the scalar aggregate metrics for ``suite`` (no per-case data)."""
    report = _run_suite(suite)
    return {key: value for key, value in report.items() if key not in _DETAIL_KEYS}


def _json_safe_report(report: dict) -> dict:
    """Drop the non-serialisable ``cases`` list (dataclass objects in the
    extraction harness); ``case_details`` already carries the serialised form."""
    clean = dict(report)
    clean.pop('cases', None)
    return clean


def run_evaluation(suite: str) -> dict:
    """Run ``suite``, persist the result and return the run envelope.

    Returns ``{run_id, suite, created_at, summary, report}`` where ``summary`` is
    the aggregate scalars and ``report`` keeps ``case_details`` but drops the
    non-serialisable ``cases`` list.
    """
    report = _run_suite(suite)
    summary = {key: value for key, value in report.items() if key not in _DETAIL_KEYS}
    safe_report = _json_safe_report(report)
    run_id = save_run(suite, summary, safe_report)
    stored = get_run(run_id)
    return {
        'run_id': run_id,
        'suite': suite,
        'created_at': stored['created_at'],
        'summary': summary,
        'report': safe_report,
    }
