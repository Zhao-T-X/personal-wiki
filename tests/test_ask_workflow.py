"""Direct fact lookup (task §20/§21/§23): simple facts are answered with 0 LLM.

Isolation mirrors ``tests/test_correction.py``: point ``DATABASE_PATH`` at a temp
file, reload the modules that bind configuration at import time, then
``db.init_db()``. No test here may reach a real model — the direct-lookup path
exists precisely because it does not need one.
"""
from __future__ import annotations

import importlib
import inspect
import os
import sys

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
    'app.repositories',
    'app.resolution',
    'app.knowledge',
    'app.claim_relations',
    'app.service',
    'app.domain.claim_state',
    'app.domain.operations',
    'app.workflows.ask_workflow',
)

# A question that maps to exactly one registered predicate ('defined_as' via the
# '定义' hint) and carries no temporal / multi-hop / research marker.
_QUESTION = 'OpenAI 的定义是什么？'


def _reload(tmp_path):
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


def _seed_claims(tmp_path, claims):
    """One document/chunk, then one CREATE claim per (subject, predicate, object).

    Returns the created claim ids, most recent last.
    """
    _reload(tmp_path)
    from app.db import transaction
    from app.domain.operations import OperationRequest, run
    from app.repositories.document_repo import DocumentRepository

    docs = DocumentRepository()
    doc_id = docs.create(title='Source', content='seeded knowledge')
    chunk = docs.replace_chunks(doc_id, [('seeded knowledge', 0, 0, 16)])[0]
    ids = []
    with transaction() as conn:
        for subject, predicate, obj in claims:
            text = f'{subject} {predicate} {obj}.'
            ids.append(run(OperationRequest(kind='CREATE', payload={
                'subject': subject, 'predicate': predicate, 'object': obj,
                'content': text, 'source_document_id': doc_id,
                'source_chunk_id': chunk['id'], 'source_quote': text}), conn).affected['claim_id'])
    return ids


def _seed_entity(tmp_path, name='OpenAI'):
    """An entity with no claim at all, to prove the lookup is not just 'name seen'."""
    _reload(tmp_path)
    from app.db import transaction
    from app.resolution import resolve_or_create_entity
    with transaction() as conn:
        return resolve_or_create_entity(conn, name=name, entity_types=['Organization'],
                                        aliases=[], description=None, properties={})


def test_one_current_claim_is_answered_without_touching_the_llm(tmp_path, monkeypatch):
    _seed_claims(tmp_path, [('OpenAI', 'defined_as', 'Sam')])

    # Any call into the model would be a bug on this path: make it loud.
    import app.llm as llm
    calls = []

    def _boom(*args, **kwargs):
        calls.append(1)
        raise AssertionError('the direct-lookup path must not call the LLM')

    monkeypatch.setattr(llm, '_client', _boom, raising=False)

    from app.workflows.ask_workflow import ANSWERED, try_direct_answer

    result = try_direct_answer(_QUESTION)

    assert result is not None
    assert result.status == ANSWERED
    assert result.answer_value == 'Sam'
    # The answer is the stored content verbatim, never a rewrite.
    assert result.answer == 'OpenAI defined_as Sam.'
    assert result.claim['predicate'] == 'defined_as'
    assert result.citations and result.evidence
    assert set(result.citations[0]) == {'document_id', 'chunk_id', 'title',
                                        'start_offset', 'end_offset'}
    assert {'chunk_id', 'document_id', 'title', 'quote',
            'start_offset', 'end_offset'} <= set(result.evidence[0])
    assert calls == []

    # Belt and braces: the module simply has no LLM dependency to begin with.
    source = inspect.getsource(sys.modules['app.workflows.ask_workflow'])
    assert 'import llm' not in source and 'app.llm' not in source


def test_two_current_claims_are_reported_as_insufficient_not_guessed(tmp_path):
    _seed_claims(tmp_path, [('OpenAI', 'defined_as', 'Sam'),
                            ('OpenAI', 'defined_as', 'Alice')])

    from app.workflows.ask_workflow import (NO_SUFFICIENT_EVIDENCE, try_direct_answer)

    result = try_direct_answer(_QUESTION)

    assert result is not None
    assert result.status == NO_SUFFICIENT_EVIDENCE
    assert result.reason == 'ambiguous_multiple_current_claims'
    assert result.answer is None and result.answer_value is None and result.claim is None


def test_no_current_claim_is_reported_as_insufficient(tmp_path):
    (claim_id,) = _seed_claims(tmp_path, [('OpenAI', 'defined_as', 'Sam')])
    # A superseded claim is history: answering "Sam" for a present-tense question
    # would be presenting replaced knowledge as current.
    from app.repositories.claim_repo import ClaimRepository
    ClaimRepository().set_status(claim_id, 'superseded')

    from app.workflows.ask_workflow import (NO_SUFFICIENT_EVIDENCE, try_direct_answer)

    result = try_direct_answer(_QUESTION)

    assert result is not None
    assert result.status == NO_SUFFICIENT_EVIDENCE
    assert result.reason == 'no_current_claim'
    assert result.answer is None


def test_entity_without_any_claim_falls_back_to_the_llm_path(tmp_path):
    _seed_entity(tmp_path, 'OpenAI')

    from app.workflows.ask_workflow import try_direct_answer

    # No stored claim means no direct answer: hand the question back to the
    # retrieval + generation path rather than inventing an empty result.
    assert try_direct_answer(_QUESTION) is None


def test_research_question_is_left_to_the_llm_path(tmp_path):
    _seed_claims(tmp_path, [('OpenAI', 'defined_as', 'Sam')])

    from app.workflows.ask_workflow import try_direct_answer

    # '预测'/'未来' are research markers: even a perfect fact-lookup shape must not
    # be answered deterministically.
    assert try_direct_answer('预测 OpenAI 未来的定义会如何变化？') is None


def test_longest_entity_name_wins(tmp_path):
    _seed_claims(tmp_path, [('苹果', 'classified_as', 'Fruit'),
                            ('苹果公司', 'classified_as', 'Company')])

    from app.workflows.ask_workflow import extract_signals

    signals = extract_signals('苹果公司的 CEO 是谁？')

    # "苹果" also occurs in the question, but the longest match must win.
    assert signals.subject == '苹果公司'


def test_explain_reports_signals_and_plan(tmp_path):
    _seed_claims(tmp_path, [('OpenAI', 'defined_as', 'Sam')])

    from app.workflows.ask_workflow import explain

    report = explain(_QUESTION)

    assert report['signals']['subject'] == 'OpenAI'
    assert report['signals']['predicates'] == ['defined_as']
    assert report['signals']['direct_claim_available'] is True
    assert report['plan']['route'] == 'fact_lookup'
    assert report['plan']['llm_expected'] is False
