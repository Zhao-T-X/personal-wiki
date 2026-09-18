"""Step 15 A/B — agentic vs prefetch extraction, on the same five live cases.

Both arms go through the production extraction path (``extract_structured``, the call
``app/llm.py::_extract_one`` makes) and are measured at the provider seam: the same
``AsyncCompletions.create`` the capture script hooks counts the calls and reports the
tokens. Nothing here is estimated from the project side except the context composition,
which is labelled as such.

    python scripts/step15_ab.py                # both arms, 5 cases
    python scripts/step15_ab.py --mode prefetch --only predicate_registry_ceo

Cost: agentic ≈ 2 calls/case, prefetch ≈ 1 call/case. The run prints its own usage total
before writing the artifact.
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
ARTIFACT = FIXTURE_DIR / 'step15_ab.json'

# The five cases §12 names: entity, concept, predicate, temporal, evidence.
CASE_IDS = ['entity_context_runtime', 'concept_progressive_loading', 'predicate_registry_ceo',
            'temporal_former_ceo', 'evidence_attachment']
MODES = ('agentic', 'prefetch')


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
    tmp = Path(tempfile.mkdtemp(prefix='llmwiki-step15-'))
    os.environ['DATABASE_PATH'] = str(tmp / 'wiki.db')
    os.environ.setdefault('SETTINGS_PATH', str(tmp / 'settings.json'))
    os.environ['LLM_TEST_MODE'] = 'live'


def _install_usage_spy():
    """Count and measure the real provider calls, then get out of the way."""
    from app.agents import extraction_agent as ea

    probe = ea.build_extraction_agent('probe', mode='prefetch')
    completions_cls = type(probe.model.client.chat.completions)
    original = completions_cls.create
    calls: list[dict] = []

    async def spy(self, **kwargs):
        response = await original(self, **kwargs)
        usage = getattr(response, 'usage', None)
        calls.append({
            'prompt_tokens': int(getattr(usage, 'prompt_tokens', 0) or 0),
            'completion_tokens': int(getattr(usage, 'completion_tokens', 0) or 0),
            'tool_names': [((t.get('function') or {}).get('name') or t.get('name'))
                           for t in (kwargs.get('tools') or [])],
            'message_count': len(kwargs.get('messages') or []),
        })
        return response

    completions_cls.create = spy
    return calls, (lambda: setattr(completions_cls, 'create', original))


def _context_composition(payload: str) -> dict:
    """What the prefetch prompt is made of, project side (§16). Offline."""
    from app.context import estimate_tokens
    from app.context.extraction_context import plan_extraction_context
    from app.prompt_profiles import build_context

    plan = plan_extraction_context(payload)
    compiled = build_context('extractor', include_reference=True, references=[], tools=[],
                             extra_items=plan.items(), persist=False, use_cache=False)
    by_type: dict[str, int] = {}
    for section in compiled.to_trace()['sections']:
        by_type[section['name']] = by_type.get(section['name'], 0) + section['tokens']
    return {
        'by_section': by_type,
        'rendered_tokens': compiled.rendered_tokens,
        'ledger_tokens': compiled.total_tokens,
        'not_rendered': compiled.not_rendered,
        'predicate_candidates': list(plan.predicate_candidates),
        'flags': list(plan.flags),
        'chunk_tokens': estimate_tokens(payload),
    }


def _run_case(case: dict, mode: str) -> dict:
    """One case through the real extraction path, then compile + persist + evaluate."""
    from live_contract_eval import evaluate_case

    from app import db, service
    from app.agents import extraction_agent as ea
    from app.extraction_items import compile_extraction_items
    from app.knowledge import persist_extraction

    marker = uuid.uuid4().hex[:8]
    content = f'{case["text"]} [{marker}]'
    doc = service.create_document(title=f'step15 {case["id"]} {mode}',
                                  content=content, source_type='note')
    chunk = service.write_chunks(doc, content)[0]
    payload = f'[CHUNK {chunk["id"]}]\n{content}'

    calls, restore = _install_usage_spy()
    try:
        raw = asyncio.run(ea.extract_structured(payload, mode=mode))
    finally:
        restore()

    outcome = compile_extraction_items(raw)
    with db.transaction() as conn:
        counts = persist_extraction(conn, document_id=doc, extraction=outcome.envelope)
    verdict = evaluate_case(case, outcome.envelope, counts,
                            [r.failure_view() for r in outcome.failures])

    return {
        'case': case['id'], 'covers': case['covers'], 'mode': mode, 'text': case['text'],
        'provider_calls': len(calls),
        'prompt_tokens': sum(c['prompt_tokens'] for c in calls),
        'completion_tokens': sum(c['completion_tokens'] for c in calls),
        'calls': calls,
        'passed': verdict['passed'],
        'checks': verdict['checks'],
        'rejected': [r.failure_view() for r in outcome.failures],
        'document': {'status': outcome.status, 'entities': counts.get('entities'),
                     'claims': counts.get('claims'), 'relations': counts.get('relations')},
        'observed_claims': [{'subject': c.get('subject'), 'predicate': c.get('predicate'),
                             'object': c.get('object'), 'object_kind': c.get('object_kind'),
                             'temporal_signal': c.get('temporal_signal')}
                            for c in outcome.envelope['claims']],
        'raw_envelope': raw,
    }


def _summary(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for mode in MODES:
        arm = [r for r in rows if r['mode'] == mode]
        if not arm:
            continue
        out[mode] = {
            'cases': len(arm),
            'provider_calls': sum(r['provider_calls'] for r in arm),
            'prompt_tokens': sum(r['prompt_tokens'] for r in arm),
            'completion_tokens': sum(r['completion_tokens'] for r in arm),
            'semantic_passed': f"{sum(1 for r in arm if r['passed']['semantic'])}/{len(arm)}",
            'failed_cases': [r['case'] for r in arm if not r['passed']['semantic']],
            'calls_per_case': round(sum(r['provider_calls'] for r in arm) / len(arm), 2),
            'prompt_tokens_per_case': round(sum(r['prompt_tokens'] for r in arm) / len(arm)),
        }
    if 'agentic' in out and 'prefetch' in out:
        before = out['agentic']['prompt_tokens']
        after = out['prefetch']['prompt_tokens']
        out['delta'] = {
            'prompt_tokens_saved': before - after,
            'reduction': f'{(before - after) / before:.1%}' if before else 'n/a',
            'calls_saved': out['agentic']['provider_calls'] - out['prefetch']['provider_calls'],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=[*MODES, 'both'], default='both')
    ap.add_argument('--only', default=None, help='substring filter over case ids')
    args = ap.parse_args()

    _bootstrap_env()
    from app.config import runtime
    from app.db import init_db

    if not runtime().get('openai_api_key'):
        print('no provider key configured; cannot run a live A/B')
        return 3

    cases = json.loads(CASES_PATH.read_text(encoding='utf-8'))['live_cases']
    wanted = [c for c in cases if c['id'] in CASE_IDS]
    if args.only:
        wanted = [c for c in wanted if args.only in c['id']]
    assert len(wanted) == (1 if args.only else len(CASE_IDS)), [c['id'] for c in wanted]

    modes = MODES if args.mode == 'both' else (args.mode,)
    init_db()

    rows: list[dict] = []
    for case in wanted:
        for mode in modes:
            row = _run_case(case, mode)
            rows.append(row)
            verdict = 'PASS' if row['passed']['semantic'] else 'FAIL'
            print(f"  [{verdict}] {case['id']:<28} {mode:<9} calls={row['provider_calls']} "
                  f"prompt={row['prompt_tokens']} completion={row['completion_tokens']} "
                  f"claims={row['document']['claims']}")
            for check in row['checks']:
                if not check['ok']:
                    print(f"        {check['layer']:<10} {check['check']}: {check['detail']}")

    summary = _summary(rows)
    composition = _context_composition(f'[CHUNK probe]\n{wanted[0]["text"]}')

    artifact = {
        'what': 'Step 15 A/B: two provider calls (agentic) versus one prefetch call, on the '
                'same five cases, measured at the provider seam.',
        'model': runtime().get('openai_model'),
        'case_ids': [c['id'] for c in wanted],
        'modes': list(modes),
        'summary': summary,
        'prefetch_context_composition': composition,
        'runs': rows,
        'usage': {'provider_calls': sum(r['provider_calls'] for r in rows),
                  'prompt_tokens': sum(r['prompt_tokens'] for r in rows),
                  'completion_tokens': sum(r['completion_tokens'] for r in rows)},
    }
    ARTIFACT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding='utf-8')

    print('\nA/B SUMMARY')
    for mode, block in summary.items():
        print(f'  {mode}: {json.dumps(block, ensure_ascii=False)}')
    print(f"\nUSAGE {json.dumps(artifact['usage'])}")
    print(f'wrote {ARTIFACT.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
