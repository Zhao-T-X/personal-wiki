"""P0-1: natural-language revisit/research questions must not fall to UNKNOWN.

These are the sentences a real user types on first open or, more importantly, when
they come *back* ("最近我记录了什么"). The classifier previously only knew "是什么 /
什么是 / 是谁" and dropped bare "什么" / "谁", so "最近我记录了什么" had no route —
the single most important retention trigger was dead.

The fix only widened the question-shape vocabulary; it did not add any workflow or
write path, so routing still maps every recognised intent onto an *existing* endpoint.
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
    'app.repositories.base', 'app.repositories.document_repo', 'app.repositories.entity_repo',
    'app.repositories.claim_repo', 'app.repositories.relation_repo', 'app.intent',
    'app.retrieval', 'app.workflows.ask_workflow',
)


# --- shape-level: no database needed ----------------------------------------

def test_revisit_questions_are_recognised_not_unknown():
    """The eight sentences from the P0-1 spec each resolve to a known intent."""
    from app.intent import ASK, CORRECT, RESEARCH, UNKNOWN, classify

    expected = {
        '最近我记录了什么': ASK,
        '这篇文章讲了什么': ASK,
        '我最近记了什么': ASK,
        '之前保存了什么': ASK,
        '这篇东西说了什么': ASK,
        '苹果现在 CEO 是谁': ASK,
        '这个答案不对': CORRECT,
        '帮我研究一下这个问题': RESEARCH,
    }
    for sentence, intent in expected.items():
        result = classify(sentence)
        assert result.intent == intent, sentence
        assert result.intent != UNKNOWN, f'{sentence!r} must not be unknown'
        assert result.steps, f'{sentence!r} must name how to carry it out'


def test_revisit_questions_route_to_existing_endpoints():
    from app.intent import classify

    expected = {
        '最近我记录了什么': '/api/ask',
        '这篇文章讲了什么': '/api/ask',
        '苹果现在 CEO 是谁': '/api/ask',
        '这个答案不对': '/api/knowledge/corrections',
        '帮我研究一下这个问题': '/api/research',
    }
    for sentence, endpoint in expected.items():
        steps = classify(sentence).steps
        assert [s['endpoint'] for s in steps] == [endpoint], sentence


def test_plain_statements_are_not_misread_as_questions():
    """Widening the vocabulary must not turn ordinary statements into QA."""
    from app.intent import ASK, classify

    for sentence in (
        '苹果 CEO 是库克',                       # a statement, not a question
        '这里没什么要记的',                       # "没什么" is "nothing", not "what"
        '没啥好补充的',
        '我已经把这篇文章存进知识库了',
        '今天天气不错，和我的知识库无关',
    ):
        assert classify(sentence).intent != ASK, sentence


def test_writing_intents_still_confirm_before_executing():
    from app.intent import CORRECT, KNOWLEDGE, RESEARCH, classify

    assert classify('这个答案不对').needs_confirmation is True
    assert classify('帮我研究一下这个问题').needs_confirmation is True
    assert classify('帮我记住这个').needs_confirmation is True
    assert classify('这个答案不对').intent == CORRECT


# --- endpoint-level: the HTTP surface the frontend actually calls -----------

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


def test_endpoint_resolves_revisit_sentences(tmp_path):
    _reload_db(tmp_path)
    from fastapi.testclient import TestClient
    from app.main import app

    expected = {
        '最近我记录了什么': 'ask',
        '这篇文章讲了什么': 'ask',
        '我最近记了什么': 'ask',
        '之前保存了什么': 'ask',
        '这篇东西说了什么': 'ask',
        '苹果现在 CEO 是谁': 'ask',
        '这个答案不对': 'correct',
        '帮我研究一下这个问题': 'research',
    }
    for sentence, intent in expected.items():
        body = TestClient(app).post('/api/onebox/intent', json={'text': sentence}).json()
        assert body['intent'] == intent, sentence
        assert body['steps'], sentence
