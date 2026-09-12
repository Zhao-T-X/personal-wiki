"""Deleting a document must work even after it has been indexed.

`claims` / `relations` / `events` / `ideas` / `questions` reference documents
WITHOUT ON DELETE CASCADE (see app/db.py), and `PRAGMA foreign_keys = ON` is set
on every connection, so a plain `DELETE FROM documents` fails as soon as the
document has derived knowledge. That failure also skipped `conn.close()`, leaking
a write lock that broke unrelated requests with "database is locked".
"""
import importlib
import os
import sys
import uuid


def _reload_db(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config); importlib.reload(db)
    for name in ('app.runlog', 'app.service', 'app.knowledge', 'app.resolution',
                 'app.retrieval', 'app.graph', 'app.importer', 'app.embeddings'):
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


def _client(db):
    from fastapi.testclient import TestClient
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def _connect():
    """Resolve connect() lazily: _reload_db rebinds it on the module object."""
    import app.db as db
    return db.connect()


def _indexed_document(c, title, content):
    doc_id = c.post('/api/documents',
                    json={'title': title, 'content': content, 'source_type': 'note'}).json()['id']
    assert c.post(f'/api/documents/{doc_id}/index/local').status_code == 200
    conn = _connect()
    chunk_id = conn.execute('SELECT id FROM chunks WHERE document_id=? LIMIT 1', (doc_id,)).fetchone()['id']
    conn.close()
    return doc_id, chunk_id


def _seed_derived(doc_id, chunk_id):
    """Attach a row to every table that references documents without CASCADE."""
    conn = _connect()
    subject, target = str(uuid.uuid4()), str(uuid.uuid4())
    for eid, name in ((subject, 'Feedback Loop'), (target, 'System')):
        conn.execute(
            'INSERT INTO entities(id,type,types_json,name,aliases_json,description,properties_json,status)'
            ' VALUES(?,?,?,?,?,?,?,?)',
            (eid, 'Concept', '["Concept"]', name, '[]', None, '{}', 'candidate'))
    conn.execute(
        'INSERT INTO claims(id,subject_id,predicate,object_id,content,claim_type,polarity,modality,'
        'confidence,status,created_by,source_document_id,source_chunk_id,source_start_offset,'
        'source_end_offset,source_quote) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (str(uuid.uuid4()), subject, 'affects', target, 'Feedback loops affect systems.',
         'factual', 'positive', 'asserted', 0.9, 'candidate', 'llm', doc_id, chunk_id, 0, 20,
         'Feedback loops matter.'))
    conn.execute(
        'INSERT INTO relations(id,source_id,predicate,target_id,confidence,status,created_by,'
        'source_document_id,source_chunk_id,source_start_offset,source_end_offset)'
        ' VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (str(uuid.uuid4()), subject, 'affects', target, 0.9, 'candidate', 'llm',
         doc_id, chunk_id, 0, 20))
    conn.execute(
        'INSERT INTO events(id,event_type,description,status,source_document_id,source_chunk_id)'
        ' VALUES(?,?,?,?,?,?)',
        (str(uuid.uuid4()), 'meeting', 'Reviewed the loop', 'unknown', doc_id, chunk_id))
    conn.execute(
        'INSERT INTO ideas(id,content,status,source_document_id,source_chunk_id) VALUES(?,?,?,?,?)',
        (str(uuid.uuid4()), 'Loops are everywhere', 'candidate', doc_id, chunk_id))
    conn.commit(); conn.close()


def test_delete_document_with_derived_knowledge(tmp_path):
    db = _reload_db(tmp_path)
    c = _client(db)
    doc_id, chunk_id = _indexed_document(c, 'Systems', 'Feedback loops matter.')
    _seed_derived(doc_id, chunk_id)

    r = c.delete(f'/api/documents/{doc_id}')
    assert r.status_code == 200, r.text

    conn = _connect()
    assert conn.execute('SELECT COUNT(*) c FROM documents WHERE id=?', (doc_id,)).fetchone()['c'] == 0
    for table, column in (('chunks', 'document_id'), ('claims', 'source_document_id'),
                          ('relations', 'source_document_id'), ('events', 'source_document_id'),
                          ('ideas', 'source_document_id')):
        left = conn.execute(f'SELECT COUNT(*) c FROM {table} WHERE {column}=?', (doc_id,)).fetchone()['c']
        assert left == 0, f'{table} left {left} row(s) behind'
    conn.close()


def test_delete_missing_document_returns_404(tmp_path):
    db = _reload_db(tmp_path)
    c = _client(db)
    assert c.delete('/api/documents/does-not-exist').status_code == 404


def test_delete_does_not_touch_other_documents(tmp_path):
    db = _reload_db(tmp_path)
    c = _client(db)
    keep_id, _ = _indexed_document(c, 'Keep', 'keep me')
    drop_id, drop_chunk = _indexed_document(c, 'Drop', 'drop me')
    _seed_derived(drop_id, drop_chunk)

    assert c.delete(f'/api/documents/{drop_id}').status_code == 200

    conn = _connect()
    assert conn.execute('SELECT COUNT(*) c FROM chunks WHERE document_id=?', (keep_id,)).fetchone()['c'] > 0
    assert conn.execute('SELECT COUNT(*) c FROM documents').fetchone()['c'] == 1
    conn.close()
