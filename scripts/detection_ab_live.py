"""Step 16 live canary — Detection ON vs OFF on three cases.

Replay proved the detection plumbing changes nothing *given the same extraction output*.
What replay cannot prove is whether the model extracts differently without the scope hint
in its prompt, because the hint is the one difference replay holds constant. This run
measures exactly that, on the three cases §9 names, at the provider seam.

    python scripts/detection_ab_live.py

Cost: 3 cases x (1 detect + 1 extract) + 3 x (1 extract) = 9 provider calls.
"""
from __future__ import annotations

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
ARTIFACT = FIXTURE_DIR / 'step16_detection_ab.json'
CASE_IDS = ['predicate_registry_ceo', 'concept_progressive_loading', 'path_source_artifact']
MODES = ('on', 'off')


def _bootstrap_env() -> None:
    settings = ROOT / 'data' / 'settings.json'
    if settings.exists():
        data = json.loads(settings.read_text(encoding='utf-8'))
        for env_name, key in (('OPENAI_API_KEY', 'openai_api_key'),
                              ('OPENAI_BASE_URL', 'openai_base_url'),
                              ('OPENAI_MODEL', 'openai_model')):
            if data.get(key) and not os.environ.get(env_name):
                os.environ[env_name] = str(data[key])
    tmp = Path(tempfile.mkdtemp(prefix='llmwiki-step16-'))
    os.environ['DATABASE_PATH'] = str(tmp / 'wiki.db')
    os.environ.setdefault('SETTINGS_PATH', str(tmp / 'settings.json'))
    os.environ['LLM_TEST_MODE'] = 'live'


def _install_provider_counter():
    """Count the real provider calls, split by pass, then get out of the way."""
    from app.agents.extraction_agent import build_extraction_agent
    probe = build_extraction_agent('probe', mode='prefetch')
    completions_cls = type(probe.model.client.chat.completions)
    original = completions_cls.create
    calls: list[dict] = []

    async def spy(self, **kwargs):
        response = await original(self, **kwargs)
        usage = getattr(response, 'usage', None)
        system = next((m.get('content') for m in (kwargs.get('messages') or [])
                       if m.get('role') == 'system'), '')
        if not isinstance(system, str):
            system = json.dumps(system, ensure_ascii=False, default=str)
        calls.append({
            'kind': 'detection' if 'fast triage classifier' in system else 'extraction',
            'prompt_tokens': int(getattr(usage, 'prompt_tokens', 0) or 0),
            'completion_tokens': int(getattr(usage, 'completion_tokens', 0) or 0),
        })
        return response

    completions_cls.create = spy
    return calls, (lambda: setattr(completions_cls, 'create', original))


def _run(case: dict, mode: str) -> dict:
    from live_contract_eval import evaluate_case

    from app import config, db, llm, service
    from app.extraction_items import compile_extraction_items
    from app.knowledge import persist_extraction

    marker = uuid.uuid4().hex[:8]
    content = f'{case["text"]} [{marker}]'
    doc = service.create_document(title=f'step16 {case["id"]} {mode}', content=content,
                                  source_type='note')
    chunk = service.write_chunks(doc, content)[0]

    calls, restore = _install_provider_counter()
    try:
        previous = config._OVERRIDES.get('extraction_detection_mode')
        config._OVERRIDES['extraction_detection_mode'] = mode
        try:
            raw = asyncio.run(llm.extract([{'id': chunk['id'], 'content': content}]))
        finally:
            if previous is None:
                config._OVERRIDES.pop('extraction_detection_mode', None)
            else:
                config._OVERRIDES['extraction_detection_mode'] = previous
    finally:
        restore()

    outcome = compile_extraction_items(raw)
    with db.transaction() as conn:
        counts = persist_extraction(conn, document_id=doc, extraction=outcome.envelope)
    verdict = evaluate_case(case, outcome.envelope, counts,
                            [r.failure_view() for r in outcome.failures])
    extraction = [c for c in calls if c['kind'] == 'extraction']
    detection = [c for c in calls if c['kind'] == 'detection']
    return {
        'case': case['id'], 'mode': mode, 'text': case['text'],
        'detection_calls': len(detection),
        'detection_tokens': sum(c['prompt_tokens'] + c['completion_tokens'] for c in detection),
        'extraction_calls': len(extraction),
        'extraction_prompt_tokens': sum(c['prompt_tokens'] for c in extraction),
        'extraction_completion_tokens': sum(c['completion_tokens'] for c in extraction),
        'passed': verdict['passed'],
        'semantic_checks': [c for c in verdict['checks'] if c['layer'] == 'semantic'],
        'rejected': [r.failure_view() for r in outcome.failures],
        'observed_claims': [{'subject': c.get('subject'), 'predicate': c.get('predicate'),
                             'object': c.get('object'), 'object_kind': c.get('object_kind'),
                             'temporal_signal': c.get('temporal_signal')}
                            for c in outcome.envelope['claims']],
        'document_status': outcome.status,
        'raw_envelope': raw,
    }


def main() -> int:
    _bootstrap_env()
    from app.config import runtime
    from app.db import init_db
    if not runtime().get('openai_api_key'):
        print('no provider key configured; cannot run the live canary')
        return 3
    init_db()

    cases = {c['id']: c for c in
             json.loads(CASES_PATH.read_text(encoding='utf-8'))['live_cases']}
    rows = []
    for case_id in CASE_IDS:
        for mode in MODES:
            row = _run(cases[case_id], mode)
            rows.append(row)
            verdict = 'PASS' if row['passed']['semantic'] else 'FAIL'
            print(f"  [{verdict}] {case_id:<28} {mode:<4} detect={row['detection_calls']} "
                  f"extract={row['extraction_calls']} "
                  f"in={row['extraction_prompt_tokens']} out={row['extraction_completion_tokens']} "
                  f"claims={len(row['observed_claims'])}")
            for claim in row['observed_claims']:
                print(f"        {claim.get('subject')} --{claim.get('predicate')}--> "
                      f"{claim.get('object')} kind={claim.get('object_kind')} "
                      f"temporal={claim.get('temporal_signal')}")
            for check in row['semantic_checks']:
                if not check['ok']:
                    print(f"        BAD {check['check']}: {check['detail'][:120]}")

    summary = {}
    for mode in MODES:
        arm = [r for r in rows if r['mode'] == mode]
        summary[mode] = {
            'cases': len(arm),
            'detection_calls': sum(r['detection_calls'] for r in arm),
            'detection_tokens': sum(r['detection_tokens'] for r in arm),
            'extraction_calls': sum(r['extraction_calls'] for r in arm),
            'extraction_prompt_tokens': sum(r['extraction_prompt_tokens'] for r in arm),
            'extraction_completion_tokens': sum(r['extraction_completion_tokens'] for r in arm),
            'semantic_passed': f"{sum(1 for r in arm if r['passed']['semantic'])}/{len(arm)}",
        }
    artifact = {'what': 'Step 16 live canary: Detection ON vs OFF, three cases, provider seam.',
                'model': runtime().get('openai_model'), 'case_ids': CASE_IDS,
                'summary': summary, 'runs': rows}
    ARTIFACT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\nSUMMARY')
    for mode, block in summary.items():
        print(f'  {mode}: {json.dumps(block, ensure_ascii=False)}')
    print(f'wrote {ARTIFACT.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
