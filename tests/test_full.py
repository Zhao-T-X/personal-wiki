import asyncio
import os


_DB_BOUND_MODULES = (
    'app.service', 'app.knowledge', 'app.resolution',
    'app.retrieval', 'app.graph', 'app.importer', 'app.embeddings',
)


def _db(tmp_path):
    os.environ['DATABASE_PATH']=str(tmp_path/'kb.db')
    os.environ['SETTINGS_PATH']=str(tmp_path/'settings.json')
    import importlib, sys
    import app.config as config; import app.db as db
    importlib.reload(config); importlib.reload(db)
    # Modules that bound the previous db helpers at import time must be
    # reloaded too, otherwise they keep writing to an older database.
    for name in _DB_BOUND_MODULES:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db(); return db


def test_hybrid_fallback_and_provenance(tmp_path):
    db=_db(tmp_path)
    from app.service import create_document, index_document
    from app.retrieval import search
    did=create_document(title='RAG',content='RAG helps with changing knowledge.',source_type='note',source_uri=None,metadata={})
    asyncio.run(index_document(did,use_llm=False))
    out=search('changing knowledge',limit=5,semantic=True)
    assert out and out[0]['document_id']==did
    c=db.connect(); row=c.execute('select start_offset,end_offset,content from chunks where document_id=?',(did,)).fetchone(); c.close()
    assert row[0]==0 and row[1]==len(row[2])


def test_status_and_schema(tmp_path):
    db=_db(tmp_path)
    from app.extraction import normalize_extraction
    x={'entities':[{'name':'RAG','types':['Concept'],'aliases':[]}], 'claims':[], 'events':[], 'ideas':[], 'questions':[]}
    assert normalize_extraction(x)['entities'][0]['name']=='RAG'


def test_agentscope_endpoint_disabled(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr('app.main.runtime', lambda: {'agentscope_enabled': False, 'openai_api_key': ''})
    with TestClient(app) as client:
        r = client.post('/api/agent/ask', json={'message': 'hello'})
        assert r.status_code == 503

def test_agent_prompt_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path/'kb.db'))
    import importlib
    import app.config as config
    import app.db as db
    import app.prompt_profiles as pp
    importlib.reload(config); importlib.reload(db); importlib.reload(pp); db.init_db()
    profile = pp.get_profile('knowledge')
    assert profile['editable_scope'] == 'custom_prompt_only'
    assert profile['storage'] == 'sqlite'
    updated = pp.update_profile('knowledge', 'Answer with a concise executive summary first.', 'test')
    assert 'executive summary' in updated['custom_prompt']
    effective = pp.compose_prompt('knowledge')
    assert 'executive summary' in effective
    assert '[SKILL]' in effective
    restored = pp.restore_version('knowledge', 1)
    assert restored['custom_prompt'] == ''
    conn = db.connect()
    assert conn.execute('SELECT COUNT(*) FROM agent_prompt_profiles').fetchone()[0] == 6
    assert conn.execute('SELECT COUNT(*) FROM agent_prompt_versions WHERE role=\'knowledge\'').fetchone()[0] >= 2
    conn.close()
    reset = pp.reset_profile('knowledge')
    assert reset['custom_prompt'] == ''
