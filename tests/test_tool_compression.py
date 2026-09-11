"""Context Runtime P2b: tool result compression + the batch entity tool.

The agent tools are the boundary between SQLite and the ReAct conversation:
whatever they return is appended to the model's context verbatim. These tests
pin the contract introduced in P2b - quote cards instead of whole chunks,
capped and ranked claims, description-free graphs, honest truncation markers,
and a batch entity tool so scanning N entities costs one call instead of N.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD_MODULES = (
    'app.config',
    'app.db',
    'app.tools.compression',
    'app.retrieval',
    'app.graph',
    'app.tools.knowledge_tools',
)


def _reload(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'wiki.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config
    import app.db
    importlib.reload(app.config)
    importlib.reload(app.db)
    for name in _RELOAD_MODULES[2:]:
        importlib.reload(importlib.import_module(name))
    app.db.init_db()
    return app.db


def _seed_doc_chunk(conn, doc_id='d1', chunk_id='ch1', body='RAG is a method. '):
    conn.execute("INSERT INTO documents(id,title,content,source_type,content_hash) VALUES(?,?,?,?,?)",
                 (doc_id, 'RAG Survey', body, 'note', 'hash-' + doc_id))
    conn.execute('INSERT INTO chunks(id,document_id,content,chunk_index,start_offset,end_offset) VALUES(?,?,?,?,?,?)',
                 (chunk_id, doc_id, body, 0, 0, len(body)))
    return doc_id, chunk_id


def _seed_entity(conn, name, description=None, status='candidate', doc='d1', chunk='ch1'):
    eid = str(uuid.uuid4())
    conn.execute('INSERT INTO entities(id,type,types_json,name,aliases_json,description,properties_json,status) '
                 'VALUES(?,?,?,?,?,?,?,?)',
                 (eid, 'Technology', '["Technology"]', name, '[]', description, '{}', status))
    return eid


def _seed_claim(conn, subject_id, predicate, content, status='candidate', confidence=0.5,
                doc='d1', chunk='ch1', offset=0):
    cid = str(uuid.uuid4())
    conn.execute('''INSERT INTO claims(id,subject_id,predicate,object_text,content,confidence,status,
                    source_document_id,source_chunk_id,source_start_offset,source_end_offset)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                 (cid, subject_id, predicate, content, content, confidence, status,
                  doc, chunk, offset, offset + 4))
    return cid


def _seed_relation(conn, source_id, target_id, predicate='uses', doc='d1', chunk='ch1'):
    rid = str(uuid.uuid4())
    conn.execute('''INSERT INTO relations(id,source_id,predicate,target_id,status,created_by,
                    source_document_id,source_chunk_id,source_start_offset,source_end_offset)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''',
                 (rid, source_id, predicate, target_id, 'verified', 'normalizer',
                  doc, chunk, 0, 4))
    return rid


# ---------------------------------------------------------------- search


def test_search_returns_quote_cards_not_whole_chunks(tmp_path):
    _reload(tmp_path)
    import app.db as db
    from app.tools.knowledge_tools import search_knowledge

    conn = db.connect()
    _seed_doc_chunk(conn, body='RAG overview. ' + 'y' * 4000)
    conn.commit()
    conn.close()

    out = json.loads(search_knowledge('RAG'))
    assert out['count'] >= 1
    card = out['results'][0]
    # The full chunk body never enters the tool result...
    assert 'content' not in card
    assert len(card['quote']) < 300 and card['quote'].endswith(('…', 'method.'))
    # ...but every id needed for drill-down survives.
    assert card['chunk_id'] and card['document_id'] and card['title'] == 'RAG Survey'


def test_search_result_is_valid_json_and_bounded_by_limit(tmp_path):
    _reload(tmp_path)
    import app.db as db
    from app.tools.knowledge_tools import search_knowledge

    conn = db.connect()
    for i in range(3):
        _seed_doc_chunk(conn, doc_id=f'd{i}', chunk_id=f'ch{i}', body=f'RAG part {i}. ')
    conn.commit()
    conn.close()

    out = json.loads(search_knowledge('RAG', limit=2))
    assert out['count'] <= 2
    assert all(set(r) >= {'chunk_id', 'document_id', 'quote'} for r in out['results'])


# ---------------------------------------------------------------- get_entity


def test_get_entity_caps_claims_ranks_verified_first_and_reports_total(tmp_path):
    _reload(tmp_path)
    import app.db as db
    from app.tools.knowledge_tools import get_entity

    conn = db.connect()
    _seed_doc_chunk(conn)
    eid = _seed_entity(conn, 'RAG', description='retrieval ' * 300, status='verified')
    for i in range(12):
        _seed_claim(conn, eid, 'improves', f'claim {i}',
                    status='verified' if i == 11 else 'candidate',
                    confidence=0.9 if i == 11 else 0.1 + i / 100)
    conn.commit()
    conn.close()

    card = json.loads(get_entity(eid))
    assert card['entity_id'] == eid and card['status'] == 'verified'
    assert len(card['description']) < 320                 # description is clipped
    assert card['claims_total'] == 12
    assert len(card['claims']) == 8                       # capped, not the 12-row dump
    assert card['claims'][0]['status'] == 'verified'      # ranked first by the program
    assert 'hint' in card and 'more claims omitted' in card['hint']
    assert all({'id', 'predicate', 'content', 'status'} <= set(c) for c in card['claims'])


def test_get_entity_not_found_is_a_stable_error(tmp_path):
    _reload(tmp_path)
    from app.tools.knowledge_tools import get_entity

    assert json.loads(get_entity('missing')) == {'error': 'Entity not found'}


# ---------------------------------------------------------------- get_entities


def test_get_entities_returns_batch_cards_and_reports_missing_ids(tmp_path):
    _reload(tmp_path)
    import app.db as db
    from app.tools.knowledge_tools import get_entities

    conn = db.connect()
    _seed_doc_chunk(conn)
    e1 = _seed_entity(conn, 'RAG', status='verified')
    e2 = _seed_entity(conn, 'AgentScope')
    for i in range(4):
        _seed_claim(conn, e1, 'improves', f'claim {i}', confidence=0.5)
    conn.commit()
    conn.close()

    out = json.loads(get_entities([e1, e2, 'not-an-id'], include_claims=2))
    assert out['count'] == 2 and out['not_found'] == ['not-an-id']
    by_id = {c['entity_id']: c for c in out['results']}
    assert by_id[e1]['name'] == 'RAG' and len(by_id[e1]['claims']) == 2
    assert by_id[e1]['claims_total'] == 4
    assert 'claims' not in by_id[e2]                      # include_claims=2, entity has none


def test_get_entities_enforces_the_batch_limit(tmp_path):
    _reload(tmp_path)
    import app.db as db
    from app.tools.knowledge_tools import get_entities

    out = json.loads(get_entities([f'id{i}' for i in range(10)]))
    assert out['count'] == 0
    assert out['truncated'] == 2 and 'batch limit' in out['hint']
    assert len(out['not_found']) == 8


def test_get_entities_rejects_an_empty_id_list(tmp_path):
    _reload(tmp_path)
    from app.tools.knowledge_tools import get_entities

    assert 'error' in json.loads(get_entities([]))


# ---------------------------------------------------------------- graph


def test_graph_drops_descriptions_caps_nodes_and_keeps_ids(tmp_path):
    _reload(tmp_path)
    import app.db as db
    from app.tools.knowledge_tools import get_entity_graph

    conn = db.connect()
    _seed_doc_chunk(conn)
    root = _seed_entity(conn, 'RAG', description='long ' * 200, status='verified')
    for i in range(30):
        neighbor = _seed_entity(conn, f'N{i:02d}', description='long ' * 200)
        _seed_relation(conn, root, neighbor)
    conn.commit()
    conn.close()

    out = json.loads(get_entity_graph(root, depth=1))
    assert len(out['nodes']) <= 25                        # capped, not the 31-node dump
    assert all('description' not in n for n in out['nodes'])
    assert out['nodes'][0]['id'] == root                  # root survives first
    assert out['truncated'] == 31 - len(out['nodes']) and 'hint' in out
    assert all({'predicate', 'source', 'target'} <= set(e) for e in out['edges'])
    assert all('source_document_id' not in e for e in out['edges'])


# ---------------------------------------------------------------- catalogue


def test_batch_tool_stays_invisible_to_extraction_intent(tmp_path):
    _reload(tmp_path)
    from app.context.providers.tools import ToolProvider
    from app.runtime import TaskContext, features_for
    from app.runtime.task import INTENT_CURATE, INTENT_EXTRACT

    provider = ToolProvider()
    extract = TaskContext(task_type='extract', agent='ExtractionAgent',
                          features=features_for('ExtractionAgent', 'extract'))
    curate = TaskContext(task_type='curate', agent='CuratorAgent',
                         features=features_for('CuratorAgent', INTENT_CURATE))

    assert provider.select(extract) == ['list_skills', 'read_skill_reference']
    assert 'get_entities' in provider.select(curate)
