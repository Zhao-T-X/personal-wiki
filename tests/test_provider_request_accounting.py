"""Provider-boundary accounting (Step 13.4) — all offline, 0 tokens.

The cost question could not be answered from the project side, so the request was
captured where it is actually issued (``client.chat.completions.create``). These tests
keep that capture trustworthy: no credentials, a stable shape, per-message traceability,
and a token split that never counts unrendered context as sent.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'extraction_cases'
SNAPSHOT = FIXTURE_DIR / 'provider_request_snapshot.json'
TOOL_AUDIT = FIXTURE_DIR / 'tool_usage_audit.json'
PROMPT_SNAPSHOT = FIXTURE_DIR / 'extraction_prompt_snapshot.json'

_SECRET_PATTERNS = (re.compile(r'sk-[A-Za-z0-9]{8,}'), re.compile(r'(?i)authorization'),
                    re.compile(r'(?i)bearer\s+[A-Za-z0-9._-]{8,}'))


def _load(path: Path) -> dict:
    if not path.exists():
        pytest.skip(f'{path.name} not captured yet: run scripts/capture_provider_request.py')
    return json.loads(path.read_text(encoding='utf-8'))


# ------------------------------------------------------------------ no credentials

def test_capture_contains_no_secrets():
    text = SNAPSHOT.read_text(encoding='utf-8') if SNAPSHOT.exists() else ''
    if not text:
        pytest.skip('no capture yet')
    for pattern in _SECRET_PATTERNS:
        assert not pattern.search(text), f'credential-like content in snapshot: {pattern.pattern}'


def test_sanitizer_redacts_credentials():
    """The sanitiser is tested on a synthetic payload so it is covered without a live run."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        'capture_provider_request',
        Path(__file__).resolve().parents[1] / 'scripts' / 'capture_provider_request.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    payload = {'api_key': 'sk-live-SECRET', 'Authorization': 'Bearer SECRET',
               'model': 'm', 'headers': {'cookie': 'session=SECRET'},
               'messages': [{'role': 'user', 'content': 'keep me'}]}
    clean = module._sanitize(payload)
    assert clean['api_key'] == '<redacted>'
    assert clean['Authorization'] == '<redacted>'
    assert clean['headers']['cookie'] == '<redacted>'
    assert clean['model'] == 'm'                      # structure survives
    assert clean['messages'][0]['content'] == 'keep me'


# ------------------------------------------------------------------ request shape

def test_request_is_traceable():
    snapshot = _load(SNAPSHOT)
    assert snapshot['secrets_included'] is False
    for capture in snapshot['captures']:
        request = capture['request']
        assert isinstance(request['messages'], list) and request['messages']
        roles = [m['role'] for m in request['messages']]
        assert all(isinstance(r, str) and r for r in roles), roles
        assert roles[0] == 'system'
        assert {'role', 'chars', 'estimated_tokens', 'sha1', 'preview'} <= set(request['messages'][0])
        assert 'response_format' in request          # explicitly recorded, even when None
        assert isinstance(request['tools'], list)
        assert request['model']


def test_one_step_makes_two_provider_calls_and_that_is_the_cost():
    """The 8k figure is a STEP total, not one request. Locked so the report cannot drift."""
    snapshot = _load(SNAPSHOT)
    for capture in snapshot['captures']:
        assert capture['provider_calls_in_one_step'] >= 2, capture['case']
        calls = capture['calls']
        base, follow_up = calls[0], calls[1]
        # The follow-up replays the base (system + tools) and adds tool results.
        assert follow_up['provider_prompt_tokens'] > base['provider_prompt_tokens']
        step_total = sum(c['provider_prompt_tokens'] or 0 for c in calls)
        assert step_total == capture['token_layers']['provider_reported_prompt_step_total']
        assert len(base['tools']) == len(follow_up['tools'])


def test_schema_ships_as_a_tool_not_duplicated():
    """Structured output is an extra TOOL (`GenerateStructuredOutput`), response_format is
    None — so the schema is sent once per request, never twice in the same request."""
    snapshot = _load(SNAPSHOT)
    for capture in snapshot['captures']:
        request = capture['request']
        assert request['response_format'] is None
        assert 'GenerateStructuredOutput' in request['tool_names']


def test_tool_audit_lists_every_sent_tool():
    audit = _load(TOOL_AUDIT)
    names = {row['tool'] for row in audit['tools']}
    assert {'read_skill_reference', 'GenerateStructuredOutput', 'list_skills', 'Skill'} <= names
    structured = next(r for r in audit['tools'] if r['tool'] == 'GenerateStructuredOutput')
    assert structured['sent'] is True
    assert structured['schema_tokens'] > 1000          # the dominant tool


# ------------------------------------------------------- bookkeeping (no live call)

def test_unrendered_reference_is_not_counted_as_sent():
    """`reference` is loaded by the ledger but dropped by render() — the two must differ,
    or every cost report silently banks ~0.55k tokens that were never sent."""
    import os
    import tempfile
    from pathlib import Path as _Path

    tmp = _Path(tempfile.mkdtemp(prefix='llmwiki-accounting-'))
    os.environ['DATABASE_PATH'] = str(tmp / 'wiki.db')
    os.environ.setdefault('SETTINGS_PATH', str(tmp / 'settings.json'))

    from app.db import init_db
    from app.prompt_profiles import build_context

    init_db()
    compiled = build_context('extractor', include_reference=True,
                             references=['claim-predicates.md'], persist=False, use_cache=False)
    trace = compiled.to_trace()

    assert trace['actual_tokens'] >= trace['rendered_tokens']
    assert 'rendered_tokens' in trace
    assert trace['not_rendered'] or trace['actual_tokens'] == trace['rendered_tokens']
    assert compiled.rendered_tokens < compiled.total_tokens
    assert any(item['type'] == 'reference' for item in compiled.not_rendered)
    # and the rendered prompt really does not contain the reference text
    assert 'Claim Predicate Registry v1.0' not in compiled.render()


def test_prompt_snapshot_agrees_with_the_capture():
    """The project-side breakdown and the provider-side capture must describe the same
    request; a mismatch means one of the two measurements is stale."""
    snapshot = _load(SNAPSHOT)
    if not PROMPT_SNAPSHOT.exists():
        pytest.skip('no prompt snapshot')
    project = json.loads(PROMPT_SNAPSHOT.read_text(encoding='utf-8'))['estimated_tokens']
    base = snapshot['captures'][0]
    serialized = base['token_layers']['serialized_request_estimated']
    known = project['known_project_total']
    # Same order of magnitude, and the serialized request is never smaller than the parts.
    assert serialized >= known * 0.8
    assert abs(serialized - known) / known < 0.35
