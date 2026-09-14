"""Anti-hallucination ontology tests — the Knowledge Compilation Pipeline (ADR-011).

These feed the pipeline the exact shapes a model produces when it ignores the
vocabulary rule ("new_ceo", "optimises", "frobnicates") and assert that the
system either canonizes the value or refuses it. It must never let an invented
predicate become knowledge.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD = (
    'app.repositories.base',
    'app.repositories.document_repo',
    'app.repositories.entity_repo',
    'app.repositories.claim_repo',
    'app.repositories.relation_repo',
    'app.repositories.evidence_repo',
    'app.repositories.event_repo',
    'app.repositories.idea_repo',
    'app.repositories.question_repo',
    'app.repositories.research_repo',
    'app.repositories.run_repo',
    'app.repositories.catalog_repo',
    'app.repositories.operation_repo',
    'app.resolution',
    'app.knowledge',
    'app.claim_relations',
    'app.service',
    'app.domain.claim_state',
    'app.domain.operations',
    'app.domain.predicate_resolver',
    'app.domain.compiler',
    'app.workflows.correction_workflow',
)


def _db(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config)
    importlib.reload(db)
    for name in _RELOAD:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


# --- ONTOLOGY MUTATION POLICY ------------------------------------------------

def test_policy_states_the_eight_rules():
    from app.domain.ontology_policy import ONTOLOGY_MUTATION_POLICY, policy_text

    assert len(ONTOLOGY_MUTATION_POLICY) == 8
    assert 'Unresolved predicates never become persisted knowledge.' in ONTOLOGY_MUTATION_POLICY
    assert 'ONTOLOGY MUTATION POLICY' in policy_text()


def test_registry_is_closed():
    from app.domain.ontology_policy import (OntologyViolation, is_registered,
                                            require_registered)

    assert is_registered('claim_predicate', 'improves')
    assert not is_registered('claim_predicate', 'new_ceo')
    with pytest.raises(OntologyViolation):
        require_registered('claim_predicate', 'new_ceo')


# --- Temporal signal is not a predicate --------------------------------------

def test_temporal_signal_is_split_out_of_the_candidate():
    from app.domain.predicate_resolver import split_temporal_signal

    assert split_temporal_signal('new_ceo') == ('ceo', 'new')
    assert split_temporal_signal('ceo_new') == ('ceo', 'new')
    assert split_temporal_signal('former_ceo') == ('ceo', 'former')
    assert split_temporal_signal('新任CEO') == ('CEO', 'new')
    assert split_temporal_signal('improves') == ('improves', None)


# --- Resolver: RESOLVED / AMBIGUOUS / UNRESOLVED -----------------------------

def test_registered_predicates_resolve_to_themselves():
    from app.domain.predicate_resolver import resolve_predicate
    from app.ontology import CLAIM_PREDICATES

    for predicate in CLAIM_PREDICATES:
        resolution = resolve_predicate(predicate)
        assert resolution.resolved, predicate
        assert resolution.predicate == predicate


def test_alias_candidate_is_canonicalized():
    from app.domain.predicate_resolver import resolve_predicate

    resolution = resolve_predicate('optimises')
    assert resolution.status == 'resolved'
    assert resolution.predicate == 'improves'
    assert resolution.source == 'alias'


def test_temporal_candidate_keeps_predicate_and_captures_signal():
    from app.domain.predicate_resolver import resolve_predicate

    resolution = resolve_predicate('new_improves')
    assert resolution.resolved
    assert resolution.predicate == 'improves'      # one relation, not a new term
    assert resolution.temporal_signal == 'new'


def test_unmappable_candidate_has_a_legal_failure_exit():
    """``new_ceo`` must not become ``new_ceo``: it stays unresolved."""
    from app.domain.predicate_resolver import resolve_predicate

    resolution = resolve_predicate('new_ceo', text='苹果公司的新任 CEO 是约翰·特努斯。')
    assert resolution.status == 'unresolved'
    assert resolution.predicate is None
    assert resolution.temporal_signal == 'new'     # the signal is not lost
    assert 'new_ceo' not in (resolution.candidates or ())

    invented = resolve_predicate('frobnicates')
    assert invented.status == 'unresolved'
    assert invented.predicate is None


# --- Compiler: draft -> canonical, or refusal --------------------------------

def test_compiler_refuses_unresolved_candidate():
    from app.domain.compiler import KnowledgeCompiler, ClaimDraft, UNRESOLVED

    result = KnowledgeCompiler().compile_claim(
        ClaimDraft(subject='Apple', predicate_candidate='new_ceo', object='John Ternus'))
    assert result.status == UNRESOLVED
    assert result.claim is None
    assert result.reasons


def test_compiler_produces_canonical_claim_for_alias():
    from app.domain.compiler import KnowledgeCompiler, ClaimDraft, COMPILED

    result = KnowledgeCompiler().compile_claim(
        ClaimDraft(subject='RAG', predicate_candidate='optimises', object='Accuracy'))
    assert result.status == COMPILED
    assert result.claim.predicate == 'improves'


def test_compiler_enforces_domain_and_range():
    from app.domain.compiler import KnowledgeCompiler, ClaimDraft, COMPILED, REJECTED

    compiler = KnowledgeCompiler()
    legal = compiler.compile_claim(ClaimDraft(
        subject='GPT-4', predicate_candidate='trained_on', object='MMLU',
        subject_types=['Model'], object_types=['Dataset']))
    assert legal.status == COMPILED

    illegal = compiler.compile_claim(ClaimDraft(
        subject='GPT-4', predicate_candidate='trained_on', object='Transformer',
        subject_types=['Technology'], object_types=['Technology']))
    assert illegal.status == REJECTED
    assert illegal.claim is None
    assert 'domain_range_violation' in illegal.reasons


# --- Extraction path obeys the pipeline --------------------------------------

def test_extraction_carries_temporal_signal_instead_of_a_new_predicate(tmp_path):
    _db(tmp_path)
    from app.extraction import normalize_extraction

    draft = {
        'entities': [{'name': 'RAG', 'types': ['Concept']},
                     {'name': 'Accuracy', 'types': ['Concept']}],
        'claims': [{'subject': 'RAG', 'predicate': 'new_improves', 'object': 'Accuracy',
                    'content': 'RAG now improves accuracy.', 'evidence_quote': 'RAG now improves accuracy.',
                    'confidence': 0.9, 'source_chunk': 'chunk-1'}],
        'events': [], 'ideas': [], 'questions': [],
    }
    out = normalize_extraction(draft)
    assert out['claims'][0]['predicate'] == 'improves'
    assert out['claims'][0]['temporal_signal'] == 'new'


# --- Correction path obeys the pipeline --------------------------------------

def test_correction_apply_refuses_unregistered_predicate():
    from app.domain.operations import OperationError
    from app.workflows.correction_workflow import (CorrectionIntent, CorrectionPlan,
                                                   apply_correction)

    intent = CorrectionIntent(text='苹果公司的新任 CEO 是约翰·特努斯。', subject='苹果公司',
                              predicate='new_ceo', object='约翰·特努斯')
    plan = CorrectionPlan(text=intent.text, intent=intent, subject_entity_id=None,
                          relationship='new', related_claim_id=None, apply_supersede=False)
    with pytest.raises(OperationError):
        apply_correction(plan)


def test_correction_plan_is_blocked_when_predicate_is_unresolved(tmp_path):
    _db(tmp_path)
    from app.workflows.correction_workflow import CorrectionIntent, build_plan

    intent = CorrectionIntent(
        text='苹果公司的新任 CEO 是约翰·特努斯。', subject='苹果公司', predicate='',
        predicate_candidate='new_ceo',
        predicate_resolution={'status': 'unresolved', 'candidates': []})
    plan = build_plan(intent.text, intent)
    assert plan.blocked is True
    assert plan.relationship == 'unresolved'
    assert '受控谓词' in plan.summary


# --- Predicate semantics are registry data, not Python literals (task §31) ----

def test_predicate_semantics_come_from_the_registry():
    from app.claim_relations import FUNCTIONAL_PREDICATES
    from app.ontology import CLAIM_PREDICATES, claim_predicate_spec

    # The set is derived from the registry and can never contain a non-predicate.
    assert FUNCTIONAL_PREDICATES
    assert FUNCTIONAL_PREDICATES <= CLAIM_PREDICATES

    spec = claim_predicate_spec('defined_as')
    assert spec is not None
    assert spec.functional is True
    assert spec.temporal is True
    assert spec.evolution == 'supersedable'

    assert claim_predicate_spec('uses').functional is False
    assert claim_predicate_spec('frobnicates') is None    # unregistered -> no spec
