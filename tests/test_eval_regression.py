"""Evaluation regression baselines (offline, deterministic).

For each suite (extraction / qa / retrieval) this loads the hand-checked golden
set, runs the *real* offline eval harness (``evaluate_dataset`` family) and
compares the aggregated ``summary`` metrics against the recorded baseline in
``tests/evaluation/baselines/{suite}.json``.

Any metric that drops below its baseline by more than ``TOLERANCE`` is a
regression and fails the test — this is the guard that stops a refactoring of the
pipeline / validator / scoring from silently degrading quality.

Golden data is read from ``app/evaluation/data/golden/{suite}.json`` when present
(single source of truth), otherwise from the legacy
``tests/evaluation/{suite}/golden.json`` path. Baselines are produced by
``make eval-baseline`` (``python -m app.evaluation.baseline``); if a baseline file
is missing the test skips with a hint rather than erroring.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASELINE_DIR = ROOT / 'tests' / 'evaluation' / 'baselines'

# Below this drop the metric is treated as unchanged (floating point noise).
TOLERANCE = 1e-6

from app.evaluation.extraction_eval import load_cases, evaluate_dataset
from app.evaluation.qa_eval import load_qa_cases, evaluate_qa_dataset
from app.evaluation.retrieval_eval import (load_retrieval_cases,
                                           evaluate_retrieval_dataset)

# suite -> (loader, evaluator, aggregated metric keys)
SUITES = {
    'extraction': (
        load_cases, evaluate_dataset,
        ('entity_precision', 'entity_recall', 'claim_precision', 'claim_recall',
         'evidence_accuracy', 'ontology_violation_rate'),
    ),
    'qa': (
        load_qa_cases, evaluate_qa_dataset,
        ('citation_coverage', 'groundedness', 'answer_correctness',
         'unknown_answer_hallucination_rate'),
    ),
    'retrieval': (
        load_retrieval_cases, evaluate_retrieval_dataset,
        ('recall_at_5', 'recall_at_10', 'precision_at_5', 'mrr'),
    ),
}


def _golden_path(suite: str) -> Path:
    new = ROOT / 'app' / 'evaluation' / 'data' / 'golden' / f'{suite}.json'
    if new.exists():
        return new
    return ROOT / 'tests' / 'evaluation' / suite / 'golden.json'


@pytest.mark.parametrize('suite', sorted(SUITES))
def test_eval_metrics_do_not_regress(suite):
    baseline_path = BASELINE_DIR / f'{suite}.json'
    if not baseline_path.exists():
        pytest.skip(
            f'baseline missing for {suite}: run `make eval-baseline` '
            f'(or `python -m app.evaluation.baseline`) to generate it'
        )

    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    loader, evaluator, keys = SUITES[suite]

    cases = loader(_golden_path(suite))
    report = evaluator(cases)

    missing = [key for key in keys if key not in report]
    assert not missing, f'{suite} report is missing keys: {missing}'

    baseline_metrics = baseline.get('metrics', {})
    regressions = {}
    for key in keys:
        if key not in baseline_metrics:
            continue  # unknown baseline key: nothing to compare against
        expected = baseline_metrics[key]
        actual = report[key]
        if actual < expected - TOLERANCE:
            regressions[key] = (actual, expected)

    assert not regressions, (
        f'[{suite}] metrics regressed beyond tolerance {TOLERANCE}: '
        + ', '.join(f'{k}={a} < {e}' for k, (a, e) in regressions.items())
    )
