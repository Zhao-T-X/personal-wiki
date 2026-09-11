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


def _seed(db):
    import uuid
    from app.service import create_document, write_chunks
    doc_id = create_document(title='RAG Survey', content='seed', source_type='note', source_uri=None, metadata={})
    chunk_id = write_chunks(doc_id, 'seed')[0]['id']
    conn = db.connect()
    eid = str(uuid.uuid4())
    conn.execute('INSERT INTO entities(id,type,types_json,name,aliases_json,properties_json,status) VALUES(?,?,?,?,?,?,?)',
                 (eid, 'Technology', '["Technology"]', 'RAG', '[]', '{}', 'verified'))
    def claim(pred, pol, obj_text=None, obj_id=None):
        cid = str(uuid.uuid4())
        conn.execute('''INSERT INTO claims(id,subject_id,predicate,object_id,object_text,claim_type,polarity,modality,
                        confidence,status,created_by,source_document_id,source_chunk_id,source_start_offset,source_end_offset,source_quote)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                     (cid, eid, pred, obj_id, obj_text, 'causal', pol, 'possible', 0.9, 'verified', 'llm',
                      doc_id, chunk_id, 0, 4, 'seed quote'))
        return cid
    claim('defined_as', 'positive', obj_text='a grounding technology')
    c_pos = claim('improves', 'positive', obj_text='answer quality')
    claim('improves', 'negative', obj_text='answer quality')  # conflict twin
    conn.execute('INSERT INTO relations(id,source_id,predicate,target_id,status,created_by,source_document_id,source_chunk_id,source_start_offset,source_end_offset) VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (str(uuid.uuid4()), eid, 'uses', eid, 'candidate', 'normalizer', doc_id, chunk_id, 0, 4))
    conn.commit(); conn.close()
    return eid, c_pos


def test_claim_detail(tmp_path):
    db = _reload_db(tmp_path)
    eid, cid = _seed(db)
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    c = client.get(f'/api/claims/{cid}').json()
    assert c['subject_name'] == 'RAG'
    assert c['predicate'] == 'improves'
    assert client.get('/api/claims/missing').status_code == 404


def test_entity_object_aggregate(tmp_path):
    db = _reload_db(tmp_path)
    eid, _ = _seed(db)
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    o = client.get(f'/api/entities/{eid}/object').json()
    assert o['entity']['name'] == 'RAG'
    assert o['counts']['claims'] == 3
    assert o['counts']['relations'] == 1
    assert o['counts']['evidence'] == 3
    assert o['counts']['documents'] == 1
    assert any(t['type'].startswith('a grounding technology') for t in o['type_decision'])
    assert client.get('/api/entities/missing/object').status_code == 404


def test_conflicts_endpoint(tmp_path):
    db = _reload_db(tmp_path)
    _seed(db)
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    cs = client.get('/api/conflicts').json()
    assert len(cs) == 1
    assert cs[0]['subject_name'] == 'RAG' and cs[0]['predicate'] == 'improves'
    assert {c['polarity'] for c in cs[0]['claims']} == {'positive', 'negative'}


def test_health_endpoint(tmp_path):
    db = _reload_db(tmp_path)
    _seed(db)
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    h = client.get('/api/knowledge/health').json()
    assert h['total_claims'] == 3
    assert h['verified_claims'] == 3 and h['verified_claim_ratio'] == 100.0
    assert 'object_text_claims' in h and 'open_questions' in h and 'stale_candidates' in h


def test_skills_endpoints(tmp_path):
    _reload_db(tmp_path)
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    skills = client.get('/api/skills').json()
    assert isinstance(skills, list)
    if skills:
        d = client.get(f"/api/skills/{skills[0]['name']}").json()
        assert d['name'] == skills[0]['name'] and len(d['content']) > 0
    assert client.get('/api/skills/definitely-not-exist').status_code == 404
