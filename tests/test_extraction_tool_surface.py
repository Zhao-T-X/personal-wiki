"""Step 14 — the extraction agent's tool surface.

The Step 13.4 capture showed every extraction request carrying four tools, two of which
the model never called in any recorded run: `Skill` (AgentScope's skill viewer, added by
the skill loader) and `list_skills`. Both were pure cost — and the skill loader also
appended an `<agent-skills>` catalogue to the system prompt that advertised the removed
tool and shipped an absolute local skill path to the provider.

This file pins the surface so it cannot silently grow back. It deliberately asserts
**membership**, never a tool count: a genuinely useful tool must be addable without
editing this test.

Two levels are checked, because they can drift apart:

    toolkit    what ``build_extraction_agent`` registers -> what the provider is sent
    request    what the real provider request actually carried (the capture)

``GenerateStructuredOutput`` only exists at the request level: AgentScope registers it
into the toolkit for the duration of ``reply(structured_schema=...)`` and removes it
again, so it cannot be observed on a toolkit that is not mid-call. Asserting it on the
captured request is the stronger statement anyway — that is what the provider received.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.agents.base import DEFAULT_TOOLS, build_toolkit
from app.agents.extraction_agent import EXTRACTION_TOOLS, build_extraction_agent

FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'extraction_cases'
# Step 13.4 capture — the surface BEFORE Step 14 (four tools, two of them unused).
BEFORE_SNAPSHOT = FIXTURE_DIR / 'provider_request_snapshot.json'
# Step 14 capture — the surface AFTER the slimming.
AFTER_SNAPSHOT = FIXTURE_DIR / 'provider_request_snapshot_step14.json'

# The extraction contract needs these. Neither may be removed.
REQUIRED_TOOLS = {'read_skill_reference', 'GenerateStructuredOutput'}
# Proven unused by the recorded runs and removed in Step 14. Must not come back.
FORBIDDEN_TOOLS = {'Skill', 'list_skills'}


def _toolkit_names(toolkit) -> set[str]:
    """Tool names registered on a toolkit, read from the schemas it would send."""
    schemas = asyncio.run(toolkit.get_tool_schemas())
    return {((s.get('function') or {}).get('name') or s.get('name')) for s in schemas}


def _capture(path: Path) -> dict:
    if not path.exists():
        pytest.skip(f'{path.name} not captured yet: run scripts/capture_provider_request.py')
    return json.loads(path.read_text(encoding='utf-8'))


@pytest.fixture
def extraction_toolkit():
    """The toolkit the extraction agent is built with, without needing a provider key.

    ``build_toolkit`` is the seam ``build_agent`` uses, and it takes the same tool list
    the agent declares — so this exercises the real surface, not a copy of it.
    """
    return build_toolkit(EXTRACTION_TOOLS, skills=False)


# ------------------------------------------------------------------ the toolkit surface

def test_extraction_toolkit_keeps_the_required_tools(extraction_toolkit):
    names = _toolkit_names(extraction_toolkit)
    # GenerateStructuredOutput is added by AgentScope during reply(); see the request-level
    # test below. Everything else the contract needs must be registered here.
    assert {'read_skill_reference'} <= names


def test_extraction_toolkit_drops_the_unused_tools(extraction_toolkit):
    names = _toolkit_names(extraction_toolkit)
    assert names & FORBIDDEN_TOOLS == set(), f'removed tools came back: {names}'
    assert 'list_skills' not in {getattr(fn, '__name__', '') for fn in EXTRACTION_TOOLS}


def test_extraction_agent_registers_no_skill_loader():
    """The agent must be built with the loader off, or the Skill tool rides along.

    Checked through the tool source rather than a tool count: `EXTRACTION_TOOLS` is the
    list the agent passes to `build_agent`, and `skills=False` is what keeps AgentScope
    from adding its skill viewer on top of it.
    """
    source = Path(build_extraction_agent.__code__.co_filename).read_text(encoding='utf-8')
    assert 'skills=False' in source, 'agents/extraction_agent.py must opt out of the skill loader'


def test_no_skill_catalogue_reaches_the_extraction_prompt():
    """The `<agent-skills>` block goes with the loader that produced it.

    Keeping the text while removing the tool would tell the model to use a tool it no
    longer has — and the block carries an absolute local path on every call.
    """
    toolkit = build_toolkit(EXTRACTION_TOOLS, skills=False)
    assert not asyncio.run(toolkit.get_skill_instructions())


def test_other_agents_keep_their_skill_access():
    """Only the extraction agent was slimmed. Guards against a blanket change."""
    default_names = _toolkit_names(build_toolkit())
    assert {'Skill', 'list_skills', 'read_skill_reference'} <= default_names
    assert {'list_skills', 'read_skill_reference'} <= {
        getattr(fn, '__name__', '') for fn in DEFAULT_TOOLS}


# ------------------------------------------------------------------ the real request

def test_removed_tools_are_gone_from_the_real_request():
    """The capture is authoritative: these are the tools the provider was sent."""
    after = _capture(AFTER_SNAPSHOT)
    for capture in after['captures']:
        for call in capture['calls']:
            names = set(call['tool_names'])
            assert names & FORBIDDEN_TOOLS == set(), f"{capture['case']} call still sends {names}"
            assert 'GenerateStructuredOutput' in names   # still a tool, never response_format
            assert 'read_skill_reference' in names


def test_every_call_of_a_step_was_slimmed():
    """Both calls of a step carry the system prompt and the tools, so both had to shrink.

    Asserting only the first call would hide the fact that the follow-up call replays
    the full tool block.
    """
    after = _capture(AFTER_SNAPSHOT)
    for capture in after['captures']:
        assert capture['provider_calls_in_one_step'] >= 2, capture['case']
        for call in capture['calls']:
            assert call['tool_names'], 'a call with no tools at all would be a different bug'
            assert _tool_schema_tokens(call) < 1699    # the Step 13.4 four-tool total


def test_the_capture_proves_the_tool_block_shrank():
    """Before/After on the recorded requests — the saving is measured, not asserted blind."""
    before = _capture(BEFORE_SNAPSHOT)
    after = _capture(AFTER_SNAPSHOT)

    before_names = set(before['captures'][0]['calls'][0]['tool_names'])
    after_names = set(after['captures'][0]['calls'][0]['tool_names'])
    assert before_names - after_names == FORBIDDEN_TOOLS
    assert after_names - before_names == set()          # nothing was gained by accident
    assert REQUIRED_TOOLS & after_names == REQUIRED_TOOLS

    for call_before, call_after in zip(before['captures'][0]['calls'],
                                       after['captures'][0]['calls']):
        assert _tool_schema_tokens(call_after) < _tool_schema_tokens(call_before)


def test_the_skill_catalogue_left_the_system_prompt():
    """The prompt-side half of the saving, from the same two captures."""
    before = _capture(BEFORE_SNAPSHOT)['captures'][0]['request']['messages'][0]['content']
    after = _capture(AFTER_SNAPSHOT)['captures'][0]['request']['messages'][0]['content']
    assert '<agent-skills>' in before
    assert '<agent-skills>' not in after
    assert 'list_skills' not in after


def test_the_saving_is_exactly_the_removed_schemas():
    """No magic number: the request-level saving must equal the removed tool schemas.

    A fixed token figure here would rot the moment a docstring is edited, and would say
    nothing about what caused the change. This asserts the identity instead, so the
    measurement stays true as the two tools' text evolves.

    Tolerance of 2 tokens: the estimator rounds each JSON blob it is handed, and the
    removed tools are measured as two blobs while the delta is measured as one. Anything
    beyond rounding means a third change reached the request, which is what this guards.
    """
    before = _capture(BEFORE_SNAPSHOT)['captures'][0]['calls'][0]['tools']
    after = _capture(AFTER_SNAPSHOT)['captures'][0]['calls'][0]['tools']
    removed = sum(_schema_tokens([tool]) for tool in before
                  if _tool_name(tool) in FORBIDDEN_TOOLS)
    assert removed > 0
    delta = _schema_tokens(before) - _schema_tokens(after)
    assert abs(delta - removed) <= 2, f'saving {delta} does not match the removed {removed}'


def _tool_name(tool: dict) -> str:
    return (tool.get('function') or {}).get('name') or tool.get('name')


def _schema_tokens(tools: list) -> int:
    from app.context import estimate_tokens
    return estimate_tokens(json.dumps(tools, ensure_ascii=False))


def _tool_schema_tokens(call: dict) -> int:
    return _schema_tokens(call['tools'])
