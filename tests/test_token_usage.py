from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD_MODULES = (
    'app.prompt_profiles',
    'app.runlog',
    'app.llm',
    'app.agent_usage',
    'app.skills',
    'app.runtime.task',
    'app.runtime',
    'app.context.tokens',
    'app.context.budget',
    'app.context.policies',
    'app.context.items',
    'app.context.value',
    'app.context.manager',
    'app.context.planner',
    'app.context.compiler',
    'app.context.trace',
    'app.context.packet',
    'app.context.providers.base',
    'app.context.providers.skills',
    'app.context.providers.tools',
    'app.context.providers',
    'app.context',
    'app.main',
)


def _reload_db(tmp_path, with_main: bool = False):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'wiki.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    os.environ['AGENT_PROMPTS_PATH'] = str(tmp_path / 'agent_prompts.json')
    import app.config
    import app.db
    importlib.reload(app.config)
    importlib.reload(app.db)
    for name in _RELOAD_MODULES:
        if name == 'app.main' and not with_main:
            continue
        importlib.reload(importlib.import_module(name))
    app.db.init_db()
    return app.db


class _FakeUsage:
    prompt_tokens = 1234
    completion_tokens = 56


class _FakeMessage:
    content = 'grounded answer [doc:abc chunk:def]'


class _FakeResponse:
    choices = [type('C', (), {'message': _FakeMessage})()]
    usage = _FakeUsage()


class _FakeClient:
    chat = type('Chat', (), {'completions': type('Comp', (), {'create': lambda self, **kw: _FakeResponse})()})()


def test_answer_records_provider_usage(tmp_path, monkeypatch):
    db = _reload_db(tmp_path)
    import app.llm as llm

    monkeypatch.setattr(llm, '_client', lambda: _FakeClient())
    llm.answer('what is RAG?', 'evidence text')

    conn = db.connect()
    step = conn.execute('SELECT * FROM llm_run_steps').fetchone()
    run = conn.execute('SELECT * FROM llm_runs').fetchone()
    conn.close()

    assert step['prompt_tokens'] == 1234
    assert step['completion_tokens'] == 56
    assert step['usage_source'] == 'provider'
    assert run['prompt_tokens'] == 1234          # run aggregates its steps
    assert run['completion_tokens'] == 56
    assert run['status'] == 'success'


def test_agent_usage_channel_lands_on_the_step(tmp_path):
    db = _reload_db(tmp_path)
    from app.agent_usage import record
    from app.runlog import record_run

    with record_run('extract', agent_role='ExtractionAgent') as run:
        with run.step('extract_batch', input_summary='batch 1/1'):
            record({'prompt_tokens': 900, 'completion_tokens': 300, 'usage_source': 'provider'})

    conn = db.connect()
    step = conn.execute('SELECT * FROM llm_run_steps').fetchone()
    conn.close()
    assert step['prompt_tokens'] == 900
    assert step['completion_tokens'] == 300
    assert step['usage_source'] == 'provider'


def test_step_starts_with_a_clean_usage_channel(tmp_path):
    db = _reload_db(tmp_path)
    from app.agent_usage import record
    from app.runlog import record_run

    record({'prompt_tokens': 111, 'completion_tokens': 22, 'usage_source': 'provider'})
    with record_run('agent') as run:
        with run.step('agent_reply', input_summary='hello'):
            pass                                  # nobody reports usage for this step

    conn = db.connect()
    step = conn.execute('SELECT * FROM llm_run_steps').fetchone()
    conn.close()
    assert step['prompt_tokens'] is None          # stale usage did not leak in
    assert step['usage_source'] is None


def test_collect_sums_the_context_diff_and_falls_back_to_the_reply(tmp_path):
    _reload_db(tmp_path)
    from app.agent_usage import collect

    def msg(prompt, completion):
        return type('M', (), {'usage': type('U', (), {'input_tokens': prompt, 'output_tokens': completion})()})()

    context = [msg(10, 1), msg(20, 2)]
    agent = type('A', (), {'state': type('S', (), {'context': context})()})()

    # before=0 → every message of the reply counts (ReAct: several model calls)
    assert collect(agent, None, 0) == {'prompt_tokens': 30, 'completion_tokens': 3, 'usage_source': 'provider'}
    # before=1 → only the messages appended by this reply
    assert collect(agent, None, 1)['prompt_tokens'] == 20

    # no inspectable context → fall back to the returned message
    blind = type('A', (), {})()
    assert collect(blind, msg(7, 3), None) == {'prompt_tokens': 7, 'completion_tokens': 3, 'usage_source': 'provider'}

    # nothing usable → marked unavailable instead of pretending it is zero-cost
    empty = type('A', (), {'state': type('S', (), {'context': []})()})()
    assert collect(empty, None, 0)['usage_source'] == 'unavailable'


def test_context_metrics_endpoint_reports_both_ledgers(tmp_path):
    db = _reload_db(tmp_path, with_main=True)
    from fastapi.testclient import TestClient

    from app.context import ContextManager, record_context
    from app.main import app
    from app.runlog import record_run

    with record_run('ask', agent_role='ask') as run:
        with run.step('answer', input_summary='q') as step:
            step.set_usage({'prompt_tokens': 800, 'completion_tokens': 200, 'usage_source': 'provider'})

    cm = ContextManager('KnowledgeAgent')
    cm.add('bootstrap', 'You are KnowledgeAgent.', required=True)
    record_context(cm.build())

    with TestClient(app) as client:
        data = client.get('/api/context/metrics?days=7').json()

    assert data['window_days'] == 7
    agent = next(a for a in data['agents'] if a['agent_name'] == 'KnowledgeAgent')
    assert agent['calls'] == 1
    assert agent['avg_context_tokens'] == 6          # estimated system-prompt cost
    assert agent['over_budget_calls'] == 0

    task = next(t for t in data['by_task_type'] if t['task_type'] == 'ask')
    assert task['prompt_tokens'] == 800
    assert task['completion_tokens'] == 200
    assert task['total_tokens'] == 1000
    assert task['tokens_per_run'] == 1000
    assert task['provider_steps'] == 1

    assert data['totals']['prompt_tokens'] == 800
    assert data['totals']['completion_tokens'] == 200
