"""Phase 3 — the write entry points reach knowledge only through Operations.

The architecture rule (ARCHITECTURE-GAP.md §3) is: any API that changes current
knowledge must enter ``domain.operations.run``. Three routes used to bypass it —
the status PATCH, the claim-relation PATCH and the entity merge — writing the
repositories directly from the application layer. This file proves they no longer
do: after each call an Operation record exists, and the lifecycle change is real.
"""
import pytest

import claim_entries
from app.db import transaction
from app.repositories import (ClaimRepository, EntityRepository, IdeaRepository,
                              OperationRepository, QuestionRepository)


@pytest.fixture
def db(tmp_path):
    return claim_entries.prepare_database(tmp_path)


@pytest.fixture
def ops(db):
    """The application operation module, imported *after* ``prepare_database`` so its
    cached ``OperationError`` / ``run`` match the reloaded ``domain.operations``
    (the reload swaps in fresh class objects; production never reloads)."""
    import importlib
    import app.application.operation as m
    importlib.reload(m)
    return m


def _seed_entity(name='实体A'):
    from app.resolution import resolve_or_create_entity
    with transaction() as tx:
        return resolve_or_create_entity(tx, name=name, entity_types=['Resource'],
                                        aliases=[], description=None, properties={})


def _seed_claim(subject='苹果公司', predicate='has_ceo', obj='蒂姆·库克'):
    from app.domain.operations import OperationRequest, run
    from app.repositories.document_repo import DocumentRepository
    with transaction() as tx:
        docs = DocumentRepository(tx)
        doc_id = docs.create(title=f'{subject}简介', content=f'{subject}的{predicate}是{obj}。',
                             source_type='note')
        chunk = docs.replace_chunks(doc_id, [(f'{subject}的{predicate}是{obj}。', 0, 0, 20)])[0]
        return run(OperationRequest(kind='CREATE', payload={
            'subject': subject, 'predicate': predicate, 'object': obj,
            'content': f'{subject}的{predicate}是{obj}。', 'source_document_id': doc_id,
            'source_chunk_id': chunk['id'], 'source_quote': f'{subject}的{predicate}是{obj}。',
        }), tx).affected['claim_id']


def _operation_kinds(*kinds):
    rows = OperationRepository().list(200)
    return [r for r in rows if r['kind'] in kinds]


# --- status PATCH ---------------------------------------------------------- #

def test_status_patch_archives_and_restores_through_operation(ops, db):
    eid = _seed_entity()
    out = ops.update_knowledge_status('entity', eid, 'archived')
    assert out == {'id': eid, 'status': 'archived'}
    assert EntityRepository().get(eid)['status'] == 'archived'
    arch = [r for r in _operation_kinds('ARCHIVE') if r['payload'].get('item_id') == eid]
    assert arch, 'ARCHIVE operation must be recorded'
    assert arch[0]['payload']['kind'] == 'entity'

    ops.update_knowledge_status('entity', eid, 'verified')
    assert EntityRepository().get(eid)['status'] == 'verified'
    rest = [r for r in _operation_kinds('RESTORE') if r['payload'].get('item_id') == eid]
    assert rest, 'RESTORE operation must be recorded'
    assert rest[0]['payload']['status'] == 'verified'


def test_status_patch_archive_claim_through_operation(ops, db):
    cid = _seed_claim()
    ops.update_knowledge_status('claim', cid, 'archived')
    assert ClaimRepository().status_of(cid) == 'archived'
    assert any(r['payload'].get('item_id') == cid
               for r in _operation_kinds('ARCHIVE'))


def test_status_patch_rejects_bad_status(ops, db):
    eid = _seed_entity()
    with pytest.raises(ops.ValidationError):
        ops.update_knowledge_status('entity', eid, 'not_a_status')


def test_status_patch_missing_object_is_not_found(ops, db):
    with pytest.raises(ops.NotFoundError):
        ops.update_knowledge_status('entity', 'does-not-exist', 'archived')


# --- claim-relation PATCH -------------------------------------------------- #

def test_relation_resolution_through_operation(ops, db):
    cid1 = _seed_claim(obj='A')
    cid2 = _seed_claim(obj='B')
    with transaction() as tx:
        rel_id = ClaimRepository(tx).insert_relation(
            source_claim_id=cid1, target_claim_id=cid2, relationship='duplicate',
            confidence=0.9, reason='test', suggested_action='link_evidence',
            status='candidate', created_by='user')
    out = ops.resolve_claim_relation(rel_id, {'status': 'accepted',
                                              'relationship': 'supersedes'})
    assert out == {'id': rel_id, 'status': 'accepted', 'relationship': 'supersedes'}
    rel = ClaimRepository().relation(rel_id)
    assert rel['status'] == 'accepted' and rel['relationship'] == 'supersedes'
    assert ClaimRepository().status_of(cid2) == 'superseded'
    assert any(r['payload'].get('relation_id') == rel_id
               for r in _operation_kinds('RELATION_RESOLVE'))


def test_relation_resolution_missing_is_not_found(ops, db):
    with pytest.raises(ops.NotFoundError):
        ops.resolve_claim_relation('missing', {'status': 'accepted'})


# --- entity merge ---------------------------------------------------------- #

def test_entity_merge_through_operation(ops, db):
    keep = _seed_entity('苹果公司')
    drop = _seed_entity('Apple')
    out = ops.merge_entities(keep_id=keep, drop_id=drop)
    assert out['merged']['keep_id'] == keep and out['merged']['drop_id'] == drop
    assert EntityRepository().get(drop)['status'] == 'archived'
    assert any(r['payload'].get('keep_id') == keep and r['payload'].get('drop_id') == drop
               for r in _operation_kinds('MERGE_ENTITY'))


def test_entity_merge_self_is_rejected(ops, db):
    eid = _seed_entity()
    with pytest.raises(ValueError):  # integrity raises ValueError for self-merge
        ops.merge_entities(keep_id=eid, drop_id=eid)


# --- kind-aware lifecycle for idea / question ------------------------------ #

def _idea_status(iid):
    for row in IdeaRepository().list(1000, 0, None):
        if row['id'] == iid:
            return row['status']
    raise AssertionError(f'idea {iid} not found')


def _question_status(qid):
    for row in QuestionRepository().list(1000, 0, None):
        if row['id'] == qid:
            return row['status']
    raise AssertionError(f'question {qid} not found')


def test_kind_aware_lifecycle_for_idea_and_question(ops, db):
    from app.domain.operations import OperationRequest, run
    iid = IdeaRepository().insert(content='想法一', status='candidate')
    qid = QuestionRepository().insert(content='问题一', status='open')

    with transaction() as tx:
        run(OperationRequest(kind='ARCHIVE', payload={'kind': 'idea', 'item_id': iid},
                             actor='user', reason=''), tx)
    assert _idea_status(iid) == 'archived'

    with transaction() as tx:
        run(OperationRequest(kind='RESTORE', payload={'kind': 'question', 'item_id': qid,
                                                      'status': 'answered'}), tx)
    assert _question_status(qid) == 'answered'

    with transaction() as tx:
        run(OperationRequest(kind='ACCEPT', payload={'kind': 'question', 'item_id': qid,
                                                     'status': 'answered'}), tx)
    assert _question_status(qid) == 'answered'

    kinds = {r['kind'] for r in OperationRepository().list(200)}
    assert {'ARCHIVE', 'RESTORE', 'ACCEPT'} <= kinds
