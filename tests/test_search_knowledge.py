"""Search presented as knowledge: what the wiki holds, then where it read that.

The six cases below are the acceptance criteria for the projection, and each one
guards a different way this could go wrong:

A. a current fact is found at all — the query names the subject and the predicate;
B. it is found through the *registry's* words (the label), not the identifier;
C. a superseded fact stays findable, ranked below the current one, not hidden;
D. every result can be traced back claim → chunk → document;
E. a predicate the registry does not name is never turned into prose;
F. one fact extracted from two sources is one result, not two.

Recall is deliberately still lexical: a chunk has to be recalled before its claims
can be projected, so every query here is one the seeded passages can actually be
found by. Widening recall to reach the ontology directly is a separate change, and
these tests would not hide it if it landed.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_RELOAD = (
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
    'app.repositories.operation_repo',
    'app.resolution',
    'app.knowledge',
    'app.claim_relations',
    'app.service',
    'app.retrieval',
)


# --- the projection is pure logic ---------------------------------------------

def test_fold_keeps_chinese_whole():
    """``normalize_predicate`` would erase it; matching must not."""
    from app.readmodels.knowledge_view import fold

    assert fold('  CEO ') == 'ceo'
    assert fold('首席执行官') == '首席执行官'
    assert fold(None) == ''


def test_predicate_keywords_come_from_the_registry():
    from app.readmodels.knowledge_view import predicate_keywords

    keywords = predicate_keywords('has_ceo')
    # The registry declares all four; none of them is a guess made here.
    assert 'has_ceo' in keywords and 'ceo' in keywords
    assert '首席执行官' in keywords and 'chief executive officer' in keywords
    # An unregistered predicate contributes only itself — nothing is invented.
    assert predicate_keywords('legacy_pred') == ('legacy_pred',)


def test_state_precedence_is_deliberate():
    from app.readmodels.knowledge_view import state_of

    assert state_of({'status': 'superseded'}, disputed=True) == 'historical'
    assert state_of({'status': 'verified'}, disputed=True) == 'disputed'
    assert state_of({'status': 'candidate'}) == 'candidate'
    assert state_of({'status': 'verified'}) == 'current'


def test_relevance_counts_every_name_the_query_reaches():
    from app.readmodels.knowledge_view import predicate_keywords, relevance

    claim = {'subject_name': '苹果公司', 'object_name': '约翰·特努斯',
             'content': '苹果公司的首席执行官是约翰·特努斯。'}
    terms = ['apple', 'ceo']
    keywords = predicate_keywords('has_ceo')
    # "CEO" through the registry's alias, even though the sentence is Chinese…
    assert relevance(claim, terms, keywords=keywords) == 1
    # …and "Apple" only once the entity's alias table is in play.
    assert relevance(claim, terms, aliases=['Apple'], keywords=keywords) == 2
    assert relevance(claim, ['不存在的词']) == 0


def test_statement_key_ignores_which_chunk_it_came_from():
    from app.readmodels.knowledge_view import statement_key

    a = {'subject_id': 'e1', 'predicate': 'has_ceo', 'object_id': 'e2', 'source_chunk_id': 'c1'}
    b = {'subject_id': 'e1', 'predicate': 'has_ceo', 'object_id': 'e2', 'source_chunk_id': 'c9'}
    assert statement_key(a) == statement_key(b)
    # A literal object has no id, so its text stands in for one.
    assert statement_key({'subject_id': 'e1', 'predicate': 'contains', 'object_text': 'X'}) \
        == statement_key({'subject_id': 'e1', 'predicate': 'contains', 'object_text': ' x '})


# --- the endpoint projects the stored knowledge -------------------------------

def _reload_db(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config)
    importlib.reload(db)
    for name in _RELOAD:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


def _seed(tmp_path):
    """Three sources about one fact, so both history and corroboration are real."""
    db = _reload_db(tmp_path)
    from app.ontology import normalize_name
    from app.service import create_document, write_chunks

    # Documents first: they open their own transactions, and holding a second write
    # connection open while they run would deadlock against it.
    passages = {
        'current': ('苹果公司公告', 'Apple 的首席执行官是 John Ternus。'),
        'former': ('旧公告', 'Apple 的前任首席执行官是 Tim Cook。'),
        'corroborating': ('另一来源', '据另一来源，Apple 的首席执行官是 John Ternus。'),
    }
    chunks: dict[str, dict] = {}
    documents: dict[str, str] = {}
    for key, (title, text) in passages.items():
        doc_id = create_document(title=title, content=text, source_type='note',
                                 source_uri=None, metadata={})
        documents[key] = doc_id
        chunks[key] = write_chunks(doc_id, text)[0]

    entities = {
        '苹果公司': ('e-apple', 'Organization', ['Apple', '苹果']),
        '约翰·特努斯': ('e-john', 'Person', ['John Ternus']),
        '蒂姆·库克': ('e-tim', 'Person', ['Tim Cook']),
    }
    conn = db.connect()
    for name, (eid, etype, aliases) in entities.items():
        conn.execute('INSERT INTO entities(id,type,types_json,name,aliases_json,properties_json,status) '
                     'VALUES(?,?,?,?,?,?,?)',
                     (eid, etype, json.dumps([etype]), name, json.dumps(aliases), '{}', 'verified'))
        for alias in aliases:
            conn.execute('INSERT OR IGNORE INTO entity_aliases(entity_id,alias,alias_normalized) '
                         'VALUES(?,?,?)', (eid, alias, normalize_name(alias)))

    def claim(key, *, subject, predicate, object_id, object_text, status, text):
        cid = str(uuid.uuid4())
        chunk = chunks[key]
        conn.execute(
            '''INSERT INTO claims(id,subject_id,predicate,object_id,object_text,content,context_json,
                   claim_type,polarity,modality,confidence,status,created_by,source_document_id,
                   source_chunk_id,source_start_offset,source_end_offset,source_quote)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (cid, subject, predicate, object_id, object_text, text, '{}', 'factual', 'positive',
             'asserted', 0.9, status, 'llm', documents[key], chunk['id'],
             chunk['start_offset'], chunk['end_offset'], text))
        return cid

    ids = {
        'current': claim('current', subject='e-apple', predicate='has_ceo', object_id='e-john',
                         object_text=None, status='verified', text=passages['current'][1]),
        'former': claim('former', subject='e-apple', predicate='has_ceo', object_id='e-tim',
                        object_text=None, status='superseded', text=passages['former'][1]),
        # The same fact as `current`, from a different source, still awaiting review.
        'same_fact': claim('corroborating', subject='e-apple', predicate='has_ceo',
                           object_id='e-john', object_text=None, status='candidate',
                           text=passages['corroborating'][1]),
        # A predicate the registry does not declare — pre-registry data, still stored.
        'legacy': claim('current', subject='e-apple', predicate='legacy_pred', object_id=None,
                        object_text='某物', status='verified', text='Apple 的 legacy_pred 是某物。'),
    }
    conn.commit()
    conn.close()
    return db, ids, chunks, documents


def _client(db):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _knowledge(db, q, **params):
    query = '&'.join(f'{k}={v}' for k, v in params.items())
    body = _client(db).get(f'/api/search/knowledge?q={q}' + (f'&{query}' if query else '')).json()
    return body['knowledge'], body['results']


def test_current_knowledge_is_found_by_subject_and_predicate(tmp_path):
    """A. "Apple CEO" — the subject by alias, the predicate by its registry name."""
    db, ids, _, _ = _seed(tmp_path)
    knowledge, results = _knowledge(db, 'Apple%20CEO')

    assert knowledge, 'the current fact must be found'
    top = knowledge[0]
    assert top['claim_id'] == ids['current']
    assert top['subject_label'] == '苹果公司'
    assert top['predicate'] == 'has_ceo'
    assert top['object_label'] == '约翰·特努斯'
    assert top['state'] == 'current' and top['status_label'] == '当前'
    # The passage layer is still there, unchanged, for the same query.
    assert results and 'matched_in' in results[0]


def test_predicate_is_reached_through_its_registry_label(tmp_path):
    """B. The user types 首席执行官; the wiki stores has_ceo. Same search."""
    db, _, _, _ = _seed(tmp_path)
    knowledge, _ = _knowledge(db, '%E9%A6%96%E5%B8%AD%E6%89%A7%E8%A1%8C%E5%AE%98')

    found = [k for k in knowledge if k['predicate'] == 'has_ceo']
    assert found, 'the label must resolve to the registered predicate'
    assert found[0]['predicate_label'] == '首席执行官'
    # Never the identifier dressed up as a label.
    assert found[0]['predicate_label'] != found[0]['predicate']


def test_history_stays_findable_but_ranks_below_the_current_fact(tmp_path):
    """C. Search is not a current-state query — it must find, then rank."""
    db, ids, _, _ = _seed(tmp_path)
    knowledge, _ = _knowledge(db, 'Apple%20Tim%20Cook')

    by_state = {k['object_label']: k['state'] for k in knowledge}
    assert by_state.get('蒂姆·库克') == 'historical', 'the former CEO must still be found'
    assert by_state.get('约翰·特努斯') == 'current'
    # Current first — the example the ordering rule was written for.
    assert knowledge[0]['object_label'] == '约翰·特努斯'
    assert knowledge[-1]['object_label'] == '蒂姆·库克'
    assert knowledge[-1]['status_label'] == '历史'


def test_every_result_traces_back_to_its_document(tmp_path):
    """D. claim → chunk → document must actually resolve, not just be filled in."""
    db, _, chunks, documents = _seed(tmp_path)
    knowledge, _ = _knowledge(db, 'Apple%20CEO')
    top = knowledge[0]

    evidence = top['evidence']
    assert evidence['chunk_id'] and evidence['document_id'] and evidence['document_title']
    client = _client(db)
    assert client.get(f"/api/documents/{evidence['document_id']}").status_code == 200
    chunk_ids = {c['id'] for c in client.get(f"/api/documents/{evidence['document_id']}/chunks").json()}
    assert evidence['chunk_id'] in chunk_ids
    assert evidence['document_id'] == documents['current']
    assert evidence['chunk_id'] == chunks['current']['id']


def test_unnamed_predicate_never_becomes_prose(tmp_path):
    """E. Pre-registry data is shown honestly, not by printing its identifier."""
    db, ids, _, _ = _seed(tmp_path)
    knowledge, _ = _knowledge(db, 'Apple%20CEO')

    legacy = [k for k in knowledge if k['claim_id'] == ids['legacy']]
    assert legacy, 'the claim is stored, so it is still projected'
    assert legacy[0]['predicate'] == 'legacy_pred'
    assert legacy[0]['predicate_label'] is None
    # Nothing a caller could accidentally render as a name for it.
    assert legacy[0]['status_label'] == '当前'


def test_one_fact_from_two_sources_is_one_result(tmp_path):
    """F. Two chunks, two claim rows, one fact — one answer."""
    db, ids, _, _ = _seed(tmp_path)
    knowledge, _ = _knowledge(db, 'Apple%20CEO')

    same = [k for k in knowledge if k['object_label'] == '约翰·特努斯']
    assert len(same) == 1, 'the same statement must not be shown twice'
    # Two stored statements back it, and the better state is the one shown.
    assert same[0]['sources'] == 2
    assert same[0]['state'] == 'current'
    assert same[0]['claim_id'] == ids['current']


def test_the_old_search_contract_is_untouched(tmp_path):
    """`/api/search` still returns a flat list of chunks — callers must not break."""
    db, _, _, _ = _seed(tmp_path)
    body = _client(db).get('/api/search?q=Apple&limit=10').json()

    assert isinstance(body, list)
    assert body and {'id', 'title', 'content', 'document_id'} <= set(body[0])


def test_empty_query_returns_nothing_of_either_kind(tmp_path):
    db, _, _, _ = _seed(tmp_path)
    body = _client(db).get('/api/search/knowledge?q=%20').json()
    assert body == {'knowledge': [], 'results': []}
