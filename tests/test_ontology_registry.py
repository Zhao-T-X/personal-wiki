"""Predicate Registry v2 — design-time ontology integrity (ADR-015, task §31).

Expanding the ontology is a **design-time** act: a maintainer edits the registry,
and only then may the value be used. The runtime can never do it. These tests pin
both halves of that rule — the registry really does describe ``has_ceo`` as an
Organization→Person relation, and no runtime path can mint a predicate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / 'schemas' / 'claim-predicate-registry.json'


def _registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))


# --- the registry declares the relation --------------------------------------

def test_registry_declares_has_ceo_as_a_domain_relation():
    spec = _registry()['predicate_metadata']['has_ceo']
    assert spec['label'] == '首席执行官'
    assert spec['domain'] == ['Organization']
    assert spec['range'] == ['Person']
    assert spec['functional'] is True
    assert spec['temporal'] is True
    assert spec['evolution'] == 'supersedable'
    assert set(spec['aliases']) >= {'CEO', 'chief executive officer', '首席执行官'}


def test_has_ceo_is_a_registered_claim_predicate():
    registry = _registry()
    assert 'has_ceo' in registry['claim_predicates']


def test_registry_version_was_bumped_and_is_readable():
    from app.ontology import claim_registry_version
    assert _registry()['version'] == '1.3'
    assert claim_registry_version() == '1.3'


def test_metadata_never_describes_an_unregistered_predicate():
    registry = _registry()
    registered = set(registry['claim_predicates'])
    assert set(registry.get('predicate_metadata') or {}) <= registered


# --- labels are the display layer, and nothing else ---------------------------

def test_every_registered_predicate_has_a_human_readable_label():
    """A predicate without a label renders as ``use_of_the_word`` in the UI.

    The import panel and the correction flow read ``label`` straight from here, so a
    predicate added without one silently shows the user an identifier. The rule the
    registry states in its ``_note`` is that a label is display metadata: it never
    participates in a semantic judgement, and code always uses the predicate itself.
    """
    registry = _registry()
    missing = [p for p in registry['claim_predicates']
               if not (registry['predicate_metadata'].get(p) or {}).get('label')]
    assert missing == [], f'predicates without a label: {missing}'


def test_labels_are_display_only_and_did_not_move_the_semantics():
    """Filling in labels must not drag ``functional`` / ``temporal`` / ``evolution`` along.

    ``functional`` drives conflict detection (``compare_claim``) and ``evolution``
    drives supersede advice, so a label pass that touched them would quietly change
    what the product *decides*, not merely what it shows.
    """
    metadata = _registry()['predicate_metadata']
    functional = {p for p, m in metadata.items() if m.get('functional')}
    assert functional == {'is', 'defined_as', 'classified_as', 'has_ceo'}
    assert {p for p, m in metadata.items() if m.get('temporal')} == functional
    assert {p for p, m in metadata.items() if m.get('evolution')} == functional


# --- the registry drives resolution and constraints --------------------------

def test_predicate_spec_exposes_the_v2_fields():
    from app.ontology import claim_predicate_spec

    spec = claim_predicate_spec('has_ceo')
    assert spec is not None
    assert spec.domain == ('Organization',)
    assert spec.range == ('Person',)
    assert spec.label == '首席执行官'
    assert 'CEO' in spec.aliases

    # A predicate with no declared domain/range is simply unconstrained.
    loose = claim_predicate_spec('uses')
    assert loose is not None and loose.domain == () and loose.range == ()

    assert claim_predicate_spec('not_a_predicate') is None


def test_alias_resolution_reads_the_registry_not_a_python_table():
    """``ceo`` resolves only because the registry declares it as an alias."""
    from app.domain.predicate_resolver import resolve_predicate

    resolution = resolve_predicate('ceo')
    assert resolution.predicate == 'has_ceo'
    assert resolution.source == 'registry_alias'


def test_registry_aliases_do_not_collide_with_registered_predicates():
    from app.ontology import CLAIM_PREDICATES, claim_predicate_alias_index

    index = claim_predicate_alias_index()
    assert not (set(index) & set(CLAIM_PREDICATES))
    assert index['ceo'] == 'has_ceo'
    assert index['chief_executive_officer'] == 'has_ceo'


def test_domain_and_range_are_enforced_for_claim_predicates():
    from app.ontology import claim_predicate_endpoint_allowed

    assert claim_predicate_endpoint_allowed(['Organization'], 'has_ceo')
    assert claim_predicate_endpoint_allowed(['Person'], 'has_ceo', target=True)
    assert not claim_predicate_endpoint_allowed(['Location'], 'has_ceo', target=True)
    # An unknown endpoint is unconstrained, not a violation.
    assert claim_predicate_endpoint_allowed([], 'has_ceo', target=True)


# --- the runtime still cannot extend the ontology -----------------------------

def test_runtime_cannot_create_a_predicate():
    from app.domain.ontology_policy import OntologyViolation, require_registered

    for invented in ('new_ceo', 'former_ceo', 'random_relationship'):
        with pytest.raises(OntologyViolation):
            require_registered('claim_predicate', invented)


def test_ontology_maintenance_is_declared_design_time_only():
    from app.domain.ontology_policy import (ONTOLOGY_MUTATION_POLICY,
                                            ONTOLOGY_WRITE_SURFACE)

    assert any('explicit ontology maintenance' in rule
               for rule in ONTOLOGY_MUTATION_POLICY)
    assert ONTOLOGY_WRITE_SURFACE, 'the write surface must be declared explicitly'
