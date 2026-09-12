"""Claim relationship detection: duplicate / coexists / supersedes / contradicts.

The core promise under test is *never overwrite*: new knowledge becomes a new
claim, the relationship to what we already knew is recorded separately, and only
a human can let one claim stop representing current knowledge.
"""
import importlib
import os
import sys

from app.claim_relations import (compare_claim, compare_with_related,
                                 detect_claim_relations)


def _claim(**kw):
    base = {
        'id': 'c1', 'subject_id': 's1', 'predicate': 'uses',
        'object_id': None, 'object_text': None,
        'polarity': 'positive', 'status': 'candidate',
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- pure verdicts

def test_identical_statement_is_duplicate():
    a = _claim(id='c1', object_id='o1')
    b = _claim(id='c2', object_id='o1')
    v = compare_claim(b, a)
    assert v['relationship'] == 'duplicate'
    assert v['related_claim_id'] == 'c1'
    assert v['suggested_action'] == 'link_evidence'


def test_resolved_entities_compare_by_id_not_surface_text():
    """'Apple Inc.' and 'Apple' are equal once entity resolution folded them."""
    a = _claim(id='c1', object_id='o1', object_text='Apple Inc.')
    b = _claim(id='c2', object_id='o1', object_text='Apple')
    assert compare_claim(b, a)['relationship'] == 'duplicate'


def test_same_statement_opposite_polarity_contradicts():
    a = _claim(id='c1', object_id='o1', polarity='positive')
    b = _claim(id='c2', object_id='o1', polarity='negative')
    v = compare_claim(b, a)
    assert v['relationship'] == 'contradicts'
    assert v['confidence'] > 0.8


def test_multi_valued_predicate_different_object_coexists():
    a = _claim(id='c1', predicate='uses', object_id='o1')
    b = _claim(id='c2', predicate='uses', object_id='o2')
    v = compare_claim(b, a)
    assert v['relationship'] == 'coexists'
    assert v['suggested_action'] == 'keep_both'


def test_single_valued_predicate_different_object_needs_review():
    a = _claim(id='c1', predicate='defined_as', object_id='o1')
    b = _claim(id='c2', predicate='defined_as', object_id='o2')
    v = compare_claim(b, a)
    assert v['relationship'] == 'contradicts'
    assert v['suggested_action'] == 'review'


def test_supersession_is_never_inferred_from_structure_alone():
    """Telling which claim is newer needs evidence, so the system asks instead."""
    for predicate in ('is', 'defined_as', 'classified_as', 'uses'):
        a = _claim(id='c1', predicate=predicate, object_id='o1')
        b = _claim(id='c2', predicate=predicate, object_id='o2')
        assert compare_claim(b, a)['relationship'] != 'supersedes'


def test_different_subject_or_predicate_is_unclear():
    a = _claim(id='c1', subject_id='s1', object_id='o1')
    assert compare_claim(_claim(id='c2', subject_id='s2', object_id='o1'), a)['relationship'] == 'unclear'
    assert compare_claim(_claim(id='c3', predicate='supports', object_id='o1'), a)['relationship'] == 'unclear'


def test_unresolved_object_is_unclear():
    a = _claim(id='c1', object_id=None, object_text='')
    b = _claim(id='c2', object_id=None, object_text='')
    assert compare_claim(b, a)['relationship'] == 'unclear'


def test_compare_with_related_orders_by_significance():
    duplicate = _claim(id='c1', object_id='o1')
    contradicting = _claim(id='c2', object_id='o1', polarity='negative')
    coexisting = _claim(id='c3', object_id='o2')
    new = _claim(id='c9', object_id='o1')
    order = [v['relationship'] for v in compare_with_related(new, [coexisting, contradicting, duplicate])]
    assert order == ['duplicate', 'contradicts', 'coexists']


# ------------------------------------------------------------- database integration

def _reload_db(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config); importlib.reload(db)
    for name in ('app.runlog', 'app.service', 'app.knowledge', 'app.resolution',
                 'app.retrieval', 'app.graph', 'app.importer', 'app.embeddings',
                 'app.claim_relations'):
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


def _seed(conn, *, doc, claims, predicate='defined_as'):
    """Insert a document + entities + claims. `claims` = [(id, object_entity_id)]."""
    conn.execute("INSERT INTO documents(id,title,content,source_type,content_hash)"
                 " VALUES(?,?,?,?,?)", (doc, f'Doc {doc}', 'text', 'note', f'hash-{doc}'))
    conn.execute("INSERT INTO chunks(id,document_id,content,chunk_index,start_offset,end_offset)"
                 " VALUES(?,?,?,?,?,?)", (f'k-{doc}', doc, 'text', 0, 0, 4))
    for eid, name in (('s1', 'System'), ('o1', 'Feedback'), ('o2', 'Delay')):
        conn.execute("INSERT OR IGNORE INTO entities(id,type,types_json,name,aliases_json,"
                     "description,properties_json,status) VALUES(?,?,?,?,?,?,?,?)",
                     (eid, 'Concept', '["Concept"]', name, '[]', None, '{}', 'verified'))
    for cid, object_id in claims:
        conn.execute(
            "INSERT INTO claims(id,subject_id,predicate,object_id,content,claim_type,polarity,"
            "modality,confidence,status,created_by,source_document_id,source_chunk_id,"
            "source_start_offset,source_end_offset) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, 's1', predicate, object_id, f'System {predicate} {object_id}', 'factual',
             'positive', 'asserted', 0.9, 'candidate', 'llm', doc, f'k-{doc}', 0, 4))


def test_conflict_is_recorded_for_review_without_touching_claims(tmp_path):
    db = _reload_db(tmp_path)
    conn = db.connect()
    _seed(conn, doc='d1', claims=[('c1', 'o1'), ('c2', 'o2')])
    conn.commit()

    written = detect_claim_relations(conn, document_id='d1')
    conn.commit()

    rows = [dict(r) for r in conn.execute('SELECT * FROM claim_relations').fetchall()]
    assert written == 1 and len(rows) == 1
    assert rows[0]['relationship'] == 'contradicts'
    assert rows[0]['status'] == 'candidate'        # conflicts always wait for a human
    assert rows[0]['suggested_action'] == 'review'

    # The claims themselves are untouched: same objects, same status, both still there.
    claims = {r['id']: dict(r) for r in conn.execute('SELECT id,object_id,status FROM claims')}
    assert set(claims) == {'c1', 'c2'}
    assert claims['c1']['object_id'] == 'o1' and claims['c2']['object_id'] == 'o2'
    assert claims['c1']['status'] == 'candidate' and claims['c2']['status'] == 'candidate'
    conn.close()


def test_duplicate_is_linked_automatically_across_documents(tmp_path):
    db = _reload_db(tmp_path)
    conn = db.connect()
    _seed(conn, doc='d1', claims=[('c1', 'o1')])
    _seed(conn, doc='d2', claims=[('c2', 'o1')])
    conn.commit()

    written = detect_claim_relations(conn, document_id='d2')
    conn.commit()

    row = dict(conn.execute('SELECT relationship,status FROM claim_relations').fetchone())
    assert written == 1
    assert row['relationship'] == 'duplicate'
    assert row['status'] == 'accepted'             # linking evidence is safe to automate
    conn.close()


def test_expanding_a_document_replaces_its_own_relations(tmp_path):
    """Re-indexing clears derived claims (and cascades), so detection can re-run."""
    db = _reload_db(tmp_path)
    conn = db.connect()
    _seed(conn, doc='d1', claims=[('c1', 'o1'), ('c2', 'o2')])
    conn.commit()
    assert detect_claim_relations(conn, document_id='d1') == 1
    conn.commit()

    conn.execute("DELETE FROM claims WHERE source_document_id='d1'")
    conn.commit()
    # CASCADE means no dangling relationship can survive its claims.
    assert conn.execute('SELECT COUNT(*) c FROM claim_relations').fetchone()['c'] == 0
    conn.close()


def test_coexistence_is_recorded_as_accepted(tmp_path):
    """Two facts standing side by side are not a conflict, so no human is needed."""
    db = _reload_db(tmp_path)
    conn = db.connect()
    _seed(conn, doc='d1', claims=[('c1', 'o1')], predicate='uses')
    _seed(conn, doc='d2', claims=[('c2', 'o2')], predicate='uses')
    conn.commit()

    assert detect_claim_relations(conn, document_id='d2') == 1
    conn.commit()

    row = dict(conn.execute('SELECT relationship,status FROM claim_relations').fetchone())
    assert row['relationship'] == 'coexists'
    assert row['status'] == 'accepted'
    conn.close()


# --------------------------------------------------------- retrieval lifecycle (§21)

def test_superseded_claim_is_dropped_when_a_current_one_covers_it():
    from app.retrieval import _current_claims
    claims = [
        {'subject_name': 'OpenAI', 'predicate': 'ceo_of', 'status': 'superseded', 'object_name': 'Sam'},
        {'subject_name': 'OpenAI', 'predicate': 'ceo_of', 'status': 'verified', 'object_name': 'Alice'},
    ]
    out = _current_claims(claims)
    assert len(out) == 1
    assert out[0]['object_name'] == 'Alice'


def test_superseded_claim_survives_when_nothing_current_covers_it():
    """History is not deleted: a question about the past still needs evidence."""
    from app.retrieval import _current_claims
    claims = [{'subject_name': 'OpenAI', 'predicate': 'ceo_of', 'status': 'superseded', 'object_name': 'Sam'}]
    out = _current_claims(claims)
    assert len(out) == 1
    assert out[0]['lifecycle'] == 'superseded'


def test_current_claims_passes_through_unrelated_claims():
    from app.retrieval import _current_claims
    claims = [
        {'subject_name': 'A', 'predicate': 'uses', 'status': 'candidate', 'object_name': 'B'},
        {'subject_name': 'C', 'predicate': 'uses', 'status': 'superseded', 'object_name': 'D'},
    ]
    out = _current_claims(claims)
    # D is kept: nothing current covers C/uses, so it is the only knowledge we have.
    assert [c.get('lifecycle') for c in out] == [None, 'superseded']
    assert _current_claims([]) == []


# ------------------------------------------------------ supersession lifecycle (E2E §29)

def _client(db):
    from fastapi.testclient import TestClient
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def test_confirming_supersession_moves_only_the_lifecycle_status(tmp_path):
    """Detect -> confirm -> older claim is history. Its text is never rewritten."""
    db = _reload_db(tmp_path)
    conn = db.connect()
    _seed(conn, doc='d1', claims=[('c1', 'o1')])
    _seed(conn, doc='d2', claims=[('c2', 'o2')])
    conn.commit()
    assert detect_claim_relations(conn, document_id='d2') == 1
    conn.commit()
    relation_id = conn.execute('SELECT id FROM claim_relations').fetchone()['id']
    conn.close()

    c = _client(db)
    r = c.patch(f'/api/claim-relations/{relation_id}',
                json={'status': 'accepted', 'relationship': 'supersedes'})
    assert r.status_code == 200, r.text

    conn = db.connect()
    older = dict(conn.execute("SELECT status,object_id,content FROM claims WHERE id='c1'").fetchone())
    assert older['status'] == 'superseded'        # lifecycle moved
    assert older['object_id'] == 'o1'             # content untouched
    assert older['content'] == 'System defined_as o1'

    # Undo restores exactly the status it had before.
    assert c.patch(f'/api/claim-relations/{relation_id}', json={'status': 'candidate'}).status_code == 200
    assert conn.execute("SELECT status FROM claims WHERE id='c1'").fetchone()['status'] == 'candidate'
    conn.close()


def test_claims_table_accepts_superseded_and_rejects_unknown_status(tmp_path):
    """The rebuilt constraint allows the new lifecycle value, nothing more."""
    import sqlite3

    db = _reload_db(tmp_path)
    conn = db.connect()
    _seed(conn, doc='d1', claims=[('c1', 'o1')])
    conn.commit()

    conn.execute("UPDATE claims SET status='superseded' WHERE id='c1'")
    conn.commit()
    assert conn.execute("SELECT status FROM claims WHERE id='c1'").fetchone()['status'] == 'superseded'

    try:
        conn.execute("UPDATE claims SET status='nonsense' WHERE id='c1'")
        conn.commit()
        raise AssertionError('CHECK constraint should reject an unknown status')
    except sqlite3.IntegrityError:
        conn.rollback()
    conn.close()
