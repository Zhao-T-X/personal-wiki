from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD_MODULES = (
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
    'app.context.providers.base',
    'app.context.providers.knowledge',
    'app.context.providers',
    'app.context',
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
    return app.db


def test_summary_line_carries_name_types_and_a_short_description(tmp_path):
    _reload(tmp_path)
    from app.context.providers import EntitySummary

    summary = EntitySummary(name='RAG', types=['Technology', 'Method'],
                            summary='A method and technology combining retrieval with generation.')
    assert summary.line() == ('- RAG [Technology/Method] — A method and technology combining '
                              'retrieval with generation.')


def test_long_descriptions_are_truncated_not_loaded_whole(tmp_path):
    _reload(tmp_path)
    from app.context.providers import EntitySummary

    long_text = 'x' * 5000
    summary = EntitySummary(name='Verbose', types=['Concept'], summary=long_text)
    assert len(summary.line()) < 200


def test_entity_summary_accepts_the_shapes_we_get_from_sql(tmp_path):
    _reload(tmp_path)
    from app.context.providers import EntitySummary

    assert EntitySummary.from_dict({'name': 'A', 'type': 'Concept'}).types == ['Concept']
    assert EntitySummary.from_dict({'name': 'B', 'types': 'Software'}).types == ['Software']
    assert EntitySummary.from_dict({'name': 'C', 'description': 'desc'}).summary == 'desc'


def test_knowledge_items_are_empty_without_summaries(tmp_path):
    _reload(tmp_path)
    from app.context.providers import knowledge_items

    assert knowledge_items([]) == []
    assert knowledge_items([__import__('app.context.providers', fromlist=['EntitySummary'])
                            .EntitySummary(name='')]) == []


def test_knowledge_item_is_summary_first_not_a_full_profile(tmp_path):
    _reload(tmp_path)
    from app.context.providers import EntitySummary, knowledge_items

    items = knowledge_items([EntitySummary(name='RAG', types=['Technology'], summary='retrieval + generation')])
    assert len(items) == 1
    item = items[0]
    assert item.type == 'knowledge'
    assert item.source == 'knowledge_store.entity_summaries'
    assert 'summary-first' in item.reason
    assert item.estimated_tokens < 40
    assert 'claims' not in item.content and 'relations' not in item.content


def test_knowledge_provider_respects_the_entity_limit(tmp_path):
    _reload(tmp_path)
    from app.context import PLANNER
    from app.context.providers import EntitySummary, KnowledgeProvider
    from app.runtime import TaskContext, features_for

    summaries = [EntitySummary(name=f'E{i}', types=['Concept'], summary=f'about {i}') for i in range(10)]
    task = TaskContext(task_type='ask', agent='KnowledgeAgent',
                       features=features_for('KnowledgeAgent', 'ask'))
    items = KnowledgeProvider(summaries).provide(task, PLANNER.plan(task))

    assert len(items) == 1
    assert items[0].metadata['entities'] == ['E0', 'E1', 'E2']    # 3 by default, not 10


def test_ask_plan_budgets_the_evidence_block(tmp_path):
    _reload(tmp_path)
    from app.context import PLANNER
    from app.context.items import TYPE_EVIDENCE, TYPE_HISTORY, TYPE_KNOWLEDGE
    from app.runtime import TaskContext, features_for

    plan = PLANNER.plan(TaskContext(task_type='ask', agent='KnowledgeAgent',
                                    features=features_for('KnowledgeAgent', 'ask')))

    assert {s.type for s in plan.sections} == {TYPE_KNOWLEDGE, TYPE_EVIDENCE, TYPE_HISTORY}
    assert plan.section(TYPE_EVIDENCE).budget > plan.section(TYPE_KNOWLEDGE).budget
    assert plan.section(TYPE_KNOWLEDGE).provider == 'knowledge'
    assert plan.section(TYPE_EVIDENCE).provider == 'evidence'
    assert sum(s.budget for s in plan.sections) <= plan.hard_budget
