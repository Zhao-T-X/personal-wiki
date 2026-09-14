"""Knowledge Operations: knowledge changes go through an audited operation.

The invariants under test: no direct claim editing, provenance is mandatory,
supersession only moves lifecycle, and a failed operation leaves nothing behind.
"""
import importlib
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
    'app.resolution',
    'app.knowledge',
    'app.claim_relations',
    'app.service',
    'app.domain.claim_state',
    'app.domain.operations',
)


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


def _seed(tmp_path):
    _reload(tmp_path)
    from app.repositories.document_repo import DocumentRepository
    docs = DocumentRepository()
    doc_id = docs.create(title='D', content='Alice leads OpenAI.')
    chunk = docs.replace_chunks(doc_id, [('Alice leads OpenAI.', 0, 0, 19)])[0]
    return doc_id, chunk['id']


def _create(conn, doc_id, chunk_id, *, subject, predicate, obj, quote='Alice leads OpenAI.'):
    from app.domain.operations import OperationRequest, run
    return run(OperationRequest(kind='CREATE', payload={
        'subject': subject, 'predicate': predicate, 'object': obj,
        'content': quote, 'source_document_id': doc_id, 'source_chunk_id': chunk_id,
        'source_quote': quote}), conn)


def test_create_operation_writes_claim_and_audit(tmp_path):
    doc_id, chunk_id = _seed(tmp_path)
    from app.db import transaction
    from app.repositories.claim_repo import ClaimRepository
    from app.repositories.operation_repo import OperationRepository

    with transaction() as conn:
        result = _create(conn, doc_id, chunk_id, subject='OpenAI', predicate='ceo_of', obj='Alice')

    claim_id = result.affected['claim_id']
    claim = ClaimRepository().get(claim_id)
    assert claim['object_text'] == 'Alice'
    assert claim['source_document_id'] == doc_id
    assert claim['status'] == 'candidate'

    audit = OperationRepository().list(10)
    assert len(audit) == 1
    assert audit[0]['kind'] == 'CREATE'
    assert audit[0]['status'] == 'applied'
    assert audit[0]['result']['claim_id'] == claim_id


def test_create_operation_requires_provenance(tmp_path):
    _seed(tmp_path)
    from app.db import transaction
    from app.domain.operations import OperationError, OperationRequest, run

    with transaction() as conn:
        try:
            run(OperationRequest(kind='CREATE', payload={'subject': 'X', 'predicate': 'is',
                                                         'object': 'Y'}), conn)
            raise AssertionError('CREATE without provenance should be rejected')
        except OperationError:
            pass


def test_supersede_moves_only_lifecycle_and_records_previous_status(tmp_path):
    doc_id, chunk_id = _seed(tmp_path)
    from app.db import transaction
    from app.domain.operations import OperationRequest, run
    from app.repositories.claim_repo import ClaimRepository

    with transaction() as conn:
        old = _create(conn, doc_id, chunk_id, subject='OpenAI', predicate='ceo_of', obj='Sam') \
            .affected['claim_id']
        new = _create(conn, doc_id, chunk_id, subject='OpenAI', predicate='ceo_of', obj='Alice') \
            .affected['claim_id']
        result = run(OperationRequest(kind='SUPERSEDE',
                                      payload={'new_claim_id': new, 'old_claim_id': old}), conn)

    claims = ClaimRepository()
    older = claims.get(old)
    assert older['status'] == 'superseded'
    assert older['object_text'] == 'Sam'            # content untouched
    assert older['source_quote'] == 'Alice leads OpenAI.'  # evidence untouched
    assert claims.get(new)['status'] == 'candidate'
    assert result.affected['previous_status'] == 'candidate'


def test_archive_and_restore_round_trip(tmp_path):
    doc_id, chunk_id = _seed(tmp_path)
    from app.db import transaction
    from app.domain.operations import OperationRequest, run
    from app.repositories.claim_repo import ClaimRepository

    with transaction() as conn:
        claims = ClaimRepository(conn)
        claim_id = _create(conn, doc_id, chunk_id, subject='OpenAI', predicate='ceo_of',
                           obj='Alice').affected['claim_id']
        run(OperationRequest(kind='ARCHIVE', payload={'claim_id': claim_id}), conn)
        assert claims.status_of(claim_id) == 'archived'
        run(OperationRequest(kind='RESTORE', payload={'claim_id': claim_id, 'status': 'verified'}), conn)
        assert claims.status_of(claim_id) == 'verified'
    # after the transaction commits, a fresh connection sees the final state
    assert ClaimRepository().status_of(claim_id) == 'verified'


def test_merge_links_and_archives_the_duplicate(tmp_path):
    doc_id, chunk_id = _seed(tmp_path)
    from app.db import transaction
    from app.domain.operations import OperationRequest, run
    from app.repositories.claim_repo import ClaimRepository

    with transaction() as conn:
        keep = _create(conn, doc_id, chunk_id, subject='OpenAI', predicate='ceo_of',
                       obj='Alice').affected['claim_id']
        drop = _create(conn, doc_id, chunk_id, subject='OpenAI', predicate='ceo_of',
                       obj='Alice').affected['claim_id']
        result = run(OperationRequest(kind='MERGE',
                                      payload={'keep_claim_id': keep, 'merge_claim_id': drop}), conn)

    claims = ClaimRepository()
    assert claims.status_of(drop) == 'archived'
    assert claims.status_of(keep) == 'candidate'
    assert result.affected['relation_id']


def test_unknown_operation_is_rejected(tmp_path):
    _seed(tmp_path)
    from app.db import transaction
    from app.domain.operations import OperationError, OperationRequest, run

    with transaction() as conn:
        try:
            run(OperationRequest(kind='EXPLODE', payload={}), conn)
            raise AssertionError('unknown operation should be rejected')
        except OperationError:
            pass


def test_failed_operation_leaves_no_trace(tmp_path):
    doc_id, chunk_id = _seed(tmp_path)
    from app.db import transaction
    from app.domain.operations import OperationError, OperationRequest, run
    from app.repositories.claim_repo import ClaimRepository
    from app.repositories.operation_repo import OperationRepository

    with transaction() as conn:
        old = _create(conn, doc_id, chunk_id, subject='OpenAI', predicate='ceo_of',
                      obj='Sam').affected['claim_id']
        try:
            # SUPERSEDE pointing at a claim that does not exist -> raises, transaction rolls back.
            run(OperationRequest(kind='SUPERSEDE',
                                 payload={'new_claim_id': old, 'old_claim_id': 'missing'}), conn)
        except OperationError:
            pass

    assert ClaimRepository().status_of(old) == 'candidate'   # untouched
    assert OperationRepository().count() == 1                # only the CREATE was audited
