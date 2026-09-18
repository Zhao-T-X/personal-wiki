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


# ------------------------------------------------------------------ planning

def test_planner_produces_budgets_and_keeps_references_lazy(tmp_path):
    _reload_db(tmp_path)
    from app.context import PLANNER as planner
    from app.context.items import (TYPE_EXTRACTION_CONTEXT, TYPE_REFERENCE, TYPE_SKILL,
                                   TYPE_TOOLS)
    from app.runtime import TaskContext, features_for

    task = TaskContext(task_type='extract', agent='ExtractionAgent', skill='knowledge-extraction',
                       features=features_for('ExtractionAgent', 'extract'))
    plan = planner.plan(task)

    assert plan.hard_budget == 3500                    # ExtractionAgent budget
    assert plan.target_budget == 2625                  # 75% target
    assert plan.reserve_tokens == 875                  # the rest is reserved
    assert plan.section(TYPE_SKILL).required is True
    assert plan.section(TYPE_TOOLS).provider == 'tools'
    reference = plan.section(TYPE_REFERENCE)
    assert reference.lazy is True and reference.budget == 0
    # Step 15: the prefetched extraction context is the opposite of a lazy reference —
    # it is loaded content that replaces the fetch, so it gets a real budget and a cap.
    prefetch = plan.section(TYPE_EXTRACTION_CONTEXT)
    assert prefetch is not None
    assert prefetch.lazy is False and prefetch.budget > 0

    trace = plan.to_trace()
    assert trace['agent'] == 'ExtractionAgent'
    assert {s['type'] for s in trace['sections']} == {
        'bootstrap', 'skill', 'reference', 'extraction_context', 'tools', 'custom',
        'constraints'}


def test_reasoning_level_adapts_to_task_signals(tmp_path):
    _reload_db(tmp_path)
    from app.runtime import features_for, reasoning_level

    assert reasoning_level(features_for('PersonalAgent', 'chat')) <= 1
    assert reasoning_level(features_for('ResearchAgent', 'research')) >= 3

    confused = features_for('KnowledgeAgent', 'ask', complexity=0.9, conflict_level=0.5,
                            knowledge_coverage=0.2, evidence_requirement=0.95)
    assert reasoning_level(confused) == 5              # capped
    assert reasoning_level(features_for('KnowledgeAgent', 'ask')) == 1


def test_budget_override_still_wins_over_the_planner_default(tmp_path):
    _reload_db(tmp_path)
    from app.context import PLANNER
    from app.runtime import TaskContext, features_for

    task = TaskContext(task_type='chat', agent='KnowledgeAgent',
                       features=features_for('KnowledgeAgent', 'chat'))
    assert PLANNER.plan(task).hard_budget == 3000
    assert PLANNER.plan(task, overrides={'KnowledgeAgent': 1500}).hard_budget == 1500


# --------------------------------------------------------------------- value

def test_value_prefers_the_small_quote_over_the_big_chunk(tmp_path):
    _reload_db(tmp_path)
    from app.context import rank, utility
    from app.context.items import ContextItem

    quote = ContextItem(id='q', type='evidence', content='RAG improves grounding.',
                        evidence_strength=0.9, relevance=0.9)
    chunk = ContextItem(id='c', type='evidence', content='RAG improves grounding. ' * 200,
                        evidence_strength=0.9, relevance=0.9)

    assert utility(quote) > utility(chunk)
    assert [i.id for i in rank([chunk, quote])] == ['q', 'c']


def test_required_items_rank_before_larger_optional_ones(tmp_path):
    _reload_db(tmp_path)
    from app.context import rank
    from app.context.items import ContextItem

    core = ContextItem(id='core', type='bootstrap', content='short contract', required=True)
    fat = ContextItem(id='fat', type='knowledge', content='x ' * 400, relevance=1.0,
                      information_gain=1.0, priority=1.0)
    assert rank([fat, core])[0].id == 'core'


# ------------------------------------------------------------------ compiler

def test_compiler_dedups_and_withholds(tmp_path):
    _reload_db(tmp_path)
    from app.context import COMPILER, PLANNER
    from app.context.items import ContextItem
    from app.context.policies import Load
    from app.runtime import TaskContext, features_for

    plan = PLANNER.plan(TaskContext(task_type='chat', agent='KnowledgeAgent',
                                    features=features_for('KnowledgeAgent', 'chat')))
    items = [
        ContextItem(id='a', type='skill', content='[SKILL]\nsame contract', required=True),
        ContextItem(id='b', type='skill', content='[SKILL]\nsame  contract', required=True),
        ContextItem(id='lazy', type='reference', content='', load_policy=Load.RETRIEVE_LATER,
                    metadata={'estimated_tokens': 400}),
        ContextItem(id='never', type='sqlite_schema', content='CREATE TABLE x(a)',
                    load_policy=Load.NEVER_LOAD),
    ]
    item = ContextItem(id='bootstrap', type='bootstrap', content='identity', required=True)
    compiled = COMPILER.compile(items + [item], plan)

    ids = [i.id for i in compiled.items]
    assert 'a' in ids and 'b' not in ids                 # duplicate collapsed
    assert 'lazy' not in ids and 'never' not in ids
    reasons = {w['id']: w['reason'] for w in compiled.withheld}
    assert reasons['b'] == 'duplicate'
    assert reasons['lazy'] == 'retrieve_later'
    assert reasons['never'] == 'never_load'
    assert compiled.saved_tokens >= 400                  # the lazy cost is reported
    assert 'CREATE TABLE' not in compiled.render()


def test_compiler_enforces_section_budget(tmp_path):
    _reload_db(tmp_path)
    from app.context import COMPILER, PLANNER
    from app.context.items import TYPE_TOOLS, ContextItem
    from app.runtime import TaskContext, features_for

    plan = PLANNER.plan(TaskContext(task_type='chat', agent='KnowledgeAgent',
                                    features=features_for('KnowledgeAgent', 'chat')))
    plan.section(TYPE_TOOLS).budget = 20
    items = [
        ContextItem(id='big', type=TYPE_TOOLS, content='- tool: ' + 'x' * 400, priority=0.9,
                    relevance=0.9, information_gain=0.9),
        ContextItem(id='small', type=TYPE_TOOLS, content='- tiny: ok', priority=0.5, relevance=0.5),
    ]
    compiled = COMPILER.compile(items, plan)

    assert [i.id for i in compiled.items] == ['small']
    assert any(w['id'] == 'big' and w['reason'] == 'section_budget' for w in compiled.withheld)


def test_compiler_reports_waste_only_above_the_target_budget(tmp_path):
    _reload_db(tmp_path)
    from app.context import COMPILER, PLANNER
    from app.context.items import ContextItem
    from app.runtime import TaskContext, features_for

    plan = PLANNER.plan(TaskContext(task_type='chat', agent='KnowledgeAgent',
                                    features=features_for('KnowledgeAgent', 'chat')))
    small = COMPILER.compile([ContextItem(id='b', type='bootstrap', content='identity', required=True)], plan)
    assert small.waste == 0.0 and small.over_budget is False

    # 1000 tokens of history against a target of 2250 is fine; against a tiny target it is waste.
    plan.target_budget = 10
    fat = COMPILER.compile(
        [ContextItem(id='b', type='bootstrap', content='identity', required=True),
         ContextItem(id='h', type='history', content='turn ' * 500)], plan)
    assert fat.waste > 0.9


# ----------------------------------------------------------------- providers

def test_skill_provider_loads_the_contract_and_reports_reference_costs(tmp_path):
    _reload_db(tmp_path)
    from app.context import PLANNER, REGISTRY
    from app.context.items import TYPE_REFERENCE, TYPE_SKILL
    from app.runtime import TaskContext, features_for

    task = TaskContext(task_type='extract', agent='ExtractionAgent', skill='knowledge-extraction',
                       features=features_for('ExtractionAgent', 'extract'))
    items = REGISTRY.collect(task, PLANNER.plan(task))

    contract = [i for i in items if i.type == TYPE_SKILL]
    assert len(contract) == 1 and contract[0].required and contract[0].version

    lazy_refs = [i for i in items if i.type == TYPE_REFERENCE]
    assert lazy_refs, 'the reference catalogue must be visible as lazy items'
    assert all(i.metadata.get('estimated_tokens', 0) > 0 for i in lazy_refs)
    assert all(not i.content for i in lazy_refs)


def test_skill_provider_inlines_only_requested_references(tmp_path):
    _reload_db(tmp_path)
    from app.context import PLANNER, REGISTRY
    from app.context.items import TYPE_REFERENCE
    from app.runtime import TaskContext, features_for

    features = features_for('ExtractionAgent', 'extract', references=['evidence.md'])
    task = TaskContext(task_type='extract', agent='ExtractionAgent', skill='knowledge-extraction',
                       features=features)
    items = REGISTRY.collect(task, PLANNER.plan(task))

    loaded = [i for i in items if i.type == TYPE_REFERENCE and i.content]
    assert [i.source.split('/')[-1] for i in loaded] == ['evidence.md']


def test_tool_provider_matches_tools_to_the_intent(tmp_path):
    _reload_db(tmp_path)
    from app.context.providers.tools import ToolProvider
    from app.runtime import TaskContext, features_for

    provider = ToolProvider()
    extract = TaskContext(task_type='extract', agent='ExtractionAgent',
                          features=features_for('ExtractionAgent', 'extract'))
    chat = TaskContext(task_type='chat', agent='PersonalAgent',
                       features=features_for('PersonalAgent', 'chat'))

    assert provider.select(extract) == ['list_skills', 'read_skill_reference']
    assert 'search_knowledge' in provider.select(chat)
    assert len(provider.select(chat)) > len(provider.select(extract))


# ------------------------------------------------------- end-to-end assembly

def test_build_context_keeps_the_prompt_contract_and_withholds_references(tmp_path):
    _reload_db(tmp_path)
    from app.prompt_profiles import build_context

    compiled = build_context('extractor')
    rendered = compiled.render()

    assert '[SKILL]' in rendered
    assert '[NON-NEGOTIABLE]' in rendered
    assert '## Reference:' not in rendered                    # nothing inlined by default
    withheld = [w for w in compiled.to_trace()['withheld'] if w['reason'] == 'retrieve_later']
    assert withheld and sum(w['tokens'] for w in withheld) > 2000
    # Was <400 before Step 13.1. The extraction SKILL now carries the `object_kind`
    # semantics and the "prefer the most specific registered predicate" rule (+~200
    # tokens); the live semantic smoke measured that addition turning 7/10 into 9/10
    # semantic correctness (docs/development/step13-1-semantic-gold.md). The guard keeps
    # its purpose — the extractor prompt stays small and every heavy reference stays lazy.
    assert compiled.total_tokens < 700
    assert compiled.efficiency > 0.15


def test_build_context_records_a_plan_and_withheld_cost_in_the_trace(tmp_path):
    db = _reload_db(tmp_path)
    from app.prompt_profiles import build_context

    build_context('knowledge', persist=True)
    conn = db.connect()
    run = conn.execute('SELECT * FROM context_runs').fetchone()
    sections = conn.execute('SELECT * FROM context_sections ORDER BY section_index').fetchall()
    conn.close()

    assert run['agent_name'] == 'KnowledgeAgent'
    assert run['budget_tokens'] == 3000
    assert run['over_budget'] == 0
    names = [s['name'] for s in sections]
    assert names[0] == 'bootstrap' and 'skill' in names
    assert all(s['policy'] in ('LOAD', 'RETRIEVE_LATER') for s in sections)
