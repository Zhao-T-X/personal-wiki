"""Retrieval evaluation harness — offline, deterministic, no LLM and no DB.

The golden set now lives in ``app/evaluation/data/golden/retrieval.json`` (a single
source of truth shipped with the package, so the API needs no ``tests/``
directory). This module proves the harness scores recorded retrieval results the
way the task requires (§16/§18) and clears the acceptance thresholds offline.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / 'app' / 'evaluation' / 'data' / 'golden' / 'retrieval.json'

from app.evaluation.retrieval_eval import (evaluate_retrieval_case,
                                           evaluate_retrieval_dataset,
                                           load_retrieval_cases, precision_at_k,
                                           recall_at_k, reciprocal_rank)
from app.evaluation.metrics import precision, recall


@pytest.fixture(scope='module')
def cases():
    return load_retrieval_cases(GOLDEN)


@pytest.fixture(scope='module')
def report(cases):
    return evaluate_retrieval_dataset(cases)


def _case(cases, case_id):
    return next(case for case in cases if case.id == case_id)


def test_retrieval_primitives_handle_zero_denominators():
    assert recall_at_k([], {'a'}, 5) == 0.0
    assert precision_at_k([], {'a'}, 5) == 0.0
    assert reciprocal_rank([], {'a'}) == 0.0
    assert recall_at_k(['a', 'b', 'x'], {'a', 'b'}, 5) == 1.0
    assert precision_at_k(['a', 'b', 'x'], {'a', 'b'}, 5) == pytest.approx(2 / 3)
    assert precision_at_k(['a'], {'a'}, 5) == 1.0


def test_retrieval_golden_set_shape(cases):
    assert len(cases) >= 5
    for case in cases:
        assert case.id
        assert case.question
        assert case.retrieved_chunk_ids


def test_retrieval_golden_set_covers_required_situations(cases):
    details = {case.id: evaluate_retrieval_case(case) for case in cases}
    assert details['top1-hit']['mrr'] == 1.0
    assert details['rank-7-hit']['recall_at_5'] == 0.0
    assert details['rank-7-hit']['recall_at_10'] == 1.0
    assert details['total-miss']['recall_at_10'] == 0.0
    assert details['multi-relevant']['hits_at_5'] == 5
    assert details['chinese-question']['hits_at_5'] == 5


def test_retrieval_dataset_meets_acceptance_thresholds(report):
    assert report['recall_at_10'] >= 0.95
    assert report['recall_at_5'] >= 0.5
    assert report['precision_at_5'] >= 0.5
    assert report['case_count'] == len(report['case_details'])


def test_retrieval_aggregate_is_micro_average(report):
    totals = report['totals']
    assert report['recall_at_5'] == recall(
        totals['hits_at_5'], totals['relevant'] - totals['hits_at_5'])
    assert report['precision_at_5'] == precision(
        totals['hits_at_5'], totals['window_at_5'] - totals['hits_at_5'])


def test_evaluation_modules_do_not_import_infrastructure():
    """ADR-011 source guard: the pure harness stays free of infra imports.

    ``store.py`` / ``runners.py`` / ``baseline.py`` are the backend wiring and are
    explicitly allowed to touch the database, so they are excluded from this check.
    """
    _INFRA = {'store.py', 'runners.py', 'baseline.py'}
    for path in (ROOT / 'app' / 'evaluation').glob('*.py'):
        if path.name in _INFRA:
            continue
        source = path.read_text(encoding='utf-8')
        for framework in ('fastapi', 'agentscope', 'sqlite3'):
            assert f'import {framework}' not in source, f'{path.name} must not import {framework}'
        assert 'from ..db' not in source and 'from .db' not in source, path.name
        assert 'from app.db' not in source, path.name
        assert 'from ..llm' not in source and 'import llm' not in source, path.name
        assert '.execute(' not in source, path.name


def test_scores_in_a_clean_interpreter_without_keys_or_database(tmp_path):
    script = (
        "import json\n"
        "from app.evaluation.retrieval_eval import load_retrieval_cases, evaluate_retrieval_dataset\n"
        f"retrieval = evaluate_retrieval_dataset(load_retrieval_cases(r'{GOLDEN}'))\n"
        "print('RETRIEVAL:' + json.dumps({k: retrieval[k] for k in (\n"
        "    'recall_at_5', 'recall_at_10', 'precision_at_5')}))\n"
    )
    env = {key: value for key, value in os.environ.items()
           if key not in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'LLM_API_KEY',
                          'DEEPSEEK_API_KEY')}
    env['PYTHONPATH'] = str(ROOT)
    env['DATABASE_PATH'] = str(tmp_path / 'absent.db')
    env['SETTINGS_PATH'] = str(tmp_path / 'absent.json')
    proc = subprocess.run([sys.executable, '-c', script], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding='utf-8', timeout=180)
    assert proc.returncode == 0, proc.stderr
    line = next(l for l in proc.stdout.splitlines() if l.startswith('RETRIEVAL:'))
    retrieval = json.loads(line[len('RETRIEVAL:'):])
    assert retrieval['recall_at_10'] >= 0.95
    assert retrieval['recall_at_5'] >= 0.5
    assert retrieval['precision_at_5'] >= 0.5
