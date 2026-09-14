"""0-LLM direct lookup through ``POST /api/ask`` (task §19/§20/§23; ADR-013).

Two boundaries matter and both are asserted here:

* a simple fact the wiki already stores is answered **without a single model
  call** (the whole point of routing);
* when the stored knowledge cannot answer *certainly* — several competing current
  claims for the same ``(subject, predicate)`` — the endpoint refuses instead of
  picking the value that merely looks right.
"""
from __future__ import annotations

import uuid


def _reload_db(tmp_path):
    import importlib
    import os

    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config)
    importlib.reload(db)
    db.init_db()
    return db


def _seed(db, *, defined_as: int = 1) -> str:
    """One entity 'RAG' with ``defined_as`` claims, all backed by a real document."""
    from app.service import create_document, write_chunks

    doc_id = create_document(title='RAG 定义', content='seed', source_type='note',
                             source_uri=None, metadata={})
    chunk_id = write_chunks(doc_id, 'seed')[0]['id']
    conn = db.connect()
    entity_id = str(uuid.uuid4())
    conn.execute('INSERT INTO entities(id,type,types_json,name,aliases_json,properties_json,status)'
                 ' VALUES(?,?,?,?,?,?,?)',
                 (entity_id, 'Technology', '["Technology"]', 'RAG', '[]', '{}', 'verified'))
    for index in range(defined_as):
        conn.execute(
            '''INSERT INTO claims(id,subject_id,predicate,object_id,object_text,claim_type,polarity,
                                  modality,confidence,status,created_by,source_document_id,
                                  source_chunk_id,source_start_offset,source_end_offset,source_quote)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (str(uuid.uuid4()), entity_id, 'defined_as', None, f'definition {index}',
             'definitional', 'positive', 'asserted', 0.9, 'verified', 'llm', doc_id,
             chunk_id, 0, 4, 'seed quote'))
    conn.commit()
    conn.close()
    return doc_id


def _ask(question: str) -> dict:
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app).post('/api/ask', json={'question': question}).json()


def test_simple_fact_is_answered_with_zero_llm_calls(tmp_path, monkeypatch):
    db = _reload_db(tmp_path)
    _seed(db, defined_as=1)

    import app.llm
    def _forbidden(*args, **kwargs):
        raise AssertionError('a direct lookup must never call the model')
    monkeypatch.setattr(app.llm, '_client', _forbidden)

    body = _ask('RAG 的定义是什么？')

    assert body['direct'] is True
    assert body['route'] == 'fact_lookup'
    assert body['llm_expected'] is False
    assert body['answer_value'] == 'definition 0'
    assert body['citations'], 'a direct answer must still be traceable'

    # And prove it: the run recorded no model steps at all.
    conn = db.connect()
    steps = conn.execute('SELECT COUNT(*) c FROM llm_run_steps').fetchone()['c']
    conn.close()
    assert steps == 0


def test_ambiguous_fact_is_refused_not_guessed(tmp_path):
    db = _reload_db(tmp_path)
    _seed(db, defined_as=2)          # two current claims: no single confident answer

    body = _ask('RAG 的定义是什么？')

    assert body['status'] == 'no_sufficient_evidence'
    assert body['direct'] is True
    assert body['citations'] == []           # a refusal offers no evidence
    assert '知识库中没有' in body['answer']    # §23: admit it, do not guess
