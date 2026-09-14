"""QA evaluation harness — offline, deterministic, no LLM and no DB (task §16/§18).

The golden sets live in ``tests/evaluation/retrieval/golden.json`` and
``tests/evaluation/qa/golden.json``; this module proves the harness scores the
*recorded* retrieval results and answers the way the task requires and that the
datasets clear the acceptance thresholds without an API key or a database.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RETRIEVAL_GOLDEN = ROOT / 'app' / 'evaluation' / 'data' / 'golden' / 'retrieval.json'
QA_GOLDEN = ROOT / 'app' / 'evaluation' / 'data' / 'golden' / 'qa.json'

from app.evaluation.qa_eval import (QaCase, evaluate_qa_case, evaluate_qa_dataset,
                                    load_qa_cases, refusal_kind)
from app.evaluation.retrieval_eval import (evaluate_retrieval_case,
                                           evaluate_retrieval_dataset,
                                           load_retrieval_cases, precision_at_k,
                                           recall_at_k, reciprocal_rank)


# --- fixtures -----------------------------------------------------------------

@pytest.fixture(scope='module')
def retrieval_cases():
    return load_retrieval_cases(RETRIEVAL_GOLDEN)


@pytest.fixture(scope='module')
def retrieval_report(retrieval_cases):
    return evaluate_retrieval_dataset(retrieval_cases)


@pytest.fixture(scope='module')
def qa_cases():
    return load_qa_cases(QA_GOLDEN)


@pytest.fixture(scope='module')
def qa_report(qa_cases):
    return evaluate_qa_dataset(qa_cases)


def _retrieval_case(cases, case_id):
    return next(case for case in cases if case.id == case_id)


def _qa_case(cases, case_id):
    return next(case for case in cases if case.id == case_id)


# --- retrieval metric primitives ---------------------------------------------

def test_retrieval_primitives_handle_zero_denominators():
    assert recall_at_k([], {'a'}, 5) == 0.0
    assert precision_at_k([], {'a'}, 5) == 0.0
    assert reciprocal_rank([], {'a'}) == 0.0

    # recall counts relevant ids, precision counts the window.
    assert recall_at_k(['a', 'b', 'x'], {'a', 'b'}, 5) == 1.0
    assert recall_at_k(['a', 'x', 'x'], {'a', 'b'}, 5) == 0.5
    assert precision_at_k(['a', 'b', 'x'], {'a', 'b'}, 5) == pytest.approx(2 / 3)
    assert precision_at_k(['a', 'x', 'x', 'x', 'x'], {'a'}, 5) == pytest.approx(0.2)

    # k truncates the considered window.
    assert recall_at_k(['x', 'x', 'x', 'x', 'x', 'a'], {'a'}, 5) == 0.0
    assert recall_at_k(['x', 'x', 'x', 'x', 'x', 'a'], {'a'}, 10) == 1.0
    assert reciprocal_rank(['x', 'x', 'a'], {'a'}) == pytest.approx(1 / 3)


def test_precision_uses_short_window_length_not_k():
    # A single result with k=5 must not be penalised for the four it never had.
    assert precision_at_k(['a'], {'a'}, 5) == 1.0


# --- retrieval golden set shape ----------------------------------------------

def test_retrieval_golden_set_shape(retrieval_cases):
    assert len(retrieval_cases) >= 5
    for case in retrieval_cases:
        assert case.id
        assert case.question
        assert case.retrieved_chunk_ids


def test_retrieval_golden_set_covers_the_required_situations(retrieval_cases):
    details = {case.id: evaluate_retrieval_case(case) for case in retrieval_cases}

    # A hit at rank 1.
    assert details['top1-hit']['mrr'] == 1.0
    # A hit past rank 5 that still lands in the top 10: recall@5 is 0, recall@10 > 0.
    assert details['rank-7-hit']['recall_at_5'] == 0.0
    assert details['rank-7-hit']['recall_at_10'] == 1.0
    # A genuine total miss.
    assert details['total-miss']['recall_at_10'] == 0.0
    assert details['total-miss']['relevant_count'] >= 1
    # Multi-relevant and a Chinese question.
    assert details['multi-relevant']['hits_at_5'] == 5
    assert details['chinese-question']['hits_at_5'] == 5


def test_retrieval_dataset_meets_acceptance_thresholds(retrieval_report):
    assert retrieval_report['recall_at_10'] >= 0.95
    assert retrieval_report['recall_at_5'] >= 0.5
    assert retrieval_report['precision_at_5'] >= 0.5
    assert retrieval_report['case_count'] == len(retrieval_report['case_details'])


def test_retrieval_aggregate_is_micro_average(retrieval_report):
    """The aggregate must be tp/fp/fn summed first, not a mean of case ratios."""
    from app.evaluation.metrics import precision, recall
    totals = retrieval_report['totals']
    assert retrieval_report['recall_at_5'] == recall(
        totals['hits_at_5'], totals['relevant'] - totals['hits_at_5'])
    assert retrieval_report['precision_at_5'] == precision(
        totals['hits_at_5'], totals['window_at_5'] - totals['hits_at_5'])


# --- refusal contract (ADR-014: the shared semantics, not a local bool) -------

def test_refusal_contract_recognises_the_api_safety_stop():
    answer = '知识库中没有找到与该问题匹配的证据。请先导入相关文档并建立索引。'
    assert refusal_kind(answer, []) == 'insufficient_evidence'


def test_refusal_contract_classifies_the_other_kinds():
    # Substantive answer with no citations is NOT a refusal.
    assert refusal_kind('The CEO lives at 12 Example Street.', []) == 'non_refusal'
    # Any citation means evidence was offered, so it is never a refusal.
    assert refusal_kind('知识库中没有找到证据', [{'chunk_id': 'x'}]) == 'non_refusal'
    # An inline marker means it is citing, so it is answering.
    assert refusal_kind('See [doc:d chunk:c]', []) == 'non_refusal'
    # An explicit decline is a refusal...
    assert refusal_kind('我无法回答这个问题。', []) == 'refusal'
    # ...while an empty answer is UNKNOWN: it neither refused nor answered.
    assert refusal_kind('', []) == 'unknown'


# --- QA golden set shape ------------------------------------------------------

def test_qa_golden_set_shape(qa_cases):
    assert len(qa_cases) >= 5
    for case in qa_cases:
        assert case.id
        assert case.question
        for citation in case.citations:
            assert citation.get('chunk_id')
            assert citation.get('document_id')


def test_qa_golden_set_has_a_refusal_and_an_incomplete_coverage_case(qa_cases, qa_report):
    cases = {case.id: case for case in qa_cases}
    assert any(case.expected_refusal for case in qa_cases)
    # The harness must actually detect incomplete coverage, not blindly pass it.
    details = {d['id']: d for d in qa_report['case_details']}
    incomplete = details['incomplete-coverage']
    assert incomplete['citation_coverage'] < 1.0
    assert 'uncited_evidence' in incomplete['flags']
    # ...while a fully cited answer reaches full coverage.
    assert details['grounded-single']['citation_coverage'] == 1.0
    assert cases['incomplete-coverage'].expected_refusal is False


def test_qa_case_scores_are_deterministic(qa_cases):
    for case in qa_cases:
        first = evaluate_qa_case(case)
        second = evaluate_qa_case(case)
        assert first == second


def test_qa_support_dimension_is_honest_without_llm(qa_cases, qa_report):
    """No LLM is injected, so ``support`` must be None everywhere — never faked."""
    for detail in qa_report['case_details']:
        assert detail['support'] is None
    report = evaluate_qa_case(_qa_case(qa_cases, 'grounded-single'))
    assert report.support is None
    assert report.dimensions['support'] is None


def test_qa_dataset_meets_acceptance_thresholds(qa_report):
    assert qa_report['citation_coverage'] >= 0.98
    assert qa_report['groundedness'] >= 0.98
    assert qa_report['answer_correctness'] >= 0.9
    assert qa_report['unknown_answer_hallucination_rate'] == 0.0
    assert qa_report['case_count'] == len(qa_report['case_details'])


# --- the hallucination metric actually moves (§40) ---------------------------

def test_unknown_answer_hallucination_is_detected():
    """A no-evidence case answered confidently must be flagged, so the reported
    rate is a real measurement rather than a constant zero."""
    case = QaCase(
        id='synthetic-hallucination',
        question='What is the CEO home address?',
        answer='The CEO lives at 12 Example Street.',
        citations=[],
        expected_answer='',
        expected_refusal=True,
    )
    result = evaluate_qa_case(case)
    assert result.refusal_kind == 'non_refusal'
    assert result.is_safety_stop is False
    assert result.refusal_correct is False
    assert result.answer_correctness == 0.0
    assert result.groundedness < 0.2  # no citations, nothing to ground on

    report = evaluate_qa_dataset([case])
    assert report['unknown_answer_hallucination_rate'] == 1.0


def test_a_correct_refusal_is_rewarded():
    case = QaCase(
        id='synthetic-refusal',
        question='What is the CEO home address?',
        answer='知识库中没有找到与该问题匹配的证据。',
        citations=[],
        expected_answer='',
        expected_refusal=True,
    )
    result = evaluate_qa_case(case)
    # "The knowledge base lacks evidence" is INSUFFICIENT_EVIDENCE, not REFUSAL —
    # it is the §23 behaviour we want, so it earns a correct safety stop.
    assert result.refusal_kind == 'insufficient_evidence'
    assert result.is_safety_stop is True
    assert result.refusal_correct is True
    assert result.citation_coverage == 1.0
    assert result.groundedness == 1.0
    assert evaluate_qa_dataset([case])['unknown_answer_hallucination_rate'] == 0.0


# --- the harness is pure: no API key, no database -----------------------------

def test_evaluation_modules_do_not_import_infrastructure():
    """Same mechanical source guard the domain layer uses (ADR-011)."""
    _INFRA = {'store.py', 'runners.py', 'baseline.py'}
    for path in (ROOT / 'app' / 'evaluation').glob('*.py'):
        if path.name in _INFRA:
            # store.py / runners.py / baseline.py are backend wiring and are
            # explicitly allowed to touch the database; they are checked separately.
            continue
        source = path.read_text(encoding='utf-8')
        for framework in ('fastapi', 'agentscope', 'sqlite3'):
            assert f'import {framework}' not in source, f'{path.name} must not import {framework}'
        assert 'from ..db' not in source and 'from .db' not in source, path.name
        assert 'from app.db' not in source, path.name
        assert 'from ..llm' not in source and 'import llm' not in source, path.name
        assert '.execute(' not in source, path.name


def test_scores_in_a_clean_interpreter_without_keys_or_database(tmp_path):
    """Proves offline operation: fresh interpreter, no keys, unreachable DB."""
    script = (
        "import json\n"
        "from app.evaluation.retrieval_eval import load_retrieval_cases, evaluate_retrieval_dataset\n"
        "from app.evaluation.qa_eval import load_qa_cases, evaluate_qa_dataset\n"
        f"retrieval = evaluate_retrieval_dataset(load_retrieval_cases(r'{RETRIEVAL_GOLDEN}'))\n"
        f"qa = evaluate_qa_dataset(load_qa_cases(r'{QA_GOLDEN}'))\n"
        "print('RETRIEVAL:' + json.dumps({k: retrieval[k] for k in (\n"
        "    'recall_at_5', 'recall_at_10', 'precision_at_5')}))\n"
        "print('QA:' + json.dumps({k: qa[k] for k in (\n"
        "    'citation_coverage', 'groundedness', 'answer_correctness',\n"
        "    'unknown_answer_hallucination_rate', 'case_count')}))\n"
    )
    env = {key: value for key, value in os.environ.items()
           if key not in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'LLM_API_KEY',
                          'DEEPSEEK_API_KEY')}
    env['PYTHONPATH'] = str(ROOT)
    env['DATABASE_PATH'] = str(tmp_path / 'absent.db')
    env['SETTINGS_PATH'] = str(tmp_path / 'absent.json')

    proc = subprocess.run([sys.executable, '-c', script], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding='utf-8',
                          timeout=180)
    assert proc.returncode == 0, proc.stderr

    retrieval_line = next(l for l in proc.stdout.splitlines() if l.startswith('RETRIEVAL:'))
    retrieval = json.loads(retrieval_line[len('RETRIEVAL:'):])
    assert retrieval['recall_at_10'] >= 0.95
    assert retrieval['recall_at_5'] >= 0.5
    assert retrieval['precision_at_5'] >= 0.5

    qa_line = next(l for l in proc.stdout.splitlines() if l.startswith('QA:'))
    qa = json.loads(qa_line[len('QA:'):])
    assert qa['citation_coverage'] >= 0.98
    assert qa['groundedness'] >= 0.98
    assert qa['answer_correctness'] >= 0.9
    assert qa['unknown_answer_hallucination_rate'] == 0.0
