from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD_MODULES = (
    'app.prompt_profiles',
    'app.runlog',
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
)


def _reload_db(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'wiki.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    os.environ['AGENT_PROMPTS_PATH'] = str(tmp_path / 'agent_prompts.json')
    import app.config
    import app.db
    importlib.reload(app.config)
    importlib.reload(app.db)
    for name in _RELOAD_MODULES:
        importlib.reload(importlib.import_module(name))
    app.db.init_db()
    return app.db


def test_record_context_persists_run_and_sections(tmp_path):
    _reload_db(tmp_path)
    from app.context import ContextManager, get_context_run, list_context_runs, record_context

    cm = ContextManager('KnowledgeAgent')
    cm.add('bootstrap', 'You are KnowledgeAgent.', required=True)
    cm.add('evidence', 'quote: RAG improves grounding.', summarizable=True)
    context = cm.build()

    context_run_id = record_context(context)
    assert context_run_id

    runs = list_context_runs()
    assert len(runs) == 1
    assert runs[0]['agent_name'] == 'KnowledgeAgent'
    assert runs[0]['budget_tokens'] == 3000
    assert runs[0]['actual_tokens'] == context.total_tokens
    assert runs[0]['over_budget'] == 0

    detail = get_context_run(context_run_id)
    assert detail is not None
    assert [s['name'] for s in detail['sections']] == ['bootstrap', 'evidence']
    assert detail['sections'][0]['policy'] == 'LOAD'
    assert detail['sections'][0]['tokens'] > 0
    assert detail['sections'][0]['source'] == ''


def test_trace_records_over_budget_and_optimizations(tmp_path):
    _reload_db(tmp_path)
    from app.context import ContextManager, list_context_runs, record_context

    cm = ContextManager('KnowledgeAgent', budget=20)
    cm.add('bootstrap', 'x' * 400, required=True)
    context = cm.build()

    record_context(context)
    row = list_context_runs()[0]

    assert row['over_budget'] == 1
    assert row['actual_tokens'] == 100
    assert any('over budget' in action for action in row['optimizations'])


def test_trace_links_to_the_current_run(tmp_path):
    _reload_db(tmp_path)
    from app.context import ContextManager, list_context_runs, record_context
    from app.runlog import record_run

    with record_run('agent', agent_role='KnowledgeAgent') as run:
        cm = ContextManager('KnowledgeAgent')
        cm.add('bootstrap', 'You are KnowledgeAgent.', required=True)
        record_context(cm.build())
        run_id = run.id

    rows = list_context_runs()
    assert len(rows) == 1
    assert rows[0]['run_id'] == run_id


def test_trace_failure_is_swallowed(tmp_path):
    db = _reload_db(tmp_path)
    conn = db.connect()
    conn.execute('DROP TABLE context_sections')
    conn.commit()
    conn.close()

    from app.context import ContextManager, record_context

    cm = ContextManager('KnowledgeAgent')
    cm.add('bootstrap', 'You are KnowledgeAgent.', required=True)

    assert record_context(cm.build()) is None       # no exception escapes


def test_withheld_cost_is_persisted_and_readable(tmp_path):
    _reload_db(tmp_path)
    from app.context import (COMPILER, PLANNER, get_context_run, list_context_runs,
                             record_context)
    from app.context.items import ContextItem
    from app.context.policies import Load
    from app.runtime import TaskContext, features_for

    plan = PLANNER.plan(TaskContext(task_type='chat', agent='KnowledgeAgent',
                                    features=features_for('KnowledgeAgent', 'chat')))
    compiled = COMPILER.compile([
        ContextItem(id='b', type='bootstrap', content='identity', required=True),
        ContextItem(id='lazy', type='reference', content='', load_policy=Load.RETRIEVE_LATER,
                    metadata={'estimated_tokens': 777}),
    ], plan)
    record_context(compiled)

    row = list_context_runs()[0]
    assert row['withheld'][0]['id'] == 'lazy'
    assert row['withheld'][0]['tokens'] == 777

    detail = get_context_run(row['id'])
    assert detail is not None
    assert detail['withheld'][0]['reason'] == 'retrieve_later'
    assert [s['name'] for s in detail['sections']] == ['bootstrap']


def test_get_context_run_returns_none_for_unknown_id(tmp_path):
    _reload_db(tmp_path)
    from app.context import get_context_run

    assert get_context_run('does-not-exist') is None


def test_list_context_runs_filters_by_agent(tmp_path):
    _reload_db(tmp_path)
    from app.context import ContextManager, list_context_runs, record_context

    for agent in ('KnowledgeAgent', 'ReviewAgent'):
        cm = ContextManager(agent)
        cm.add('bootstrap', f'You are {agent}.', required=True)
        record_context(cm.build())

    only_review = list_context_runs(agent_name='ReviewAgent')
    assert len(only_review) == 1
    assert only_review[0]['agent_name'] == 'ReviewAgent'
    assert len(list_context_runs()) == 2
