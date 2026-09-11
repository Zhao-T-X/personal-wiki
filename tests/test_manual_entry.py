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


def _client(db):
    from fastapi.testclient import TestClient
    import app.main as main
    import importlib
    importlib.reload(main)
    return TestClient(main.app)


def test_manual_entity_create_and_edit(tmp_path):
    db = _reload_db(tmp_path)
    c = _client(db)
    r = c.post('/api/entities', json={'name': 'RAG', 'type': 'Technology', 'description': 'retrieval augmented generation', 'aliases': ['rag']})
    assert r.status_code == 200
    eid = r.json()['id']
    assert c.post('/api/entities', json={'name': 'RAG', 'type': 'Technology'}).status_code == 409
    row = c.get('/api/entities').json()[0]
    assert row['name'] == 'RAG' and row['status'] == 'candidate'
    # type is editable and validated
    assert c.patch(f'/api/entities/{eid}', json={'type': 'NotAType'}).status_code == 422
    assert c.patch(f'/api/entities/{eid}', json={'type': 'Concept'}).json()['updated'] is True
    detail = c.get(f'/api/entities/{eid}').json()
    assert detail['type'] == 'Concept'
    # name rename keeps aliases in sync
    assert c.patch(f'/api/entities/{eid}', json={'name': 'Retrieval-Augmented Generation'}).json()['updated'] is True
    assert c.get(f'/api/entities/{eid}').json()['name'] == 'Retrieval-Augmented Generation'


def test_entities_search_and_total(tmp_path):
    db = _reload_db(tmp_path)
    c = _client(db)
    c.post('/api/entities', json={'name': 'GOMS', 'type': 'Technology'})
    c.post('/api/entities', json={'name': 'AgentScope', 'type': 'Software'})
    res = c.get('/api/entities?q=goms').json()
    assert len(res) == 1 and res[0]['name'] == 'GOMS' and res[0]['total'] == 1
    assert c.get('/api/entities').json()[0]['total'] == 2


def test_manual_idea_question_event_without_source(tmp_path):
    db = _reload_db(tmp_path)
    c = _client(db)
    assert c.post('/api/ideas', json={'content': '记录一个想法'}).status_code == 200
    assert c.post('/api/questions', json={'content': '检索质量如何量化？'}).status_code == 200
    assert c.post('/api/events', json={'description': '评审会', 'event_type': 'meeting'}).status_code == 200
    assert len(c.get('/api/ideas').json()) == 1
    assert len(c.get('/api/questions').json()) == 1
    assert len(c.get('/api/events').json()) == 1
    assert c.get('/api/ideas?offset=5').json() == []


def test_research_task_lifecycle(tmp_path):
    db = _reload_db(tmp_path)
    c = _client(db)
    created = c.post('/api/research', json={'question_text': '检索质量如何影响 RAG？'}).json()
    assert created['status'] == 'open'
    rows = c.get('/api/research').json()
    assert len(rows) == 1 and rows[0]['question_text'].startswith('检索质量')
    assert c.post('/api/research/missing/run').status_code == 404


def test_health_metrics_are_honest(tmp_path):
    db = _reload_db(tmp_path)
    c = _client(db)
    c.post('/api/entities', json={'name': 'A', 'type': 'Concept'})
    h = c.get('/api/knowledge/health').json()
    assert h['total_entities'] == 1 and h['verified_entities'] == 0
    assert h['verified_entity_ratio'] == 0.0
    assert 'object_text_claims' in h and 'stale_candidates' in h
    assert 'evidence_coverage' not in h


def test_run_cancel_and_finished_guard(tmp_path):
    db = _reload_db(tmp_path)
    c = _client(db)
    conn = db.connect()
    conn.execute("INSERT INTO llm_runs(id,task_type,status) VALUES('r1','extract','started')")
    conn.execute("INSERT INTO llm_runs(id,task_type,status) VALUES('r2','extract','success')")
    conn.commit(); conn.close()
    assert c.post('/api/runs/r1/cancel').json()['cancelling'] is True
    assert c.post('/api/runs/r2/cancel').status_code == 409
    assert c.post('/api/runs/missing/cancel').status_code == 404


def test_ask_without_evidence_still_records_run(tmp_path):
    db = _reload_db(tmp_path)
    c = _client(db)
    res = c.post('/api/ask', json={'question': '完全不相关的问题 xyzzy'}).json()
    assert res['evidence'] == []
    conn = db.connect()
    rows = conn.execute("SELECT status, summary_json FROM llm_runs WHERE task_type='ask'").fetchall()
    conn.close()
    assert len(rows) == 1 and rows[0]['status'] == 'success'
    import json
    summary = json.loads(rows[0]['summary_json'])
    assert summary['evidence_count'] == 0 and summary['answer_chars'] == 0
