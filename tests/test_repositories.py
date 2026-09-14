"""Repository layer (docs/adr/ADR-004).

Two things are pinned here:
1. behaviour  — each repository reads/writes the rows business code expects;
2. the unit-of-work contract — a repository built with a caller's ``conn`` joins
   that transaction and must never commit or close it on its own.
"""
import importlib
import os
import sys


# base must be reloaded after db (it binds ``connect``); the concrete repos after
# base (they bind ``Repository``).
_REPO_MODULES = (
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
)


def _reload(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config)
    importlib.reload(db)
    for name in _REPO_MODULES:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


def _seed_document():
    from app.repositories.document_repo import DocumentRepository
    docs = DocumentRepository()
    doc_id = docs.create(title='T', content='hello world')
    chunk = docs.replace_chunks(doc_id, [('hello world', 0, 0, 11)])[0]
    return doc_id, chunk['id']


def _seed_entity(name='Alpha', aliases=('Alpha',)):
    from app.ontology import normalize_name
    from app.repositories.entity_repo import EntityRepository
    import uuid
    eid = str(uuid.uuid4())
    EntityRepository().insert(eid, type='Concept', types=['Concept'], name=name,
                              aliases=list(aliases), description=None, properties={},
                              normalize=normalize_name)
    return eid


def _seed_claim(doc_id, chunk_id, subject_id):
    from app.repositories.claim_repo import ClaimRepository
    return ClaimRepository().insert(
        subject_id=subject_id, predicate='affects', object_id=None, object_text='beta',
        content='Alpha affects beta.', context={'note': 'n'}, claim_type='factual',
        polarity='positive', modality='asserted', confidence=0.9, status='candidate',
        created_by='llm', source_document_id=doc_id, source_chunk_id=chunk_id,
        source_start_offset=0, source_end_offset=11, source_quote='hello world')


# -- documents ----------------------------------------------------------------

def test_document_repo_crud_and_chunks(tmp_path):
    _reload(tmp_path)
    from app.repositories.document_repo import DocumentRepository
    docs = DocumentRepository()
    doc_id = docs.create(title='Note', content='one two three')

    assert docs.exists(doc_id)
    assert docs.get(doc_id)['title'] == 'Note'

    chunks = docs.replace_chunks(doc_id, [('one two', 0, 0, 7), ('three', 1, 8, 13)])
    assert [c['chunk_index'] for c in chunks] == [0, 1]
    assert len(docs.chunks(doc_id)) == 2
    assert docs.chunk(chunks[0]['id'])['title'] == 'Note'

    items, total = docs.list(10)
    assert total == 1 and items[0]['chunk_count'] == 2

    assert docs.update(doc_id, title='Renamed', content='new body', source_type='note',
                       source_uri=None, metadata={'k': 1}) == 1
    assert docs.get(doc_id)['title'] == 'Renamed'

    # replace_chunks drops the old chunks (and their embeddings) first.
    assert docs.replace_chunks(doc_id, [('only', 0, 0, 4)])
    assert len(docs.chunks(doc_id)) == 1

    assert docs.delete(doc_id) is True
    assert docs.get(doc_id) is None


def test_document_repo_joins_caller_transaction(tmp_path):
    _reload(tmp_path)
    from app.db import connect
    from app.repositories.document_repo import DocumentRepository

    conn = connect()
    try:
        docs = DocumentRepository(conn)
        doc_id = docs.create(title='Rolled back', content='x')
        # A repository built with a conn must NOT have committed.
        conn.rollback()
        assert DocumentRepository(conn).get(doc_id) is None
    finally:
        conn.close()


def test_standalone_write_commits(tmp_path):
    _reload(tmp_path)
    from app.repositories.document_repo import DocumentRepository
    doc_id = DocumentRepository().create(title='Committed', content='x')
    # A fresh connection (separate transaction) must now see the row.
    assert DocumentRepository().get(doc_id) is not None


# -- entities -----------------------------------------------------------------

def test_entity_repo_lookup_and_alias_rebuild(tmp_path):
    _reload(tmp_path)
    from app.db import dumps
    from app.ontology import normalize_name
    from app.repositories.entity_repo import EntityRepository

    entities = EntityRepository()
    eid = _seed_entity('Alpha', aliases=('Alpha', 'A1'))

    assert entities.by_name('alpha')['id'] == eid
    assert entities.by_alias('a1') == eid
    assert entities.get_full(eid)['aliases'] == ['Alpha', 'A1']

    items, total = entities.list(limit=10, offset=0, q='alp')
    assert total == 1 and items[0]['properties'] == {}

    assert entities.update(eid, {'name': 'Alpha 2', 'aliases_json': dumps(['Alpha 2', 'A2'])},
                           aliases=['Alpha 2', 'A2'], normalize=normalize_name) == 1
    assert entities.by_alias('a2') == eid
    assert entities.by_alias('a1') is None  # old aliases were replaced, not merged


# -- claims -------------------------------------------------------------------

def test_claim_repo_status_and_lifecycle(tmp_path):
    _reload(tmp_path)
    from app.repositories.claim_repo import ClaimRepository
    doc_id, chunk_id = _seed_document()
    subject = _seed_entity()
    claim_id = _seed_claim(doc_id, chunk_id, subject)

    claims = ClaimRepository()
    item = claims.get(claim_id)
    assert item['subject_name'] == 'Alpha'
    assert item['context'] == {'note': 'n'}

    assert [c['id'] for c in claims.list(10, status='candidate')] == [claim_id]
    assert claims.status_of(claim_id) == 'candidate'

    assert claims.set_status(claim_id, 'verified') == 1
    assert claims.status_of(claim_id) == 'verified'

    # supersede then restore: restore only fires while the claim is actually superseded.
    claims.set_status(claim_id, 'superseded')
    assert claims.restore_status(claim_id, 'verified', only_if='superseded') == 1
    assert claims.status_of(claim_id) == 'verified'
    assert claims.restore_status(claim_id, 'verified', only_if='superseded') == 0

    assert claims.health_counts()['total'] == 1


def test_claim_repo_evidence_and_provenance(tmp_path):
    _reload(tmp_path)
    from app.repositories.claim_repo import ClaimRepository
    from app.repositories.evidence_repo import EvidenceRepository
    doc_id, chunk_id = _seed_document()
    subject = _seed_entity()
    _seed_claim(doc_id, chunk_id, subject)

    # for_chunk/for_document are what retrieval reads; both carry the quote.
    assert ClaimRepository().for_chunk(chunk_id, 3)[0]['source_quote'] == 'hello world'
    assert ClaimRepository().for_document(doc_id)[0]['subject_name'] == 'Alpha'
    assert len(ClaimRepository().for_document_pack(doc_id)) == 1

    assert EvidenceRepository().provenance(chunk_id)['document_id'] == doc_id
    assert EvidenceRepository().for_document(doc_id)[0]['source_quote'] == 'hello world'


# -- relations ----------------------------------------------------------------

def test_relation_repo_insert_exists_list(tmp_path):
    _reload(tmp_path)
    from app.repositories.relation_repo import RelationRepository
    doc_id, chunk_id = _seed_document()
    source = _seed_entity('Alpha')
    target = _seed_entity('Beta')

    relations = RelationRepository()
    relations.insert(source_id=source, predicate='affects', target_id=target, context={},
                     confidence=0.8, status='candidate', created_by='normalizer',
                     source_document_id=doc_id, source_chunk_id=chunk_id,
                     source_start_offset=0, source_end_offset=11, source_quote='hello world')

    assert relations.exists(source_id=source, predicate='affects', target_id=target,
                            document_id=doc_id, chunk_id=chunk_id)
    assert relations.list(10)[0]['source_name'] == 'Alpha'
    assert relations.count() == 1
    assert relations.delete_for_document(doc_id) is None
    assert relations.count() == 0


# -- catalog ------------------------------------------------------------------

def test_catalog_repo_stats_and_export(tmp_path):
    _reload(tmp_path)
    from app.repositories.catalog_repo import CatalogRepository
    _seed_document()
    catalog = CatalogRepository()
    assert catalog.stats()['documents'] == 1
    assert len(catalog.export_all()['documents']) == 1
    assert catalog.integrity()['integrity'] == 'ok'
