"""Step 17 live canary — extraction Skill `current` vs `compact`, same everything else.

The replay A/B (`tests/test_skill_minimality.py`) proves the pipeline is skill-agnostic; it
cannot prove the *model* is still right under the shorter contract, because the model output
is recorded. This script is that missing half: five cases through the real production path
(``app.llm.extract``) under each contract, one extraction call per case.

Held constant across arms (§13): model, chunk, Context Planner (prefetch), schema, tools,
registry, temperature. The only changed input is which SKILL body rides the system prompt —
asserted per call, so an arm cannot silently run the other contract.

Detection is off for both arms (§12), so a case is exactly one extraction request; a second
extraction call is a hard failure, not a variance to average over.

    python scripts/step17_skill_ab.py                 # both arms, 5 cases (<=10 calls)
    python scripts/step17_skill_ab.py --variant compact --only predicate_registry_ceo
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

FIXTURE_DIR = ROOT / 'tests' / 'fixtures' / 'extraction_cases'
CASES_PATH = FIXTURE_DIR / 'micro_cases.json'
ARTIFACT = FIXTURE_DIR / 'step17_skill_ab.json'

# §12: the five §12 cases — entity, concept, predicate, temporal, evidence.
CASE_IDS = ['entity_context_runtime', 'concept_progressive_loading', 'predicate_registry_ceo',
            'temporal_former_ceo', 'evidence_attachment']
VARIANTS = ('current', 'compact')
DETECTION_MARKER = 'fast triage classifier'
MAX_EXTRACTION_CALLS_PER_CASE = 1


def _bootstrap_env() -> None:
    """Load the developer's provider settings before app.config is imported."""
    settings = ROOT / 'data' / 'settings.json'
    if settings.exists():
        data = json.loads(settings.read_text(encoding='utf-8'))
        for env_name, key in (('OPENAI_API_KEY', 'openai_api_key'),
                              ('OPENAI_BASE_URL', 'openai_base_url'),
                              ('OPENAI_MODEL', 'openai_model')):
            if data.get(key) and not os.environ.get(env_name):
                os.environ[env_name] = str(data[key])
    tmp = Path(tempfile.mkdtemp(prefix='llmwiki-step17-'))
    os.environ['DATABASE_PATH'] = str(tmp / 'wiki.db')
    os.environ.setdefault('SETTINGS_PATH', str(tmp / 'settings.json'))
    os.environ['LLM_TEST_MODE'] = 'live'


def _call_kind(system_text: str) -> str:
    return 'detection' if DETECTION_MARKER in system_text else 'extraction'


def _install_provider_spy():
    """Count and measure the real provider calls, and record which contract each carried."""
    from app.agents import extraction_agent as ea

    probe = ea.build_extraction_agent('probe', mode='prefetch')
    completions_cls = type(probe.model.client.chat.completions)
    original = completions_cls.create
    calls: list[dict] = []

    async def spy(self, **kwargs):
        response = await original(self, **kwargs)
        usage = getattr(response, 'usage', None)
        system_text = ''
        for message in kwargs.get('messages') or []:
            if message.get('role') == 'system':
                system_text = message.get('content') or ''
                if not isinstance(system_text, str):
                    system_text = json.dumps(system_text, ensure_ascii=False, default=str)
                break
        calls.append({
            'kind': _call_kind(system_text),
            'prompt_tokens': int(getattr(usage, 'prompt_tokens', 0) or 0),
            'completion_tokens': int(getattr(usage, 'completion_tokens', 0) or 0),
            'system_chars': len(system_text),
            # Provenance: the arm really carried the contract it claims to.
            'carries_current_skill': '[SKILL]' in system_text and 'Core rules:' in system_text,
            'carries_compact_skill': '[SKILL]' in system_text and 'Semantic contract:' in system_text,
        })
        return response

    completions_cls.create = spy
    return calls, (lambda: setattr(completions_cls, 'create', original))


def _skill_tokens(variant: str) -> int:
    from app.context import estimate_tokens
    from app.skills import read_skill_contract
    return estimate_tokens(read_skill_contract('knowledge-extraction', variant=variant))


def _run_case(case: dict, variant: str) -> dict:
    from live_contract_eval import evaluate_case

    from app import config, db, service
    from app.extraction_items import compile_extraction_items
    from app.knowledge import persist_extraction
    from app.runlog import record_run

    marker = uuid.uuid4().hex[:8]
    content = f'{case["text"]} [{marker}]'
    doc = service.create_document(title=f'step17 {case["id"]} {variant}',
                                  content=content, source_type='note')
    chunk = service.write_chunks(doc, content)[0]

    config._OVERRIDES['extraction_skill_variant'] = variant
    calls, restore = _install_provider_spy()
    try:
        with record_run('extract', document_id=doc, agent_role='step17-canary') as run:
            from app import llm
            raw = asyncio.run(llm.extract([{'id': chunk['id'], 'content': content}]))
            context_plan = getattr(run, 'extraction_context_plan', None)
    finally:
        restore()
        config._OVERRIDES.pop('extraction_skill_variant', None)

    extraction_calls = [c for c in calls if c['kind'] == 'extraction']
    if len(extraction_calls) != MAX_EXTRACTION_CALLS_PER_CASE:
        raise AssertionError(
            f"{case['id']} [{variant}]: {len(extraction_calls)} extraction provider calls "
            f"(expected {MAX_EXTRACTION_CALLS_PER_CASE}); kinds={[c['kind'] for c in calls]} "
            '— a second extraction is a defect, not a variance')

    outcome = compile_extraction_items(raw)
    with db.transaction() as conn:
        counts = persist_extraction(conn, document_id=doc, extraction=outcome.envelope)
    verdict = evaluate_case(case, outcome.envelope, counts,
                            [r.failure_view() for r in outcome.failures])

    return {
        'case': case['id'], 'covers': case['covers'], 'variant': variant, 'text': case['text'],
        'provider_calls': len(calls),
        'extraction_calls': len(extraction_calls),
        'prompt_tokens': sum(c['prompt_tokens'] for c in calls),
        'completion_tokens': sum(c['completion_tokens'] for c in calls),
        'extraction_prompt_tokens': sum(c['prompt_tokens'] for c in extraction_calls),
        'system_chars': extraction_calls[0]['system_chars'] if extraction_calls else 0,
        'carries_current_skill': any(c['carries_current_skill'] for c in extraction_calls),
        'carries_compact_skill': any(c['carries_compact_skill'] for c in extraction_calls),
        'passed': verdict['passed'], 'checks': verdict['checks'],
        'context_plan': context_plan,
        'rejected': [r.failure_view() for r in outcome.failures],
        'document': {'status': outcome.status, 'entities': counts.get('entities'),
                     'claims': counts.get('claims'), 'relations': counts.get('relations')},
        'observed_claims': [{'subject': c.get('subject'), 'predicate': c.get('predicate'),
                             'object': c.get('object'), 'object_kind': c.get('object_kind'),
                             'temporal_signal': c.get('temporal_signal'),
                             'polarity': c.get('polarity')}
                            for c in outcome.envelope['claims']],
        'raw_envelope': raw,
    }


def _summary(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for variant in VARIANTS:
        arm = [r for r in rows if r['variant'] == variant]
        if not arm:
            continue
        out[variant] = {
            'cases': len(arm),
            'extraction_calls': sum(r['extraction_calls'] for r in arm),
            'prompt_tokens': sum(r['prompt_tokens'] for r in arm),
            'completion_tokens': sum(r['completion_tokens'] for r in arm),
            'semantic_passed': f"{sum(1 for r in arm if r['passed']['semantic'])}/{len(arm)}",
            'registry_passed': f"{sum(1 for r in arm if r['passed']['registry'])}/{len(arm)}",
            'structured_passed': f"{sum(1 for r in arm if r['passed']['structural'])}/{len(arm)}",
            'failed_cases': [r['case'] for r in arm if not r['passed']['semantic']],
            'extraction_calls_per_case': round(
                sum(r['extraction_calls'] for r in arm) / len(arm), 2),
            'prompt_tokens_per_case': round(sum(r['prompt_tokens'] for r in arm) / len(arm)),
            'extraction_prompt_tokens_per_case': round(
                sum(r['extraction_prompt_tokens'] for r in arm) / len(arm)),
        }
    if 'current' in out and 'compact' in out:
        before = out['current']['extraction_prompt_tokens_per_case']
        after = out['compact']['extraction_prompt_tokens_per_case']
        out['delta'] = {
            'skill_tokens_saved': _skill_tokens('current') - _skill_tokens('compact'),
            'extraction_prompt_tokens_saved_per_case': before - after,
            'reduction': f'{(before - after) / before:.1%}' if before else 'n/a',
        }
    return out


def _semantic_drift(rows: list[dict]) -> dict:
    """Per case: the business facts each arm produced, so a drift is visible, not inferred."""

    def _key(row: dict) -> list:
        return [(c['subject'], c['predicate'], c['object'], c['object_kind'],
                 c['temporal_signal'], c['polarity']) for c in row['observed_claims']]

    by_case: dict[str, dict] = {}
    for row in rows:
        by_case.setdefault(row['case'], {})[row['variant']] = _key(row)
    drift = {}
    for case, arms in by_case.items():
        if 'current' in arms and 'compact' in arms and arms['current'] != arms['compact']:
            drift[case] = arms
    return drift


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--variant', choices=[*VARIANTS, 'both'], default='both')
    ap.add_argument('--only', default=None, help='substring filter over case ids')
    ap.add_argument('--out', default=str(ARTIFACT), help='artifact path (default: the fixture)')
    args = ap.parse_args()

    _bootstrap_env()
    from app.config import runtime
    from app.db import init_db

    if not runtime().get('openai_api_key'):
        print('no provider key configured; cannot run the live canary')
        return 3

    cases = json.loads(CASES_PATH.read_text(encoding='utf-8'))['live_cases']
    wanted = [c for c in cases if c['id'] in CASE_IDS]
    if args.only:
        wanted = [c for c in wanted if args.only in c['id']]
    assert wanted, f'--only matched no case: {args.only!r}'

    variants = VARIANTS if args.variant == 'both' else (args.variant,)
    init_db()

    # Both arms run the same pipeline; only the skill body changes. Detection is off so a
    # case is one extraction request (the skill A/B must not be confused with the Pass 1 A/B).
    from app import config
    config._OVERRIDES['extraction_context_mode'] = 'prefetch'
    config._OVERRIDES['extraction_detection_mode'] = 'off'

    rows: list[dict] = []
    for variant in variants:
        for case in wanted:
            row = _run_case(case, variant)
            rows.append(row)
            verdict = 'PASS' if row['passed']['semantic'] else 'FAIL'
            print(f"  [{verdict}] {case['id']:<28} {variant:<8} calls={row['extraction_calls']} "
                  f"prompt={row['prompt_tokens']} completion={row['completion_tokens']} "
                  f"claims={row['document']['claims']}")
            for check in row['checks']:
                if not check['ok']:
                    print(f"        {check['layer']:<10} {check['check']}: {check['detail']}")

    summary = _summary(rows)
    drift = _semantic_drift(rows)
    total_calls = sum(r['provider_calls'] for r in rows)

    artifact = {
        'what': 'Step 17 live canary: extraction Skill current vs compact, prefetch, detection '
                'off. The only changed input is the SKILL body; same model/chunk/planner/schema/'
                'tools/registry. One extraction provider call per case.',
        'model': runtime().get('openai_model'),
        'case_ids': [c['id'] for c in wanted],
        'variants': list(variants),
        'held_constant': {'extraction_context_mode': 'prefetch', 'extraction_detection_mode': 'off'},
        'skill_tokens': {v: _skill_tokens(v) for v in VARIANTS},
        'summary': summary,
        'semantic_drift': drift,
        'runs': rows,
        'usage': {'provider_calls': total_calls,
                  'extraction_calls': sum(r['extraction_calls'] for r in rows),
                  'prompt_tokens': sum(r['prompt_tokens'] for r in rows),
                  'completion_tokens': sum(r['completion_tokens'] for r in rows)},
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding='utf-8')

    print('\nSKILL A/B SUMMARY')
    for variant, block in summary.items():
        print(f'  {variant}: {json.dumps(block, ensure_ascii=False)}')
    print(f"  semantic_drift_cases={list(drift)}")
    print(f"\nUSAGE {json.dumps(artifact['usage'])}")
    print(f'wrote {out_path}')

    failed = [r for r in rows if not r['passed']['semantic']]
    if total_calls > 10:
        print(f'HARD FAIL: {total_calls} provider calls for <=10 allowed')
        return 2
    if drift:
        print(f'semantic drift between arms: {list(drift)}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
