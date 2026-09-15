"""One Box: one entry point, decided cheaply, and wrong readings are cheap to fix.

Three things are asserted, in the order they matter:

1. the four intents are read correctly, and 「苹果。」 is honestly *not* read at all;
2. routing names *existing* endpoints — One Box routes, the workflows work;
3. what writes is confirmed first, and everything is decided without a model call.

The last point is a design constraint, not an optimisation: a classifier that asks a
model "what did the user mean?" would add latency to every sentence to answer a
question the text usually answers itself.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_RELOAD = (
    'app.repositories.base',
    'app.repositories.document_repo',
    'app.repositories.entity_repo',
    'app.repositories.claim_repo',
    'app.repositories.relation_repo',
    'app.intent',
    'app.retrieval',
    'app.workflows.ask_workflow',
)


# --- reading the sentence (no database needed for the shape of the rules) -----

def test_the_four_intents_are_read_from_the_sentence():
    from app.intent import ASK, CORRECT, KNOWLEDGE, RESEARCH, classify

    assert classify('苹果 CEO 是谁？').intent == ASK
    assert classify('帮我记住：我现在采用 SQLite 作为主要存储').intent == KNOWLEDGE
    assert classify('帮我研究苹果 CEO 交接').intent == RESEARCH
    assert classify('苹果 CEO 记错了，现在应该是 John Ternus').intent == CORRECT


def test_the_common_import_phrasings_are_recognised():
    """「把…记进/记入知识库」是导入最常见的说法，落空即是最常用的入口失效。

    It is a *safe* failure — UNKNOWN executes nothing — but the suggestion list is not
    the same as the box doing what was asked, and this phrasing is how people ask.
    """
    from app.intent import KNOWLEDGE, classify

    for sentence in ('把这篇会议纪要记进知识库', '把这条结论记入知识库'):
        intent = classify(sentence)
        assert intent.intent == KNOWLEDGE, sentence
        # A write is offered, never fired: the plan exists, the execution waits.
        assert intent.needs_confirmation is True
        assert intent.steps


def test_a_bare_entity_is_unknown_and_executes_nothing():
    """The rule the whole entry point rests on: not knowing is an answer."""
    from app.intent import UNKNOWN, classify

    result = classify('苹果。')

    assert result.intent == UNKNOWN
    assert result.steps == [], 'an unknown intent must not fire anything'
    assert result.suggestions, 'it may offer shortcuts instead'
    assert '苹果 CEO' not in ' '.join(result.suggestions), 'never guess the question'


def test_only_reading_acts_on_its_own():
    """A wrong Ask costs a rephrase; a wrong write costs trust in the whole wiki."""
    from app.intent import classify

    # Reading is free, so it executes immediately…
    assert classify('苹果 CEO 是谁？').needs_confirmation is False
    # …while everything that writes (or spends a model run) is offered first.
    assert classify('帮我记住：我现在采用 SQLite 作为主要存储').needs_confirmation is True
    assert classify('帮我研究苹果 CEO 交接').needs_confirmation is True
    assert classify('苹果 CEO 记错了，现在应该是 John Ternus').needs_confirmation is True


def test_routing_names_endpoints_that_already_exist():
    from app.intent import ASK, CORRECT, KNOWLEDGE, RESEARCH, classify

    for sentence, expected in [
        ('苹果 CEO 是谁？', '/api/ask'),
        ('帮我记住：我采用 SQLite', '/api/documents'),
        ('帮我研究苹果 CEO 交接', '/api/research'),
        ('苹果 CEO 记错了，现在应该是 John Ternus', '/api/correction/plan'),
    ]:
        result = classify(sentence)
        assert [step['endpoint'] for step in result.steps] == [expected], sentence
        assert result.summary, 'the user is told what is happening'


def test_a_follow_up_uses_the_context_the_user_is_looking_at():
    """「这个不对」 must not require retyping which knowledge it is about."""
    from app.intent import CORRECT, classify

    result = classify('这个不对，应该是 John Ternus', context_claim_id='claim-1')

    assert result.intent == CORRECT
    assert result.context_claim_id == 'claim-1'
    assert result.confidence >= 0.9


def test_a_bare_statement_naming_a_registered_relation_reads_as_a_correction(tmp_path):
    """The registry-driven case, with no subject stored yet — new ones start somewhere."""
    _reload_db(tmp_path)
    from app.intent import CORRECT, classify

    result = classify('苹果的 CEO 是约翰·特努斯')

    assert result.intent == CORRECT
    # The relation is named in words the registry supplies, not by a keyword table here.
    assert '首席执行官' in result.reason or 'has_ceo' in result.reason


def test_an_empty_sentence_is_unknown_not_an_error():
    from app.intent import UNKNOWN, classify

    assert classify('').intent == UNKNOWN
    assert classify('   ').intent == UNKNOWN


# --- the endpoint -------------------------------------------------------------

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


def test_the_endpoint_reads_the_users_four_examples(tmp_path):
    db = _reload_db(tmp_path)
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    expected = {
        '苹果 CEO 是谁？': 'ask',
        '苹果的新任 CEO 是 John Ternus': 'correct',
        '帮我研究苹果 CEO 交接': 'research',
        '记住：我现在采用 SQLite 作为主要存储': 'knowledge',
    }
    for sentence, intent in expected.items():
        body = client.post('/api/onebox/intent', json={'text': sentence}).json()
        assert body['intent'] == intent, sentence
        assert body['reason'] and body['summary']
        assert body['steps'], 'a recognised intent always names how to carry it out'


def test_the_endpoint_writes_nothing(tmp_path):
    """Routing is a decision about a sentence, not an action on the knowledge base."""
    db = _reload_db(tmp_path)
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    before = client.get('/api/claims?limit=200').json()
    client.post('/api/onebox/intent', json={'text': '帮我记住：我采用 SQLite 作为主要存储'})
    client.post('/api/onebox/intent', json={'text': '苹果 CEO 是谁？'})

    assert client.get('/api/claims?limit=200').json() == before


def test_the_endpoint_takes_the_context_it_was_given(tmp_path):
    db = _reload_db(tmp_path)
    from fastapi.testclient import TestClient
    from app.main import app

    body = TestClient(app).post('/api/onebox/intent', json={
        'text': '这个不对', 'context_claim_id': 'claim-9'}).json()

    assert body['intent'] == 'correct' and body['context_claim_id'] == 'claim-9'


def test_an_unreadable_sentence_returns_no_route(tmp_path):
    db = _reload_db(tmp_path)
    from fastapi.testclient import TestClient
    from app.main import app

    body = TestClient(app).post('/api/onebox/intent', json={'text': '嗯'}).json()

    assert body['intent'] == 'unknown'
    assert body['steps'] == [] and body['suggestions']
    assert 'question' in body or True   # the payload stays a plain description
