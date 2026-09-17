"""Capture the REAL provider request at the client boundary (Step 13.4).

The audit could account for 2,740 tokens of project content while the provider reported
8,041. Guessing "AgentScope" from the remainder is not evidence, so this script hooks the
single place where the request is actually issued —

    agentscope/model/_openai_chat/_model.py :: _call_api
        -> self.client.chat.completions.create(**kwargs)

— records the exact kwargs (messages, tools, tool_choice, parameters) and the response's
tool usage, then writes a sanitised snapshot.

    python scripts/capture_provider_request.py                 # low-cost case only
    python scripts/capture_provider_request.py --both          # low + high variance case

Cost: one live extraction call per case (2 with --both). API keys, auth headers and base
URLs are stripped before anything is written.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE_DIR = ROOT / 'tests' / 'fixtures' / 'extraction_cases'
SNAPSHOT = FIXTURE_DIR / 'provider_request_snapshot.json'
TOOL_AUDIT = FIXTURE_DIR / 'tool_usage_audit.json'

# Low and high ends of the observed extract-step spread (Step 13.2 full suite).
CASES = {
    'low': {'id': 'literal_chunk_size', 'text': '最大 chunk 为 8192 tokens。',
            'recorded_prompt_tokens': 8041},
    'high': {'id': 'prose_slash_architecture', 'text': '客户端/服务端架构需要统一认证。',
             'recorded_prompt_tokens': 9839},
}

_SECRET_KEYS = ('api_key', 'apikey', 'authorization', 'auth', 'token', 'secret',
                'cookie', 'password', 'proxy')


def _bootstrap_env() -> None:
    """Load the developer's provider settings into the env BEFORE app.config is imported."""
    settings = ROOT / 'data' / 'settings.json'
    if not settings.exists():
        return
    data = json.loads(settings.read_text(encoding='utf-8'))
    for env_name, key in (('OPENAI_API_KEY', 'openai_api_key'),
                          ('OPENAI_BASE_URL', 'openai_base_url'),
                          ('OPENAI_MODEL', 'openai_model')):
        if data.get(key) and not os.environ.get(env_name):
            os.environ[env_name] = str(data[key])
    tmp = Path(tempfile.mkdtemp(prefix='llmwiki-capture-'))
    os.environ['DATABASE_PATH'] = str(tmp / 'wiki.db')
    os.environ.setdefault('SETTINGS_PATH', str(tmp / 'settings.json'))
    os.environ['LLM_TEST_MODE'] = 'live'


def _tokens(text: str) -> int:
    from app.context import estimate_tokens
    return estimate_tokens(text or '')


def _real_tokens(text: str) -> int | None:
    """A real tokenizer when the environment has one; otherwise None (never faked)."""
    try:
        import tiktoken  # type: ignore
    except Exception:
        return None
    try:
        enc = tiktoken.get_encoding('cl100k_base')
        return len(enc.encode(text or ''))
    except Exception:
        return None


def _sanitize(value, *, key: str = ''):
    """Strip anything that could carry a credential; keep structure and counts."""
    if key.lower() in _SECRET_KEYS:
        return '<redacted>'
    if isinstance(value, dict):
        return {k: _sanitize(v, key=k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v, key=key) for v in value]
    return value


def _message_view(message) -> dict:
    if isinstance(message, dict):
        role = message.get('role')
        content = message.get('content')
    else:
        role = getattr(message, 'role', type(message).__name__)
        content = getattr(message, 'content', None)
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    return {'role': role, 'chars': len(content), 'estimated_tokens': _tokens(content),
            'real_tokens': _real_tokens(content),
            'sha1': hashlib.sha1(content.encode('utf-8')).hexdigest()[:12],
            'preview': content[:200], 'content': content}


def _install_spy(captured: list[dict], responses: list[dict]):
    """Patch the CLIENT CLASS, not one instance.

    ``extract_structured`` builds its own agent (and therefore its own ``AsyncOpenAI``),
    so patching the agent we happen to hold would capture nothing. The resource class is
    shared by every instance, which is exactly the scope needed to watch the production
    path without touching it.
    """
    from app.agents.extraction_agent import build_extraction_agent

    probe = build_extraction_agent()
    completions_cls = type(probe.model.client.chat.completions)
    original = completions_cls.create

    async def spy(self, **kwargs):
        captured.append(_sanitize(kwargs))
        response = await original(self, **kwargs)
        try:
            usage = getattr(response, 'usage', None)
            choices = getattr(response, 'choices', []) or []
            message = getattr(choices[0], 'message', None) if choices else None
            responses.append({
                'usage': {'prompt_tokens': int(getattr(usage, 'prompt_tokens', 0) or 0),
                          'completion_tokens': int(getattr(usage, 'completion_tokens', 0) or 0)},
                'finish_reason': getattr(choices[0], 'finish_reason', None) if choices else None,
                'tool_calls': [getattr(tc, 'function', None) and
                               getattr(tc.function, 'name', None)
                               for tc in (getattr(message, 'tool_calls', None) or [])],
            })
        except Exception:
            pass
        return response

    completions_cls.create = spy
    return probe, completions_cls


def capture(case: dict) -> dict:
    from app.agents.extraction_agent import extract_structured
    from app.db import init_db

    init_db()
    captured: list[dict] = []
    responses: list[dict] = []
    _install_spy(captured, responses)

    payload = f'[CHUNK capture-{case["id"]}]\n{case["text"]}'
    data = asyncio.run(extract_structured(payload))
    if not captured:
        # Never spend a second call on a patch that did not land.
        raise RuntimeError('spy captured 0 provider calls — the client seam moved; '
                           'no further call will be made')

    def _view(request: dict, response: dict) -> dict:
        messages = [_message_view(m) for m in (request.get('messages') or [])]
        serialized = json.dumps(_sanitize(request), ensure_ascii=False, separators=(',', ':'))
        prompt = (response.get('usage') or {}).get('prompt_tokens')
        return {
            'model': request.get('model'),
            'messages': messages,
            'message_count': len(messages),
            'tools': request.get('tools') or [],
            'tool_names': [((t.get('function') or {}).get('name') or t.get('name'))
                           for t in (request.get('tools') or [])],
            'tool_choice': request.get('tool_choice'),
            'response_format': request.get('response_format'),
            'temperature': request.get('temperature'),
            'max_completion_tokens': request.get('max_completion_tokens'),
            'parallel_tool_calls': request.get('parallel_tool_calls'),
            'stream': request.get('stream'),
            'other_keys': sorted(k for k in request if k not in (
                'model', 'messages', 'tools', 'tool_choice', 'response_format',
                'temperature', 'max_completion_tokens', 'parallel_tool_calls', 'stream')),
            'response': response,
            'serialized_estimated_tokens': _tokens(serialized),
            'serialized_real_tokens': _real_tokens(serialized),
            'provider_prompt_tokens': prompt,
            'provider_completion_tokens': (response.get('usage') or {}).get('completion_tokens'),
        }

    calls = [_view(request, responses[i] if i < len(responses) else {})
             for i, request in enumerate(captured)]
    first = calls[0]
    provider_total = sum(c.get('provider_prompt_tokens') or 0 for c in calls)
    return {
        'case': case['id'],
        'text': case['text'],
        'provider_calls_in_one_step': len(calls),
        'calls': calls,
        'request': first,          # the BASE request: the one the project actually shapes
        'extraction_result_keys': sorted(data) if isinstance(data, dict) else None,
        'token_layers': {
            'project_known': _project_known_tokens(),
            'serialized_request_estimated': first['serialized_estimated_tokens'],
            'serialized_request_real': first['serialized_real_tokens'],
            'provider_reported_prompt_base_call': first.get('provider_prompt_tokens'),
            'provider_reported_prompt_step_total': provider_total,
            'serialization_overhead_estimated': (first['serialized_estimated_tokens']
                                                 - _project_known_tokens()),
            'provider_unattributed': ((first.get('provider_prompt_tokens') or 0)
                                      - first['serialized_estimated_tokens']),
            'recorded_prompt_tokens_earlier_run': case['recorded_prompt_tokens'],
        },
    }


def _project_known_tokens() -> int:
    snapshot = FIXTURE_DIR / 'extraction_prompt_snapshot.json'
    if snapshot.exists():
        data = json.loads(snapshot.read_text(encoding='utf-8'))
        return int(data['estimated_tokens']['known_project_total'])
    return 0


def _tool_audit(captures: list[dict]) -> dict:
    """One row per (case, tool). Usage is the union over every call of that step: a tool the
    model invoked in the first call was 'used' even though a later call merely re-offered it."""
    rows: dict[tuple, dict] = {}
    for capture_ in captures:
        called = set()
        for call in capture_['calls']:
            called |= {n for n in ((call.get('response') or {}).get('tool_calls') or []) if n}
        for tool in capture_['calls'][0]['tools']:
            name = ((tool.get('function') or {}).get('name') if isinstance(tool, dict) else None) \
                or (tool.get('name') if isinstance(tool, dict) else None)
            rows[(capture_['case'], name)] = {
                'case': capture_['case'], 'tool': name, 'sent': True, 'used': name in called,
                'sent_in_every_call': all(
                    name in [((t.get('function') or {}).get('name') or t.get('name'))
                             for t in call['tools']] for call in capture_['calls']),
                'schema_tokens': _tokens(json.dumps(tool, ensure_ascii=False)),
            }
    return {
        'what': 'Which tools the extraction request carries, and which the model actually '
                'called. Sent schemas are not automatically useful ones.',
        'provider_calls': sum(c['provider_calls_in_one_step'] for c in captures),
        'tools': list(rows.values()),
        'note': ('Sent != needed. This round measures only; no tool was removed. A tool the '
                 'model never calls is a candidate for a later round, not a finding by itself.'),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--both', action='store_true',
                    help='also capture the high end of the 8k-10k spread (2 calls total)')
    ap.add_argument('--reaggregate', action='store_true',
                    help='rebuild tool_usage_audit.json from the stored snapshot (0 calls)')
    args = ap.parse_args()

    if args.reaggregate:
        if not SNAPSHOT.exists():
            print('no snapshot to re-aggregate')
            return 3
        stored = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
        TOOL_AUDIT.write_text(json.dumps(_tool_audit(stored['captures']), ensure_ascii=False,
                                         indent=2), encoding='utf-8')
        for row in _tool_audit(stored['captures'])['tools']:
            print(f"  {row['case']:<26} {row['tool']:<26} used={row['used']} "
                  f"every_call={row['sent_in_every_call']} tokens={row['schema_tokens']}")
        print(f'wrote {TOOL_AUDIT.relative_to(ROOT)} (no provider calls)')
        return 0

    _bootstrap_env()
    from app.config import runtime
    if not runtime().get('openai_api_key'):
        print('no provider key configured; cannot capture a real request')
        return 3

    wanted = ['low'] + (['high'] if args.both else [])
    captures = [capture(CASES[name]) for name in wanted]

    snapshot = {
        'what': 'The real provider request at the client boundary '
                '(agentscope _openai_chat _call_api -> client.chat.completions.create). '
                'Credentials, auth headers and base URLs are stripped.',
        'secrets_included': False,
        'model': runtime().get('openai_model'),
        'captures': captures,
    }
    SNAPSHOT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    TOOL_AUDIT.write_text(json.dumps(_tool_audit(captures), ensure_ascii=False, indent=2),
                          encoding='utf-8')

    for capture_ in captures:
        request = capture_['request']
        layers = capture_['token_layers']
        print(f"\n=== {capture_['case']} ({capture_['text']}) ===")
        print(f"  provider calls in one step : {capture_['provider_calls_in_one_step']}")
        print(f"  model                      : {request['model']}")
        print(f"  response_format            : {request['response_format']}")
        print(f"  tools                      : {request['tool_names']}")
        print(f"  tool_choice                : {request['tool_choice']}")
        print(f"  other request keys         : {request['other_keys']}")
        for i, message in enumerate(request['messages']):
            print(f"    [{i}] {message['role']:<10} chars={message['chars']:>6} "
                  f"est={message['estimated_tokens']:>6} real={message['real_tokens']} "
                  f"sha1={message['sha1']}")
        for i, call in enumerate(capture_['calls']):
            print(f"  call[{i}] messages={call['message_count']} "
                  f"tools={len(call['tools'])} serialized_est={call['serialized_estimated_tokens']} "
                  f"provider_prompt={call['provider_prompt_tokens']} "
                  f"tool_calls={ (call['response'] or {}).get('tool_calls')}")
        print(f"  project known              : {layers['project_known']}")
        print(f"  provider prompt (base call): {layers['provider_reported_prompt_base_call']}")
        print(f"  provider prompt (step sum) : {layers['provider_reported_prompt_step_total']}")
        print(f"  provider unattributed      : {layers['provider_unattributed']}")
    print(f"\nwrote {SNAPSHOT.relative_to(ROOT)} and {TOOL_AUDIT.relative_to(ROOT)}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
