def _reload_db(tmp_path):
    import os
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import importlib
    import sys
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


def _seed_doc_with_chunk(db):
    import uuid
    from app.service import create_document, write_chunks
    doc_id = create_document(title='D', content='seed content', source_type='note', source_uri=None, metadata={})
    chunks = write_chunks(doc_id, 'seed content')
    return doc_id, chunks[0]['id']


def test_events_ideas_questions_endpoints(tmp_path):
    db = _reload_db(tmp_path)
    doc_id, chunk_id = _seed_doc_with_chunk(db)
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    conn = db.connect()
    conn.execute("INSERT INTO events(id,event_type,description,status,source_document_id,source_chunk_id,source_start_offset,source_end_offset) VALUES('e1','release','R1','completed',?,?,0,1)", (doc_id, chunk_id))
    conn.execute("INSERT INTO ideas(id,content,status,source_document_id,source_chunk_id,source_start_offset,source_end_offset) VALUES('i1','idea text','candidate',?,?,0,1)", (doc_id, chunk_id))
    conn.execute("INSERT INTO questions(id,content,status,source_document_id,source_chunk_id,source_start_offset,source_end_offset) VALUES('q1','question text','open',?,?,0,1)", (doc_id, chunk_id))
    conn.commit(); conn.close()
    ev = client.get('/api/events').json()
    ideas = client.get('/api/ideas').json()
    qs = client.get('/api/questions').json()
    assert ev[0]['id'] == 'e1' and ev[0]['event_type'] == 'release'
    assert ideas[0]['content'] == 'idea text' and ideas[0]['status'] == 'candidate'
    assert qs[0]['content'] == 'question text'
    assert client.get('/api/ideas?status=accepted').json() == []


def test_timeseries_and_integrity(tmp_path):
    db = _reload_db(tmp_path)
    from app.service import create_document
    create_document(title='T', content='c', source_type='note', source_uri=None, metadata={})
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    ts = client.get('/api/stats/timeseries?days=7').json()
    assert len(ts) == 7 and ts[-1]['documents'] >= 1
    assert ts[0]['date'] < ts[-1]['date']
    ig = client.get('/api/database/integrity').json()
    assert ig['integrity'] == 'ok' and ig['size_mb'] >= 0


def test_documents_list_aggregates(tmp_path):
    db = _reload_db(tmp_path)
    from app.service import create_document, write_chunks
    doc_id = create_document(title='D', content='hello world data', source_type='note', source_uri=None, metadata={})
    write_chunks(doc_id, 'hello world data')
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    docs = client.get('/api/documents').json()
    row = next(d for d in docs if d['id'] == doc_id)
    assert row['chunk_count'] >= 1
    assert 'last_run_status' in row
