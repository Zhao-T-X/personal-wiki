"""Extraction evaluation harness — offline, deterministic, no LLM and no DB.

The golden set lives in ``tests/evaluation/extraction/golden.json``; this module
checks that the harness scores the real Knowledge Compilation Pipeline the way
the task requires (§16/§17) and that the dataset clears the acceptance
thresholds without an API key or a database.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / 'tests' / 'evaluation' / 'extraction' / 'golden.json'

from app.domain.compiler import COMPILED, UNRESOLVED
from app.evaluation.extraction_eval import (compile_draft, evaluate_dataset,
                                            load_cases)
from app.evaluation.metrics import f1, precision, recall
from app.ontology import CLAIM_PREDICATES

REQUIRED_CASES = {
    'apple-ceo-succession',
    'alias-canonicalization',
    'unmappable-predicate',
    'clean-registered',
    'entity-type-violation',
    'multi-claim',
}


@pytest.fixture(scope='module')
def cases():
    return load_cases(GOLDEN)


@pytest.fixture(scope='module')
def report(cases):
    return evaluate_dataset(cases)


def _case(cases, case_id):
    return next(case for case in cases if case.id == case_id)


# --- metrics primitives -------------------------------------------------------

def test_metrics_handle_zero_denominators():
    assert precision(0, 0) == 0.0
    assert recall(0, 0) == 0.0
    assert f1(0.0, 0.0) == 0.0
    assert precision(1, 1) == 0.5
    assert recall(3, 1) == 0.75
    assert f1(0.5, 1.0) == pytest.approx(2.0 / 3.0)


# --- golden set shape ---------------------------------------------------------

def test_golden_set_has_the_required_cases(cases):
    assert len(cases) >= 6
    assert {case.id for case in cases} >= REQUIRED_CASES
    for case in cases:
        assert case.source_text
        assert isinstance(case.model_draft.get('claims'), list)
        assert isinstance(case.model_draft.get('entities'), list)


# --- dataset-level acceptance thresholds -------------------------------------

def test_dataset_meets_acceptance_thresholds(report):
    assert report['ontology_violation_rate'] == 0.0
    assert report['claim_precision'] >= 0.9
    assert report['claim_recall'] >= 0.8
    assert report['entity_precision'] >= 0.9
    assert report['evidence_accuracy'] >= 0.95
    assert report['case_count'] == len(report['cases'])


def test_no_golden_case_silently_loses_an_expected_claim(report):
    """A registered, well-formed claim must compile - otherwise the recall floor
    is being met by a case that quietly contributes nothing."""
    missed = {detail['id']: detail['claim_fn']
              for detail in report['case_details'] if detail['claim_fn']}
    assert missed == {}, f'expected claims that did not compile: {missed}'


# --- the invented predicate never becomes knowledge (§41) ---------------------

def test_apple_case_never_compiles_an_invented_predicate(cases):
    case = _case(cases, 'apple-ceo-succession')
    draft = compile_draft(case)

    assert len(draft.results) == 1
    result = draft.results[0]
    assert result is not None
    assert result.status == UNRESOLVED
    assert result.status != COMPILED
    assert result.claim is None
    assert result.resolution.status == 'unresolved'
    assert result.resolution.predicate is None

    compiled_predicates = [r.claim.predicate for r in draft.results
                           if r is not None and r.claim is not None]
    assert 'new_ceo' not in compiled_predicates


# --- alias canonicalisation ---------------------------------------------------

def test_alias_case_canonicalises_optimises_to_improves(cases):
    case = _case(cases, 'alias-canonicalization')
    draft = compile_draft(case)

    compiled = [r for r in draft.results if r is not None and r.status == COMPILED]
    assert [r.claim.predicate for r in compiled] == ['improves']
    assert compiled[0].resolution.source == 'alias'


# --- unregistered entity type is dropped, not raised --------------------------

def test_unregistered_entity_type_is_counted_and_dropped(cases):
    case = _case(cases, 'entity-type-violation')
    draft = compile_draft(case)  # must not raise

    assert draft.entity_type_violations >= 1
    # The unregistered type means the entity has no ontology grounding.
    assert 'acme corp' not in draft.entities
    # A registered entity in the same draft survives.
    assert 'throughput' in draft.entities


# --- every compiled claim stays inside the closed registry --------------------

def test_every_compiled_claim_predicate_is_registered(cases):
    for case in cases:
        draft = compile_draft(case)
        for claim in draft.compiled_claims:
            assert claim.predicate in CLAIM_PREDICATES, (case.id, claim.predicate)


# --- the harness is pure: no API key, no database -----------------------------

def test_evaluation_modules_do_not_import_infrastructure():
    """Same mechanical source guard the domain layer uses (ADR-011)."""
    for path in (ROOT / 'app' / 'evaluation').glob('*.py'):
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
        "from app.evaluation.extraction_eval import load_cases, evaluate_dataset\n"
        f"cases = load_cases(r'{GOLDEN}')\n"
        "report = evaluate_dataset(cases)\n"
        "print('REPORT:' + json.dumps({k: report[k] for k in (\n"
        "    'entity_precision', 'entity_recall', 'claim_precision', 'claim_recall',\n"
        "    'evidence_accuracy', 'ontology_violation_rate')}))\n"
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

    line = next(line for line in proc.stdout.splitlines() if line.startswith('REPORT:'))
    data = json.loads(line[len('REPORT:'):])
    assert data['ontology_violation_rate'] == 0.0
    assert data['claim_precision'] >= 0.9
    assert data['claim_recall'] >= 0.8
    assert data['evidence_accuracy'] >= 0.95
