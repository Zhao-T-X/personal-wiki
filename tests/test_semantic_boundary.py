"""Semantic boundary: Claim Object classification + Object Linking gate (unit + integration).

Verifies the rule the whole fix rests on: a Claim object is only an entity-linking
*candidate* when it is entity-like; a value, a description or an unclassifiable
fragment keeps its text value and is never offered as a subject to attach.
"""
import pytest

from app import config
from app.integrity import link_proposal
from app.object_classification import (CONCEPT, ENTITY, LITERAL, UNKNOWN,
                                       classify_object, is_entity_like)
from app.entity_eligibility import entity_eligibility, is_plausible_subject


# --- unit: classify_object --------------------------------------------------

@pytest.mark.parametrize('text,expected', [
    ('8192', LITERAL),
    ('3.14', LITERAL),
    ('true', LITERAL),
    ('2026', LITERAL),
    ('42%', LITERAL),
    ('5 minutes', LITERAL),
    ('高风险', LITERAL),
    ('每天', LITERAL),
    ('FTS5', ENTITY),
    ('GomsInventoryPagingRequestDTO', ENTITY),
    ('RAG', ENTITY),
    # Concept is the LLM's verdict now (Step 10), not a keyword rule.
    ('indexes raw document titles and bodies', UNKNOWN),
    ('only the selected evidence pack is given to the LLM', UNKNOWN),
    ('', UNKNOWN),
])
def test_classify_object(text, expected):
    assert classify_object(text)[0] == expected


def test_object_kind_from_llm_drives_concept():
    assert classify_object('indexes raw document titles and bodies',
                           object_kind='concept')[0] == CONCEPT


def test_known_entity_wins():
    assert classify_object('8192', known_entity=True)[0] == ENTITY


def test_is_entity_like_predicate():
    assert is_entity_like('FTS5') is True
    assert is_entity_like('8192') is False
    assert is_entity_like('the mechanism that feeds the LLM') is False


def test_plausible_subject_rejects_literals_but_not_descriptions_by_keyword():
    assert is_plausible_subject('苹果公司') is True
    assert is_plausible_subject('FTS5') is True
    assert is_plausible_subject('8192') is False
    # Step 10: whether a phrase is a description is the LLM's verdict, not a keyword reject.
    assert is_plausible_subject('只给 LLM 选定证据包的机制') is True


def test_entity_eligibility_still_drops_structural_garbage():
    assert entity_eligibility('relations', ['Resource'], supported=False)[0] == 'DROP'
    assert entity_eligibility('docs/a.md', ['Resource'], supported=False)[0] == 'DROP'
    assert entity_eligibility('8192', ['Concept'], supported=False)[0] == 'DROP'


# --- integration: Object Linking only proposes entity-like objects -----------

_POOL = [{'id': 'e1', 'name': 'FTS5', 'types': {'Technology'}, 'status': 'verified'},
         {'id': 'e2', 'name': '8192', 'types': {'Technology'}, 'status': 'verified'}]


def _claim(text):
    return {'id': 'c1', 'predicate': 'uses', 'object_text': text,
            'subject_name': 'LLM-Wiki', 'content': ''}


def test_literal_object_is_not_a_link_candidate():
    assert link_proposal(_claim('8192'), {'8192': ['e2']}, _POOL) is None


def test_descriptive_object_is_not_a_link_candidate():
    assert link_proposal(_claim('only the selected evidence pack to the LLM'), {}, _POOL) is None


def test_entity_like_object_can_be_a_candidate():
    proposal = link_proposal(_claim('FTS5'), {'fts5': ['e1']}, _POOL)
    assert proposal is not None
    assert proposal['confidence'] == 'high'
    assert proposal['target']['id'] == 'e1'


def test_gate_disabled_restores_old_behaviour():
    config._OVERRIDES['object_classification_enabled'] = False
    try:
        proposal = link_proposal(_claim('8192'), {'8192': ['e2']}, _POOL)
        assert proposal is not None and proposal['target']['id'] == 'e2'
    finally:
        config._OVERRIDES.pop('object_classification_enabled', None)
