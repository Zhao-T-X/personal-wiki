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


def test_estimate_tokens_counts_cjk_denser_than_ascii():
    from app.context import estimate_tokens

    assert estimate_tokens(None) == 0
    assert estimate_tokens('') == 0
    assert estimate_tokens('检索质量') == 4          # CJK is ~1 token per character
    assert estimate_tokens('abcd') == 1              # ASCII is ~4 characters per token
    assert estimate_tokens('检索质量 abcd') == 6      # 4 CJK + ceil(5 / 4)


def test_truncate_to_tokens_keeps_prefix_within_budget():
    from app.context.tokens import estimate_tokens, truncate_to_tokens

    text = '\n'.join(f'rule {i}: keep this line intact' for i in range(50))
    out = truncate_to_tokens(text, 60)

    assert estimate_tokens(out) <= 60
    assert out and text.startswith(out)
    assert out.rstrip() == out
    assert truncate_to_tokens(text, 0) == ''
    assert truncate_to_tokens('short', 999) == 'short'


def test_budget_for_agent_defaults_and_overrides():
    from app.context import budget_for

    assert budget_for('ExtractionAgent') == 3500
    assert budget_for('KnowledgeAgent') == 3000
    assert budget_for('NoSuchAgent') == 4000
    assert budget_for('ExtractionAgent', {'ExtractionAgent': 1200}) == 1200
    assert budget_for('ExtractionAgent', {'ExtractionAgent': 0}) == 3500


def test_budgets_can_be_overridden_from_settings(tmp_path):
    _reload_db(tmp_path)
    from app.config import save_settings, runtime
    from app.context import ContextManager

    assert ContextManager('KnowledgeAgent').budget == 3000

    save_settings({'agent_context_budgets': {'KnowledgeAgent': 1234}})
    assert runtime()['agent_context_budgets'] == {'KnowledgeAgent': 1234}
    assert ContextManager('KnowledgeAgent').budget == 1234
    assert ContextManager('ReviewAgent').budget == 3500          # untouched agent

    cm = ContextManager('KnowledgeAgent', budget=99)             # explicit wins
    assert cm.budget == 99


def test_never_load_and_retrieve_later_sections(tmp_path):
    _reload_db(tmp_path)
    from app.context import ContextManager, Load

    cm = ContextManager('KnowledgeAgent')
    assert cm.add('sqlite_schema', 'CREATE TABLE chunks(id TEXT);') is None

    ids = cm.add('evidence_ids', 'chunk-1, chunk-2', id_only=True)
    assert ids is not None and ids.policy is Load.RETRIEVE_LATER and ids.tokens == 0

    cm.add('bootstrap', 'You are KnowledgeAgent.', required=True)
    context = cm.build()

    assert 'CREATE TABLE' not in context.render()
    assert 'chunk-1' not in context.render()
    assert [s['name'] for s in context.trace()['sections']] == ['evidence_ids', 'bootstrap']
    assert context.trace()['sections'][0]['policy'] == 'RETRIEVE_LATER'


def test_empty_sections_are_not_stored(tmp_path):
    _reload_db(tmp_path)
    from app.context import ContextManager

    cm = ContextManager('KnowledgeAgent')
    assert cm.add('evidence', '   ') is None
    assert cm.add('history', None) is None
    assert cm.build().total_tokens == 0


def test_optimize_compresses_low_priority_sections_first(tmp_path):
    _reload_db(tmp_path)
    from app.context import ContextManager

    cm = ContextManager('KnowledgeAgent', budget=200)
    cm.add('bootstrap', 'You are KnowledgeAgent.', required=True)
    cm.add('constraints', 'Do not fabricate stored facts.', required=True)
    cm.add('history', '\n'.join(f'user turn {i} with some talking' for i in range(60)))

    context = cm.build()

    assert context.total_tokens <= 200
    assert context.by_name('bootstrap').content == 'You are KnowledgeAgent.'
    assert context.by_name('constraints').content == 'Do not fabricate stored facts.'
    history = context.by_name('history')
    assert history.trimmed is True and history.saved_tokens > 0
    assert any('history' in action for action in context.optimizations)


def test_optimize_drops_a_section_when_no_room_is_left(tmp_path):
    _reload_db(tmp_path)
    from app.context import ContextManager

    cm = ContextManager('KnowledgeAgent', budget=40)
    cm.add('bootstrap', 'You are KnowledgeAgent.', required=True)
    cm.add('history', '\n'.join(f'user turn {i} with some talking' for i in range(60)))

    context = cm.build()

    assert context.by_name('history').content == ''
    assert context.by_name('history').tokens == 0
    assert any('drop history' in action for action in context.optimizations)


def test_over_budget_is_reported_when_only_task_sections_remain(tmp_path):
    _reload_db(tmp_path)
    from app.context import ContextManager

    cm = ContextManager('KnowledgeAgent', budget=10)
    cm.add('bootstrap', 'x' * 400, required=True)

    context = cm.build()

    assert context.over_budget is True
    assert context.total_tokens > context.budget
    assert context.by_name('bootstrap').content == 'x' * 400          # never trimmed
    assert any('over budget' in action for action in context.optimizations)


def test_custom_section_is_never_trimmed(tmp_path):
    _reload_db(tmp_path)
    from app.context import ContextManager

    cm = ContextManager('PersonalAgent', budget=20)
    custom = 'Always answer in Chinese and cite evidence.'
    cm.add('bootstrap', 'You are PersonalAgent.', required=True)
    cm.add('custom', custom, required=True)
    cm.add('history', '\n'.join(f'turn {i} filler content' for i in range(40)))

    context = cm.build()

    assert context.by_name('custom').content == custom
    assert context.by_name('history').content == ''


def test_compose_prompt_records_sections_and_drops_inlined_registry(tmp_path):
    _reload_db(tmp_path)
    from app.prompt_profiles import compose_prompt, get_profile

    prompt = compose_prompt('extractor', tools=[])

    assert '## Reference: entity-types.md' not in prompt
    assert '## Reference: claim-predicates.md' not in prompt
    assert 'You are the LLM-Wiki knowledge extraction agent' in prompt
    assert '[NON-NEGOTIABLE]' in prompt

    profile = get_profile('extractor')
    names = [s['name'] for s in profile['context_sections']]
    # The profile preview has no tool list bound to it, so the catalogue is planned in full.
    assert names == ['bootstrap', 'skill', 'constraints', 'tools']
    assert profile['context_tokens'] > 0
    assert profile['context_budget'] == 3500


def test_compose_prompt_includes_the_tool_section_when_tools_are_passed(tmp_path):
    _reload_db(tmp_path)
    from app.agents.base import DEFAULT_TOOLS
    from app.context import get_context_run, list_context_runs
    from app.prompt_profiles import compose_prompt

    compose_prompt('knowledge', tools=DEFAULT_TOOLS)

    # The tool catalogue cost is only knowable at call time, so it shows up in the
    # trace rather than in the profile preview.
    detail = get_context_run(list_context_runs()[0]['id'])
    sections = {s['name']: s for s in detail['sections']}
    assert 'tools' in sections, 'tool catalogue cost must be visible in the trace'
    assert sections['tools']['tokens'] > 0
    assert sections['tools']['source'] == 'context.providers.tools.TOOL_CATALOG'
    assert sections['tools']['chars'] > 0


def test_custom_prompt_token_budget_status(tmp_path):
    _reload_db(tmp_path)
    from app.prompt_profiles import get_profile, update_profile

    short = update_profile('extractor', 'Keep answers short and factual.')
    assert short['custom_prompt_tokens'] > 0
    assert short['recommended_max_tokens'] == 600
    assert short['prompt_status'] == 'efficient'
    assert short['prompt_suggestion'] is None

    long_profile = update_profile('extractor', 'rule ' * 600)
    assert long_profile['custom_prompt_tokens'] > 600
    assert long_profile['prompt_status'] == 'long'
    assert 'knowledge-extraction' in (long_profile['prompt_suggestion'] or '')


def test_composition_preview_does_not_write_a_trace(tmp_path):
    db = _reload_db(tmp_path)
    from app.prompt_profiles import composition_preview

    composition_preview('knowledge')

    conn = db.connect()
    count = conn.execute('SELECT COUNT(*) FROM context_runs').fetchone()[0]
    conn.close()
    assert count == 0
