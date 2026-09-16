"""Entity Eligibility + Extraction Before/After experiment (task: real pipeline).

Covers:
* unit: deterministic eligibility rules (entity_eligibility, is_plausible_subject)
* integration: the gate runs inside persist_extraction on the real DB path
* e2e: the Golden Corpus run_experiment() drives create_document -> write_chunks ->
  normalize_extraction -> persist_extraction with the gate off (Before) and on (After)
  and asserts noise goes down while recall stays intact.
"""
import pytest

import claim_entries
from app.entity_eligibility import entity_eligibility, is_plausible_subject
from app.experiments import load_corpus, run_golden_document, run_experiment

NEGATIVES = ['relations', 'claim_relations', '出库点检日志',
             'docs/design/context-runtime-design.md']


@pytest.fixture(scope='module', autouse=True)
def _db(tmp_path_factory):
    # A dedicated, empty database: the golden-corpus metrics must not be contaminated
    # by entities other test files seeded into the shared session database.
    claim_entries.prepare_database(tmp_path_factory.mktemp('golden'))


# --- unit: pure deterministic rules ------------------------------------------

def test_file_path_is_dropped():
    assert entity_eligibility('docs/design/context-runtime-design.md', ['Resource'], supported=False)[0] == 'DROP'
    assert entity_eligibility('a/b/c.py', ['Resource'], supported=False)[0] == 'DROP'


def test_relation_name_is_dropped():
    assert entity_eligibility('relations', ['Resource'], supported=False)[0] == 'DROP'
    assert entity_eligibility('claim_relations', ['Resource'], supported=False)[0] == 'DROP'
    assert entity_eligibility('库存字段', ['Concept'], supported=False)[0] == 'DROP'


def test_log_suffix_is_dropped():
    assert entity_eligibility('出库点检日志', ['Concept'], supported=False)[0] == 'DROP'


def test_modifier_is_dropped():
    assert entity_eligibility('现在', ['Concept'], supported=False)[0] == 'DROP'


def test_technical_without_claim_is_review():
    assert entity_eligibility('GomsOrderPagingRequestDTO', ['Technology'], supported=False)[0] == 'REVIEW'


def test_technical_with_claim_is_keep():
    assert entity_eligibility('GomsOrderPagingRequestDTO', ['Technology'], supported=True)[0] == 'KEEP'


def test_is_plausible_subject():
    assert is_plausible_subject('苹果公司') is True
    assert is_plausible_subject('GomsInventoryPagingRequestDTO') is True
    assert is_plausible_subject('relations') is False
    assert is_plausible_subject('docs/a.md') is False
    assert is_plausible_subject('现在') is False


def test_gate_disabled_passes_through():
    from app import config
    config._OVERRIDES['extraction_eligibility_enabled'] = False
    try:
        assert entity_eligibility('relations', ['Resource'], supported=False)[0] == 'KEEP'
        assert is_plausible_subject('relations') is True
    finally:
        config._OVERRIDES.pop('extraction_eligibility_enabled', None)


# --- integration: gate inside the real persist path --------------------------

def _goms_doc():
    return next(d for d in load_corpus()['documents'] if d['id'] == 'goms_api')


def test_before_run_includes_negatives():
    r = run_golden_document(_goms_doc(), eligibility_enabled=False)
    assert 'error' not in r, r.get('error')
    kept = {p['name'] for p in r['persisted']}
    # Old behavior: every proposed entity (incl. garbage) reached the pool.
    for n in NEGATIVES:
        assert n in kept, f'{n} should leak in Before run'


def test_after_run_excludes_negatives():
    r = run_golden_document(_goms_doc(), eligibility_enabled=True)
    assert 'error' not in r, r.get('error')
    kept = {p['name'] for p in r['persisted']}
    for n in NEGATIVES:
        assert n not in kept, f'{n} should be excluded by the gate'
    # Legit entities (DTOs referenced by claims, 库存分页查询, 库存, 库存查询能力) survive.
    for n in ['GomsInventoryPagingRequestDTO', '库存分页查询', '库存']:
        assert n in kept, f'{n} should survive the gate'


# --- e2e: full Golden Corpus Before/After ------------------------------------

def test_keep_entities_are_marked_and_kept_out_of_the_review_queue():
    """Step 9: eligibility-accepted entities are not review work."""
    from app.repositories import EntityRepository
    run_golden_document(_goms_doc(), eligibility_enabled=True)
    repo = EntityRepository()
    dto = repo.by_name('GomsInventoryPagingRequestDTO')
    assert dto is not None
    assert repo.get(dto['id'])['properties'].get('eligibility') == 'keep'
    # Every entity in this fresh golden DB is claim-backed => the review queue is empty.
    assert repo.candidates(200) == []


def test_run_experiment_metrics():
    report = run_experiment()
    assert not report.get('errors'), report.get('errors')
    agg = report['aggregate']
    assert agg['precision']['after'] == 1.0, agg
    assert agg['recall']['after'] == 1.0, agg
    # Noise clearly went down.
    assert agg['dropped_entities']['after'] >= 2, agg
    # Every document: negatives excluded in After.
    for d in report['documents']:
        after_kept = set(d['after']['kept'])
        for n in NEGATIVES:
            assert n not in after_kept
        # recall of expected entities stays at 1.0 per doc
        assert d['after']['recall'] == 1.0, d
