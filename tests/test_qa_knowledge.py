"""Knowledge QA: the answer is the answer, and the support is knowledge.

The layering is the whole point, and it is asserted rather than assumed:

    Answer   -> prose, because a person asked a question
    Knowledge-> the stored claims that justify it, current first, capped
    Evidence -> where those claims were read from

Two rules decide most of these tests. The answer must never be rendered as a
knowledge card (a sentence is not a triple), and **less is more**: QA's job is not to
show how much the wiki holds, it is to make one sentence believable — so support is
collapsed to one entry per fact, ranked, and capped.

The last test is the one that makes the feature trustworthy: after a correction, the
very next question must be answered from the *new* claim. A background fix the answer
does not reflect is worse than no fix at all.
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
    'app.repositories.curation_repo',
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
    'app.integrity',
    'app.knowledge',
    'app.claim_relations',
    'app.service',
    'app.retrieval',
    'app.workflows.ask_workflow',
)

QUESTION = '苹果公司现在的 CEO 是谁？'
PAST_QUESTION = '苹果公司之前的 CEO 是谁？'


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


def _seed(tmp_path, *, current: bool = True):
    """苹果公司 with a current CEO, and the one it replaced."""
    db = _reload_db(tmp_path)
    from app.ontology import normalize_name
    from app.service import create_document, write_chunks

    documents, chunks = {}, {}
    for key, (title, text) in {
        'now': ('苹果公司公告', '苹果公司 的首席执行官是 约翰·特努斯。'),
        'past': ('旧公告', '苹果公司 的前任首席执行官是 蒂姆·库克。'),
    }.items():
        doc_id = create_document(title=title, content=text, source_type='note',
                                 source_uri=None, metadata={})
        documents[key] = doc_id
        chunks[key] = write_chunks(doc_id, text)[0]

    entities = {
        '苹果公司': ('e-apple', 'Organization', ['苹果']),
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

    def claim(key, *, predicate, object_id, status, text, object_text=None):
        cid = str(uuid.uuid4())
        chunk = chunks[key]
        conn.execute(
            '''INSERT INTO claims(id,subject_id,predicate,object_id,object_text,content,context_json,
                   claim_type,polarity,modality,confidence,status,created_by,source_document_id,
                   source_chunk_id,source_start_offset,source_end_offset,source_quote)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (cid, 'e-apple', predicate, object_id, object_text, text, '{}', 'factual', 'positive',
             'asserted', 0.9, status, 'llm', documents[key], chunk['id'],
             chunk['start_offset'], chunk['end_offset'], text))
        return cid

    ids = {
        'now': claim('now', predicate='has_ceo', object_id='e-john', status='verified',
                     text='苹果公司 的首席执行官是 约翰·特努斯。'),
        'past': claim('past', predicate='has_ceo', object_id='e-tim', status='superseded',
                      text='苹果公司 的前任首席执行官是 蒂姆·库克。'),
        # A predicate the registry does not name: pre-registry data, still stored.
        'legacy': claim('now', predicate='legacy_pred', object_id=None, status='verified',
                        text='苹果公司 的 legacy_pred 是 某物。', object_text='某物'),
    }
    if not current:
        conn.execute("UPDATE claims SET status='superseded' WHERE id=?", (ids['now'],))
    conn.commit()
    conn.close()
    return db, ids, documents


def _client(db):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def _ask(db, question):
    return _client(db).post('/api/ask', json={'question': question}).json()


def _no_llm(monkeypatch):
    """A direct lookup must not touch the model; prove it by making it fatal."""
    import app.llm

    def _boom(*args, **kwargs):
        raise AssertionError('this path must not call the LLM')

    monkeypatch.setattr(app.llm, '_client', _boom, raising=False)


# --- a fact question: one answer, one supporting claim ------------------------

def test_a_fact_question_is_supported_by_exactly_one_claim(tmp_path, monkeypatch):
    db, ids, _ = _seed(tmp_path)
    _no_llm(monkeypatch)

    body = _ask(db, QUESTION)

    assert body['status'] == 'answered' and body['direct'] is True
    assert body['answer']                      # still prose, still the主角
    assert len(body['knowledge']) == 1
    support = body['knowledge'][0]
    assert support['claim_id'] == ids['now']
    assert support['subject_label'] == '苹果公司'
    assert support['predicate_label'] == '首席执行官'
    assert support['object_label'] == '约翰·特努斯'
    assert support['state'] == 'current' and support['status_label'] == '当前'
    # …and it traces back, so 「查看依据」 has somewhere to go.
    assert support['evidence']['document_id'] and support['evidence']['chunk_id']


def test_history_is_usable_as_support_but_marked_as_such(tmp_path, monkeypatch):
    """A past-tense answer is grounded in a superseded claim — kept, not deleted."""
    db, ids, _ = _seed(tmp_path)
    _no_llm(monkeypatch)

    body = _ask(db, PAST_QUESTION)

    assert body['status'] == 'answered'
    assert body['knowledge'] and body['knowledge'][0]['claim_id'] == ids['past']
    assert body['knowledge'][0]['state'] == 'historical'
    assert body['knowledge'][0]['status_label'] == '历史'


def test_no_current_claim_refuses_and_offers_no_support(tmp_path, monkeypatch):
    """Refusing is the answer; inventing support for it would be the lie."""
    db, _, _ = _seed(tmp_path, current=False)
    _no_llm(monkeypatch)

    body = _ask(db, QUESTION)

    assert body['status'] == 'no_sufficient_evidence'
    assert body['answer'] and body['answer_value'] is None
    assert body['knowledge'] == []


def test_no_evidence_at_all_has_an_empty_knowledge_layer(tmp_path):
    db, _, _ = _seed(tmp_path)
    body = _ask(db, '完全不相关的问题 xyzzy')

    assert body['status'] == 'no_evidence'
    assert body['knowledge'] == [] and body['evidence'] == []


# --- several claims: minimal, ordered, capped ---------------------------------

def test_a_multi_claim_answer_shows_the_few_that_support_it(tmp_path):
    """Support is a short list, not an inventory: one entry per fact, current first."""
    db, ids, _ = _seed(tmp_path)
    from app.workflows.ask_workflow import supporting_knowledge

    support = supporting_knowledge([ids['past'], ids['now'], ids['legacy']], question=QUESTION)

    # Current before historical, and both CEO claims present.
    assert [s['state'] for s in support] == ['current', 'current', 'historical']
    assert {s['claim_id'] for s in support} == {ids['now'], ids['past'], ids['legacy']}
    # Capped: a wiki that knows twenty things still answers with a few.
    capped = supporting_knowledge([ids['past'], ids['now'], ids['legacy']], question=QUESTION, limit=1)
    assert len(capped) == 1 and capped[0]['claim_id'] == ids['now']


def test_the_same_fact_asserted_twice_is_one_piece_of_support(tmp_path):
    db, ids, _ = _seed(tmp_path)
    from app.workflows.ask_workflow import supporting_knowledge

    support = supporting_knowledge([ids['now'], ids['now']], question=QUESTION)

    assert len(support) == 1 and support[0]['sources'] == 1


def test_an_unnamed_predicate_never_becomes_prose(tmp_path):
    """The identifier may exist in the data; it must not be offered as a name."""
    db, ids, _ = _seed(tmp_path)
    from app.workflows.ask_workflow import supporting_knowledge

    support = supporting_knowledge([ids['legacy']])
    legacy = support[0]

    assert legacy['predicate'] == 'legacy_pred'
    assert legacy['predicate_label'] is None
    # Nothing a caller could render as a name for it.
    assert 'legacy_pred' not in (legacy['subject_label'] + legacy['object_label'])


# --- the generated path still carries support ---------------------------------

def test_a_generated_answer_also_carries_its_support(tmp_path, monkeypatch):
    """The LLM path is not allowed to be the one place with nothing behind it."""
    db, ids, _ = _seed(tmp_path)
    import app.main
    monkeypatch.setattr(app.main, 'answer',
                        lambda question, ctx, context=None: '苹果公司目前的 CEO 是约翰·特努斯。')

    # A research marker keeps this off the 0-LLM route, so the retrieval + generation
    # path runs — with a stubbed model, since grounding is not what this test measures.
    body = _ask(db, '预测苹果公司未来的 CEO 会如何变化？')

    assert body['direct'] is False
    assert body['answer'] == '苹果公司目前的 CEO 是约翰·特努斯。'
    assert body['knowledge'], 'a generated answer must state what it was grounded in'
    # Carried in words, not identifiers — and the fact the answer used is among them.
    labels = {k['predicate']: k['predicate_label'] for k in body['knowledge']}
    assert labels.get('has_ceo') == '首席执行官'


# --- correction -> the next answer ------------------------------------------

def test_a_correction_changes_the_very_next_answer(tmp_path, monkeypatch):
    """The acceptance criterion for the whole loop: no stale answer survives a fix."""
    db, ids, documents = _seed(tmp_path)
    client = _client(db)
    _no_llm(monkeypatch)

    before = client.post('/api/ask', json={'question': QUESTION}).json()
    assert before['answer_value'] == '约翰·特努斯'
    assert before['knowledge'][0]['claim_id'] == ids['now']

    # Correct it through the sanctioned operations, not by writing rows: the point is
    # that the *product* path is what the next answer reflects.
    created = client.post('/api/knowledge/operations', json={
        'kind': 'CREATE', 'payload': {
            'subject': '苹果公司', 'predicate': 'has_ceo', 'object': '鲍勃',
            'source_document_id': documents['now'],
            'source_chunk_id': client.get(f"/api/claims/{ids['now']}").json()['source_chunk_id'],
            'source_quote': '苹果公司的首席执行官是鲍勃。'}}).json()
    new_id = created['affected']['claim_id']
    assert client.post('/api/knowledge/operations', json={
        'kind': 'SUPERSEDE', 'payload': {'new_claim_id': new_id, 'old_claim_id': ids['now'],
                                         'apply_supersede': True}}).status_code == 200

    after = client.post('/api/ask', json={'question': QUESTION}).json()

    assert after['answer_value'] == '鲍勃'
    assert after['knowledge'][0]['claim_id'] == new_id
    # The correction is newest, so it is what QA answers from — reported honestly as
    # 待确认 rather than dressed up as settled, because nobody has reviewed it yet.
    assert after['knowledge'][0]['state'] == 'candidate'
    # The replaced value is kept as history (never deleted) — and now that two values
    # have each been the CEO, a past-tense question is honestly ambiguous rather than
    # answered by picking one.
    assert client.get(f"/api/claims/{ids['now']}").json()['status'] == 'superseded'
    past = client.post('/api/ask', json={'question': PAST_QUESTION}).json()
    assert past['status'] == 'no_sufficient_evidence' and past['answer_value'] is None
    assert past['knowledge'] == []
