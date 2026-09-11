"""Context Runtime P2c: history compression (spec §7).

History = the recent 3-5 messages verbatim + a compact summary of everything
older. The provider stays silent for single-turn tasks (zero cost), and emits
at most two items when a transcript exists.
"""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD_MODULES = (
    'app.context.tokens',
    'app.context.history',
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
    importlib.reload(app.config)
    for name in _RELOAD_MODULES:
        importlib.reload(importlib.import_module(name))


def _transcript(n: int, filler: str = 'x' * 400) -> list[dict]:
    return [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'msg {i} {filler}'}
            for i in range(n)]


def test_recent_window_kept_verbatim_and_older_compressed(tmp_path):
    _reload(tmp_path)
    from app.context.history import compress_history

    msgs = _transcript(10)
    out = compress_history(msgs)
    assert out['recent'] == msgs[-4:]                    # verbatim, spec §7
    assert out['stats']['messages_compressed'] == 6
    assert '6 earlier messages compressed' in out['summary']
    assert 'user goal:' in out['summary']
    assert 'last action:' in out['summary']


def test_compression_really_saves_tokens_on_long_transcripts(tmp_path):
    _reload(tmp_path)
    from app.context.history import compress_history

    out = compress_history(_transcript(12))
    assert out['stats']['tokens_before'] > out['stats']['tokens_after'] * 2


def test_short_history_needs_no_compression(tmp_path):
    _reload(tmp_path)
    from app.context.history import compress_history

    msgs = _transcript(3)
    out = compress_history(msgs)
    assert out['recent'] == msgs
    assert out['summary'] == ''                          # nothing older to compress
    assert out['stats']['messages_compressed'] == 0


def test_empty_history_is_free(tmp_path):
    _reload(tmp_path)
    from app.context.history import compress_history

    out = compress_history([])
    assert out['recent'] == [] and out['summary'] == ''
    assert out['stats']['tokens_before'] == 0


def test_provider_is_silent_without_a_transcript(tmp_path):
    _reload(tmp_path)
    from app.context.providers.history import HistoryProvider
    from app.runtime.task import TaskContext, features_for

    task = TaskContext(task_type='ask', agent='KnowledgeAgent',
                       features=features_for('KnowledgeAgent', 'ask'))
    assert HistoryProvider().provide(task, None) == []


def test_provider_emits_summary_and_recent_window(tmp_path):
    _reload(tmp_path)
    from app.context.items import TYPE_HISTORY
    from app.context.providers.history import HistoryProvider
    from app.runtime.task import TaskContext, features_for

    task = TaskContext(task_type='ask', agent='KnowledgeAgent',
                       features=features_for('KnowledgeAgent', 'ask'),
                       history=_transcript(9))
    items = HistoryProvider().provide(task, None)

    assert len(items) == 2
    assert all(i.type == TYPE_HISTORY for i in items)
    summary = next(i for i in items if i.id == 'history:summary')
    recent = next(i for i in items if i.id == 'history:recent')
    assert '5 earlier messages compressed' in summary.content
    assert summary.metadata['tokens_after'] < summary.metadata['tokens_before']
    assert 'msg 8' in recent.content and 'msg 5' in recent.content
    assert 'msg 0' not in recent.content                 # older content only in the summary
