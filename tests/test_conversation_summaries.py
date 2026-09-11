"""Context Runtime P3: conversation_summaries persistence.

The stored row *is* the compressed conversation state (rolling summary +
recent window), so a new turn needs one row read and one upsert and a full
transcript is never stored or replayed (success criterion 5).
"""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD_MODULES = (
    'app.context.tokens',
    'app.context.history',
    'app.context.history_store',
    'app.runtime.task',
    'app.context.policies',
    'app.context.items',
    'app.context.planner',
    'app.context.providers.history',
)


def _reload(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'wiki.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config
    import app.db
    importlib.reload(app.config)
    importlib.reload(app.db)
    for name in _RELOAD_MODULES:
        importlib.reload(importlib.import_module(name))
    app.db.init_db()


def test_first_turn_starts_empty_and_persists(tmp_path):
    _reload(tmp_path)
    from app.context.history_store import begin_turn, end_turn, load_conversation

    state = begin_turn('c1', 'PersonalAgent', '我在调研 RAG 的落地方式')
    assert state['summary'] == ''                        # nothing older yet
    assert state['recent'] == [{'role': 'user', 'content': '我在调研 RAG 的落地方式'}]
    assert state['messages_seen'] == 1

    saved = end_turn('c1', 'PersonalAgent', state, 'RAG 是检索增强生成，先检索再生成。')
    assert saved['messages_seen'] == 2

    row = load_conversation('c1', 'PersonalAgent')
    assert row is not None
    # Everything still fits in the recent window, so the summary is empty by
    # design (spec §7: summary covers only what left the window).
    assert row['summary'] == ''
    assert [m['content'] for m in row['recent']] == ['我在调研 RAG 的落地方式',
                                                     'RAG 是检索增强生成，先检索再生成。']
    assert row['messages_seen'] == 2


def test_second_turn_sees_the_first_exchange(tmp_path):
    _reload(tmp_path)
    from app.context.history_store import begin_turn, end_turn
    from app.context.providers.history import HistoryProvider
    from app.runtime.task import TaskContext, features_for

    state = begin_turn('c2', 'PersonalAgent', 'RAG 和微调该怎么选？')
    end_turn('c2', 'PersonalAgent', state, '看数据量与时效性：知识频繁变化选 RAG。')

    turn2 = begin_turn('c2', 'PersonalAgent', '那向量库选型呢？')
    assert turn2['summary'] == ''                        # window still holds everything
    assert turn2['recent'][0]['content'] == 'RAG 和微调该怎么选？'
    assert turn2['recent'][-1]['role'] == 'user'         # current message verbatim

    task = TaskContext(task_type='chat', agent='PersonalAgent',
                       features=features_for('PersonalAgent', 'chat'),
                       history=turn2['recent'], history_summary=turn2['summary'])
    items = {i.id: i for i in HistoryProvider().provide(task, None)}
    # Persisted path: the recent window comes from conversation_summaries; no
    # summary item yet because nothing has left the window.
    assert 'history:summary' not in items
    assert items['history:recent'].source == 'conversation_summaries'
    assert '向量库选型' in items['history:recent'].content
    assert '看数据量与时效性' in items['history:recent'].content


def test_window_stays_bounded_over_many_turns(tmp_path):
    _reload(tmp_path)
    from app.context.history_store import begin_turn, end_turn, load_conversation

    for n in range(6):
        state = begin_turn('c3', 'KnowledgeAgent', f'问题 {n}')
        end_turn('c3', 'KnowledgeAgent', state, f'回答 {n}')

    row = load_conversation('c3', 'KnowledgeAgent')
    assert row['messages_seen'] == 12
    assert len(row['recent']) <= 4                       # the window never grows
    assert '问题 0' in row['summary']                    # but nothing is silently lost
    assert '回答 5' in row['summary'] or any(
        m['content'] == '回答 5' for m in row['recent'])


def test_state_survives_a_reconnect(tmp_path):
    _reload(tmp_path)
    from app.context.history_store import begin_turn, end_turn, load_conversation

    state = begin_turn('c4', 'ResearchAgent', '对比 Qdrant 与 pgvector')
    end_turn('c4', 'ResearchAgent', state, '两者都可行，取决于运维成本。')
    again = load_conversation('c4', 'ResearchAgent')
    assert again is not None and again['messages_seen'] == 2
    flattened = again['summary'] + ' '.join(m['content'] for m in again['recent'])
    assert 'Qdrant' in flattened and 'pgvector' in flattened


def test_summary_is_capped_on_long_running_conversations(tmp_path):
    _reload(tmp_path)
    from app.context.history_store import MAX_SUMMARY_TOKENS, begin_turn, end_turn, load_conversation
    from app.context.tokens import estimate_tokens

    long_user = '请分析这个超长问题：' + '细节' * 1200
    for n in range(8):
        state = begin_turn('c5', 'ResearchAgent', f'{long_user} #{n}')
        end_turn('c5', 'ResearchAgent', state, f'结论 {n}：' + '分析' * 800)

    row = load_conversation('c5', 'ResearchAgent')
    assert estimate_tokens(row['summary']) <= MAX_SUMMARY_TOKENS + 8
    assert len(row['recent']) <= 4


def test_provider_without_persisted_summary_still_compresses(tmp_path):
    _reload(tmp_path)
    from app.context.providers.history import HistoryProvider
    from app.runtime.task import TaskContext, features_for

    task = TaskContext(task_type='chat', agent='PersonalAgent',
                       features=features_for('PersonalAgent', 'chat'),
                       history=[{'role': 'user', 'content': f'm{i}'} for i in range(8)])
    items = {i.id: i for i in HistoryProvider().provide(task, None)}
    assert items['history:summary'].source == 'context.history.compress_history'
    assert '4 earlier messages compressed' in items['history:summary'].content


def test_workflow_threads_conversation_state_end_to_end(tmp_path):
    _reload(tmp_path)
    import app.workflows.agent_workflow as workflow

    calls: list[dict] = []

    async def fake_ask(message, *, history_summary='', history=None):
        calls.append({'message': message, 'history_summary': history_summary,
                      'history': list(history or [])})
        return f'reply-to:{message}'

    monkeypatched = dict(workflow.ROLES)
    monkeypatched['personal'] = fake_ask
    workflow.ROLES = monkeypatched
    try:
        import asyncio
        first = asyncio.run(workflow.run_agent('第一问', 'personal', 'conv-x'))
        second = asyncio.run(workflow.run_agent('第二问', 'personal', 'conv-x'))
    finally:
        workflow.ROLES = dict(monkeypatched)

    assert calls[0]['history_summary'] == ''
    # Turn 2: the first exchange is still inside the recent window (verbatim),
    # so the summary is empty by design until the window overflows.
    assert calls[1]['history_summary'] == ''
    assert calls[1]['history'][0]['content'] == '第一问'
    assert any(m['content'] == 'reply-to:第一问' for m in calls[1]['history'])
    assert first['conversation_id'] == 'conv-x'
    assert second['history']['messages_seen'] == 4
