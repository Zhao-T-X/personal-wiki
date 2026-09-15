"""Anti-hallucination ontology tests — the Knowledge Compilation Pipeline (ADR-011).

These feed the pipeline the exact shapes a model produces when it ignores the
vocabulary rule ("new_ceo", "optimises", "frobnicates") and assert that the
system either canonizes the value or refuses it. It must never let an invented
predicate become knowledge.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claim_entries


def _db(tmp_path):
    """A fresh database for this file; the shared entries need the same one."""
    return claim_entries.prepare_database(tmp_path)


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


def test_temporal_alias_resolves_to_the_registered_domain_predicate():
    """``new_ceo`` is not a predicate: it is ``has_ceo`` plus ``temporal_signal``.

    This is the resolution the Phase 5 loop depends on — the LLM may propose
    ``new_ceo`` and the registry still yields one canonical relation.
    """
    from app.domain.predicate_resolver import resolve_predicate

    resolution = resolve_predicate('new_ceo')
    assert resolution.resolved
    assert resolution.predicate == 'has_ceo'
    assert resolution.temporal_signal == 'new'
    assert resolution.source == 'temporal_split'


def test_unmappable_candidate_has_a_legal_failure_exit():
    """``head_of_company`` must not be *guessed* into ``has_ceo``."""
    from app.domain.predicate_resolver import resolve_predicate

    resolution = resolve_predicate('head_of_company', text='苹果公司的负责人是谁。')
    assert resolution.status == 'unresolved'
    assert resolution.predicate is None
    assert 'head_of_company' not in (resolution.candidates or ())

    # An unmappable candidate still keeps its temporal signal instead of losing it.
    invented = resolve_predicate('new_head_of_company')
    assert invented.status == 'unresolved'
    assert invented.predicate is None
    assert invented.temporal_signal == 'new'

    assert resolve_predicate('random_relationship').status == 'unresolved'


# --- Compiler: draft -> canonical, or refusal --------------------------------

def test_compiler_refuses_unresolved_candidate():
    from app.domain.compiler import KnowledgeCompiler, ClaimDraft, UNRESOLVED

    result = KnowledgeCompiler().compile_claim(
        ClaimDraft(subject='Apple', predicate_candidate='random_relationship',
                   object='John Ternus'))
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


def test_unknown_endpoint_type_is_unconstrained_not_a_violation():
    """A side whose entity type was never declared imposes no constraint.

    Regression guard: the compiler used to pass ``['*']`` as the *actual* type
    of the unknown side, and ``canonical_entity_type('*')`` rejects ``'*'`` — so
    every relation with one undeclared endpoint was wrongly rejected.
    """
    from app.domain.compiler import COMPILED, REJECTED, ClaimDraft, KnowledgeCompiler

    compiler = KnowledgeCompiler()
    unknown_object = compiler.compile_claim(ClaimDraft(
        subject='GPT-4', predicate_candidate='trained_on', object='MMLU',
        subject_types=['Model'], object_types=[]))
    assert unknown_object.status == COMPILED

    # The declared side is still validated: a Model-only predicate cannot be
    # applied to a Technology subject merely because the object is unknown.
    wrong_subject = compiler.compile_claim(ClaimDraft(
        subject='Transformer', predicate_candidate='trained_on', object='MMLU',
        subject_types=['Technology'], object_types=[]))
    assert wrong_subject.status == REJECTED
    assert 'domain_range_violation' in wrong_subject.reasons


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
                              predicate='random_relationship', object='约翰·特努斯')
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


# --- Every claim entry agrees on the same draft (ADR-011) ---------------------
#
# The entries, the drafts and the database setup live in ``claim_entries``, because
# ``test_architecture.py`` asserts the same property against the same entries. Two
# definitions of "an entry" would defeat the purpose of a test that checks there is
# only one implementation of claim semantics.

@pytest.mark.parametrize('entry', sorted(claim_entries.ENTRIES))
def test_every_claim_entry_compiles_one_draft_identically(tmp_path, entry):
    """ADR-011: one implementation of claim semantics, so the routes must converge.

    The same Claim Draft goes through each claim-producing entry, and the canonical
    claim must come out byte-identical — with the invented ``new_ceo`` resolved to
    ``has_ceo`` and ``new`` moved into ``temporal_signal``. This fails the moment a
    route grows its own resolution, temporal handling or defaulting again, which is
    exactly how the extraction path came to skip the domain/range gate unnoticed.
    """
    claim_entries.prepare_database(tmp_path)
    claim_entries.seed_entities(claim_entries.ENTITIES)
    from app.domain.compiler import ClaimDraft, KnowledgeCompiler

    draft = claim_entries.DRAFT
    reference = KnowledgeCompiler().compile_claim(ClaimDraft(**draft)).claim
    assert reference is not None
    assert reference.predicate == 'has_ceo'      # the invented predicate is gone
    assert reference.temporal_signal == 'new'    # the meaning is modelled as time

    assert claim_entries.ENTRIES[entry](draft, claim_entries.ENTITIES) == reference.to_dict()


@pytest.mark.parametrize('entry', sorted(claim_entries.ENTRIES))
def test_every_claim_entry_refuses_one_illegal_pairing(tmp_path, entry):
    """The domain/range gate is one gate, so every route refuses the same pairing.

    ``苹果公司 has_ceo 加利福尼亚`` resolves to a registered predicate and is still
    illegal: an Organization's CEO is a Person. Extraction used to compile it
    without ever asking, because the gate lived only on the paths that happened to
    call it.
    """
    claim_entries.prepare_database(tmp_path)
    claim_entries.seed_entities(claim_entries.ILLEGAL_ENTITIES)

    with pytest.raises(ValueError) as exc:
        claim_entries.ENTRIES[entry](claim_entries.ILLEGAL_DRAFT,
                                     claim_entries.ILLEGAL_ENTITIES)
    message = str(exc.value).replace('domain/range', 'domain_range')
    assert 'domain_range' in message


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


# --- Quality Gate is wired into the compiler (task §14/§15) -------------------

def test_compiler_accepts_a_fully_grounded_claim():
    from app.domain.compiler import COMPILED, ClaimDraft, KnowledgeCompiler
    from app.domain.quality_gate import QualitySignals

    grounded = QualitySignals(
        ontology_valid=True, schema_complete=True, entity_resolved=True,
        evidence_found=True, quote_exact=True, no_contradiction=True)
    result = KnowledgeCompiler().compile_claim(
        ClaimDraft(subject='RAG', predicate_candidate='improves', object='Accuracy'),
        quality=grounded)
    assert result.status == COMPILED
    assert result.quality is not None
    assert result.quality.verdict == 'accept'


def test_quality_gate_vetoes_an_ungrounded_claim():
    """A legal, well-formed claim with no evidence is still not knowledge.

    Note ``llm_confidence=1.0``: the model's own certainty cannot rescue it.
    """
    from app.domain.compiler import REJECTED, ClaimDraft, KnowledgeCompiler
    from app.domain.quality_gate import QualitySignals

    ungrounded = QualitySignals(
        ontology_valid=True, schema_complete=False, entity_resolved=False,
        evidence_found=False, quote_exact=False, no_contradiction=True,
        llm_confidence=1.0)
    result = KnowledgeCompiler().compile_claim(
        ClaimDraft(subject='RAG', predicate_candidate='improves', object='Accuracy'),
        quality=ungrounded)
    assert result.status == REJECTED
    assert result.claim is None
    assert 'quality_gate_rejected' in result.reasons


def test_quality_gate_routes_mid_quality_to_review():
    from app.domain.compiler import COMPILED, ClaimDraft, KnowledgeCompiler
    from app.domain.quality_gate import QualitySignals

    mid = QualitySignals(
        ontology_valid=True, schema_complete=True, entity_resolved=True,
        evidence_found=False, quote_exact=False, no_contradiction=True)
    result = KnowledgeCompiler().compile_claim(
        ClaimDraft(subject='RAG', predicate_candidate='improves', object='Accuracy'),
        quality=mid)
    assert result.status == COMPILED          # review still compiles, but is flagged
    assert 'quality_gate_review' in result.reasons
    assert result.quality.verdict == 'review'
