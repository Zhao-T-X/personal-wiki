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


def test_document_knowledge_reports_what_it_contributed(tmp_path):
    """「它从这篇里读出了什么」必须来自知识库本身，界面不能自己拼一份。"""
    db = _reload_db(tmp_path)
    import uuid
    from app.service import create_document, write_chunks

    text = '苹果公司的首席执行官是蒂姆·库克。'
    doc_id = create_document(title='苹果公司简介', content=text, source_type='note',
                             source_uri=None, metadata={})
    chunk_id = write_chunks(doc_id, text)[0]['id']
    conn = db.connect()
    apple, tim = str(uuid.uuid4()), str(uuid.uuid4())
    conn.execute('INSERT INTO entities(id,type,types_json,name,aliases_json,properties_json,status) VALUES(?,?,?,?,?,?,?)',
                 (apple, 'Organization', '["Organization"]', '苹果公司', '["苹果"]', '{}', 'candidate'))
    conn.execute('INSERT INTO entities(id,type,types_json,name,aliases_json,properties_json,status) VALUES(?,?,?,?,?,?,?)',
                 (tim, 'Person', '["Person"]', '蒂姆·库克', '[]', '{}', 'candidate'))
    claim_id = str(uuid.uuid4())
    conn.execute('''INSERT INTO claims(id,subject_id,predicate,object_id,object_text,claim_type,polarity,modality,
                    confidence,status,created_by,source_document_id,source_chunk_id,source_start_offset,source_end_offset,source_quote)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                 (claim_id, apple, 'has_ceo', tim, None, 'factual', 'positive', 'asserted', 0.9,
                  'candidate', 'llm', doc_id, chunk_id, 0, len(text), text))
    conn.commit(); conn.close()

    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    body = client.get(f'/api/documents/{doc_id}/knowledge').json()

    assert body['names'] == ['苹果公司', '蒂姆·库克']
    assert body['counts'] == {'claims': 1, 'names': 2, 'pending': 1}
    row = body['claims'][0]
    assert (row['id'], row['subject'], row['predicate'], row['object']) == (
        claim_id, '苹果公司', 'has_ceo', '蒂姆·库克')
    # 「首席执行官」是 registry 声明的 label，不是接口层自己编的翻译。
    assert row['predicate_label'] == '首席执行官'
    assert row['status'] == 'candidate'

    # 一个只被存下、还没读出知识的文档返回空，而不是 404。
    empty_doc = create_document(title='未抽取', content='无关内容', source_type='note',
                                source_uri=None, metadata={})
    empty = client.get(f'/api/documents/{empty_doc}/knowledge').json()
    assert empty['claims'] == [] and empty['counts']['claims'] == 0
    assert client.get('/api/documents/missing/knowledge').status_code == 404


def test_predicate_labels_endpoint_is_the_display_layer(tmp_path):
    """The registry's labels ship once, as data — never as a frontend vocabulary."""
    _reload_db(tmp_path)
    from fastapi.testclient import TestClient
    from app.main import app
    from app.ontology import CLAIM_PREDICATES, claim_registry_version

    body = TestClient(app).get('/api/ontology/predicates').json()
    assert body['version'] == claim_registry_version()
    # Every registered predicate is named, so no surface can be handed a bare
    # identifier it has no way to render.
    assert set(body['labels']) == set(CLAIM_PREDICATES)
    assert body['labels']['has_ceo'] == '首席执行官'
    assert all(label for label in body['labels'].values())


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
