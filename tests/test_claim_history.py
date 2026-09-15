"""Knowledge evolution is a chain that is *stored*, not reconstructed.

Two halves are pinned here. The ordering is pure logic — a chain has an oldest
member, a current member and, if the data is malformed, a cycle, and none of that
depends on the order rows come back in. The endpoint then reads the chain back with
the real timestamps that say when each statement stopped being true, which is what
makes "what did this used to say?" answerable at all (ADR-005: superseding moves a
lifecycle status; it never deletes).
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
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
)


# --- ordering is pure logic ---------------------------------------------------

def _edge(newer: str, older: str, at: str = '2026-01-01 00:00:00') -> dict:
    return {'source_claim_id': newer, 'target_claim_id': older, 'created_at': at}


def test_chain_is_ordered_oldest_first_wherever_you_enter_it():
    """A caller arriving from search does not know where in the timeline it landed."""
    from app.domain.claim_history import order_chain

    edges = [_edge('c', 'b'), _edge('b', 'a')]
    for seed in ('a', 'b', 'c'):
        history = order_chain(edges, seed)
        assert history.ordered_ids == ['a', 'b', 'c'], seed
        assert history.current_id == 'c'
        assert history.superseded_count == 2
        assert history.cycles is False


def test_a_claim_with_no_history_is_a_chain_of_one():
    from app.domain.claim_history import order_chain

    history = order_chain([], 'solo')
    assert history.ordered_ids == ['solo']
    assert history.current_id == 'solo'
    assert history.superseded_count == 0


def test_unrelated_edges_do_not_join_the_chain():
    from app.domain.claim_history import order_chain

    history = order_chain([_edge('c', 'b'), _edge('x', 'y')], 'c')
    assert history.ordered_ids == ['b', 'c']


def test_a_malformed_cycle_is_reported_not_hung_on():
    """An impossible history is a fact about the data, so it is surfaced."""
    from app.domain.claim_history import order_chain

    history = order_chain([_edge('a', 'b'), _edge('b', 'a')], 'a')
    assert history.cycles is True
    assert set(history.ordered_ids) <= {'a', 'b'}


def test_malformed_edges_are_ignored():
    from app.domain.claim_history import order_chain

    history = order_chain([{'source_claim_id': None, 'target_claim_id': 'b'},
                           {'source_claim_id': 'x', 'target_claim_id': None},
                           {'source_claim_id': 'z', 'target_claim_id': 'z'}], 'seed')
    assert history.ordered_ids == ['seed']


# --- the endpoint reads the stored chain back ---------------------------------

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


def _seed_chain(db):
    """One fact through three statements: Tim Cook → John Ternus → Alice.

    Written as knowledge, not as edits: each newer claim supersedes the older one
    through an *accepted* relation, exactly as the correction workflow leaves it.
    """
    from app.service import create_document, write_chunks

    text = '苹果公司的首席执行官。'
    doc_id = create_document(title='苹果公司简介', content=text, source_type='note',
                             source_uri=None, metadata={})
    chunk_id = write_chunks(doc_id, text)[0]['id']
    conn = db.connect()
    people = {'苹果公司': ('e-apple', 'Organization'), '蒂姆·库克': ('e-tim', 'Person'),
              '约翰·特努斯': ('e-john', 'Person'), '爱丽丝': ('e-alice', 'Person')}
    for name, (eid, etype) in people.items():
        conn.execute('INSERT INTO entities(id,type,types_json,name,aliases_json,properties_json,status) '
                     'VALUES(?,?,?,?,?,?,?)',
                     (eid, etype, json.dumps([etype]), name, '[]', '{}', 'verified'))

    def claim(obj_id, obj_text, status, quote):
        cid = str(uuid.uuid4())
        conn.execute(
            '''INSERT INTO claims(id,subject_id,predicate,object_id,object_text,content,context_json,
                   claim_type,polarity,modality,confidence,status,created_by,source_document_id,
                   source_chunk_id,source_start_offset,source_end_offset,source_quote)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (cid, 'e-apple', 'has_ceo', obj_id, obj_text, text, '{}', 'factual', 'positive',
             'asserted', 0.9, status, 'llm', doc_id, chunk_id, 0, len(text), quote))
        return cid

    # The same statement asserted by a second source: a corroborating duplicate.
    tim = claim('e-tim', '蒂姆·库克', 'superseded', '苹果公司的首席执行官是蒂姆·库克。')
    tim_again = claim('e-tim', '蒂姆·库克', 'verified', '据另一来源，首席执行官是蒂姆·库克。')
    john = claim('e-john', '约翰·特努斯', 'superseded', '苹果的新任首席执行官是约翰·特努斯。')
    alice = claim('e-alice', '爱丽丝', 'candidate', '苹果的首席执行官是爱丽丝。')

    def relation(newer, older, relationship='supersedes'):
        conn.execute('INSERT INTO claim_relations(id,source_claim_id,target_claim_id,relationship,'
                     'confidence,reason,status,created_by) VALUES(?,?,?,?,?,?,?,?)',
                     (str(uuid.uuid4()), newer, older, relationship, 0.95, 'test', 'accepted', 'test'))

    relation(john, tim)                 # John supersedes Tim
    relation(alice, john)               # Alice supersedes John
    relation(tim_again, tim, 'duplicate')
    conn.commit()
    conn.close()
    return {'tim': tim, 'john': john, 'alice': alice, 'tim_again': tim_again}


def _client(db):
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


def test_history_returns_the_chain_oldest_first_with_effective_periods(tmp_path):
    db = _reload_db(tmp_path)
    ids = _seed_chain(db)
    body = _client(db).get(f"/api/claims/{ids['john']}/history").json()

    # Entered in the middle, still returned in order.
    assert [n['object'] for n in body['chain']] == ['蒂姆·库克', '约翰·特努斯', '爱丽丝']
    assert body['current_id'] == ids['alice']
    assert body['superseded_count'] == 2
    assert body['cycles'] is False
    assert body['chain'][0]['predicate_label'] == '首席执行官'

    oldest, middle, newest = body['chain']
    # 生效期间: a statement held from the moment it was written until a later one
    # was accepted — real timestamps, not a reconstruction.
    assert oldest['effective_from'] and oldest['effective_to']
    assert oldest['superseded_by'] == ids['john']
    assert oldest['status'] == 'superseded'
    # 依据: its own quote plus the source that asserted the same thing again.
    assert oldest['sources'] == 2 and oldest['corroborating'] == 1

    assert middle['is_current'] is False and middle['effective_to']
    assert newest['is_current'] is True and newest['effective_to'] is None
    assert newest['sources'] == 1

    assert _client(db).get('/api/claims/missing/history').status_code == 404


def test_history_ignores_a_supersede_that_was_never_accepted(tmp_path):
    """A suggestion is not history. Only a human's acceptance moves a lifecycle."""
    db = _reload_db(tmp_path)
    ids = _seed_chain(db)
    conn = db.connect()
    conn.execute('INSERT INTO claim_relations(id,source_claim_id,target_claim_id,relationship,'
                 'status,created_by) VALUES(?,?,?,?,?,?)',
                 (str(uuid.uuid4()), ids['tim'], ids['alice'], 'supersedes', 'candidate', 'system'))
    conn.commit()
    conn.close()

    body = _client(db).get(f"/api/claims/{ids['alice']}/history").json()
    assert [n['object'] for n in body['chain']] == ['蒂姆·库克', '约翰·特努斯', '爱丽丝']
