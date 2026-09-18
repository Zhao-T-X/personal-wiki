"""Step 13 / 13.1 — Live Semantic Contract Smoke (real LLM, explicit opt-in).

Step 13 reported "Predicate Resolution Accuracy = 100%" from the fact that every
predicate was *registered*. That was wrong: the model answered a CEO question with
`is`, and `is` is registered. This file separates three layers, because they fail for
different reasons:

    structural   the envelope is legal and evidence is attached        (compiler)
    registry     the proposal resolved into the closed vocabularies    (registry)
    semantic     it means what the sentence means                      (model + prompt)

Only the third layer is "the model is right". A case that is structurally perfect and
semantically wrong is a FAIL, and says so.

    pytest -m smoke     # this file: live, costs tokens
    pytest              # deselected: 0 tokens

Cost protection: at most 3 LLM calls per case, at most 20 per run; the run stops
loudly rather than looping. The observed run is written to ``live_last_run.json``;
``live_baseline.json`` is only written when the run passes **and**
``LIVE_SMOKE_APPROVE_BASELINE=1`` is set.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import db, llm, service
from app.config import runtime
from app.extraction_items import compile_extraction_items
from app.knowledge import persist_extraction
from app.object_classification import is_source_artefact
from app.runlog import record_run
from live_contract_eval import SEMANTIC, STRUCTURAL, REGISTRY, evaluate_case, metrics

pytestmark = [pytest.mark.live_llm, pytest.mark.smoke]

CASES_PATH = Path(__file__).parent / 'fixtures' / 'extraction_cases' / 'micro_cases.json'
FIXTURE_DIR = CASES_PATH.parent
BASELINE_PATH = FIXTURE_DIR / 'live_baseline.json'
LAST_RUN_PATH = FIXTURE_DIR / 'live_last_run.json'
CONTEXT_COST_PATH = FIXTURE_DIR / 'live_context_cost.json'

# Step 15.1: with prefetch, one case is exactly TWO steps — Pass 1 detection, then ONE
# extraction. A third step means the extraction took a second round, which the cutover
# forbids; the cap is now the assertion, not headroom for a repair.
MAX_STEPS_PER_CASE = 2
# 11 cases x 2 steps. A case whose Pass 1 names no kind skips Pass 2 entirely, so this is
# a ceiling rather than an equality.
MAX_STEPS_TOTAL = 22
# §5: one extraction provider call per case, 11 in total. Detection is counted separately
# and is not part of this gate.
MAX_EXTRACTION_CALLS_PER_CASE = 1
DETECTION_MARKER = 'fast triage classifier'
PRICE_IN_PER_1K = float(os.getenv('LIVE_SMOKE_PRICE_IN_PER_1K', '0.0005'))
PRICE_OUT_PER_1K = float(os.getenv('LIVE_SMOKE_PRICE_OUT_PER_1K', '0.0015'))


def _steps(run_id: str) -> dict:
    conn = db.connect()
    try:
        row = conn.execute(
            'SELECT COUNT(*) c, COALESCE(SUM(prompt_tokens),0) p,'
            ' COALESCE(SUM(completion_tokens),0) o FROM llm_run_steps WHERE run_id=?',
            (run_id,)).fetchone()
        return {'steps': row['c'], 'prompt_tokens': row['p'], 'completion_tokens': row['o']}
    finally:
        conn.close()


def _steps_detail(run_id: str) -> list[dict]:
    """Per-STEP tokens, so the cost audit can tell detection from extraction.

    A case makes two calls (Pass 1 triage, Pass 2 extraction). Summing them hides which
    one carries the 4k prompt, which is exactly what the audit needs to know.
    """
    conn = db.connect()
    try:
        rows = conn.execute(
            'SELECT name, COUNT(*) c, COALESCE(SUM(prompt_tokens),0) p,'
            ' COALESCE(SUM(completion_tokens),0) o FROM llm_run_steps WHERE run_id=?'
            ' GROUP BY name ORDER BY name', (run_id,)).fetchall()
        return [{'step': r['name'], 'calls': r['c'], 'prompt_tokens': r['p'],
                 'completion_tokens': r['o']} for r in rows]
    finally:
        conn.close()


def _install_usage_spy(monkeypatch) -> list[dict]:
    """Count the real model calls behind each ``llm_run_step``.

    A step is one logical extraction call, but the agent may make more than one model
    call inside it (a tool round plus the answer). ``agent_usage.collect`` already sums
    provider usage over those calls; the spy adds how many of them there were, which is
    what turns "4.2k tokens per step" into an explanation.
    """
    import app.agent_usage as agent_usage
    captured: list[dict] = []
    real = agent_usage.collect

    def spy(agent, result, context_index_before):
        data = dict(real(agent, result, context_index_before))
        try:
            messages = list(agent.state.context)[context_index_before:]
            calls = sum(1 for m in messages if getattr(m, 'usage', None) is not None)
        except Exception:
            calls = 0
        data['model_calls'] = max(calls, 1)
        captured.append(data)
        return data

    monkeypatch.setattr(agent_usage, 'collect', spy)
    return captured


def _call_kind(request: dict) -> str:
    """Which pass a provider call belongs to, read from the system prompt it carried.

    Both passes ride the same client class and both ask for a structured output tool, so
    tool names cannot tell them apart. The system prompt can: Pass 1 is a triage
    classifier with its own prompt.
    """
    for message in request.get('messages') or []:
        if message.get('role') != 'system':
            continue
        text = message.get('content')
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False, default=str)
        return 'detection' if DETECTION_MARKER in text else 'extraction'
    return 'unknown'


def _install_provider_counter(monkeypatch) -> list[dict]:
    """Count the REAL provider calls, split by pass (Step 15.1 §5).

    ``llm_run_steps`` records how the project *bookkept* a call; this counts how many
    requests actually left the process. The one-call-per-extraction gate has to be about
    the latter, or a hidden second round could hide behind a single step record.
    """
    from app.agents.extraction_agent import build_extraction_agent
    probe = build_extraction_agent('probe', mode='prefetch')
    completions_cls = type(probe.model.client.chat.completions)
    original = completions_cls.create
    calls: list[dict] = []

    async def spy(self, **kwargs):
        response = await original(self, **kwargs)
        usage = getattr(response, 'usage', None)
        calls.append({
            'kind': _call_kind(kwargs),
            'prompt_tokens': int(getattr(usage, 'prompt_tokens', 0) or 0),
            'completion_tokens': int(getattr(usage, 'completion_tokens', 0) or 0),
        })
        return response

    monkeypatch.setattr(completions_cls, 'create', spy)
    return calls


def _run_case(case: dict) -> dict:
    """One case through the REAL pipeline: create → chunk → extract → compile → persist."""
    marker = uuid.uuid4().hex[:8]
    content = f'{case["text"]} [{marker}]'
    doc = service.create_document(title=f'smoke {case["id"]}', content=content, source_type='note')
    chunk = service.write_chunks(doc, content)[0]
    with record_run('extract', document_id=doc, agent_role='live-smoke') as run:
        raw = asyncio.run(llm.extract([{'id': chunk['id'], 'content': content}]))
        # What the planner actually put in front of the model for THIS case (authoritative
        # value, not a re-derivation of the payload).
        context_plan = getattr(run, 'extraction_context_plan', None)
    outcome = compile_extraction_items(raw)
    with db.transaction() as conn:
        counts = persist_extraction(conn, document_id=doc, extraction=outcome.envelope)
    return {'doc': doc, 'chunk': chunk['id'], 'content': content, 'run_id': run.id,
            'raw': raw, 'outcome': outcome, 'counts': counts, 'context_plan': context_plan}


def test_live_semantic_contract_smoke(monkeypatch):
    db.init_db()
    cases = json.loads(CASES_PATH.read_text(encoding='utf-8'))['live_cases']
    assert len(cases) == 11, f'expected the 11 contract cases, found {len(cases)}'
    for case in cases:
        assert 'semantic_expected' in case, f"{case['id']} has no semantic gold"
    assert sum(1 for c in cases if c.get('kind', 'semantic') == 'abstention') == 1

    # Single-case verification: `LIVE_SMOKE_ONLY=literal_chunk_size` runs one case through
    # the same production path, so re-checking one semantic contract costs <=3 calls
    # instead of the whole 11-case suite. The suite itself is unchanged.
    only = [name.strip() for name in (os.getenv('LIVE_SMOKE_ONLY') or '').split(',') if name.strip()]
    if only:
        cases = [c for c in cases if c['id'] in only]
        assert cases, f'LIVE_SMOKE_ONLY selected no case: {only}'
        print(f'  (filtered to {[c["id"] for c in cases]})')

    # Step 12 regression, deterministic and free.
    assert is_source_artefact('docs/context/runtime.md') is True
    assert is_source_artefact('https://example.com/a/b') is True
    assert is_source_artefact('Static Context / Dynamic Context') is False
    assert is_source_artefact('客户端/服务端架构') is False

    # Step 15.1: this suite verifies the PRODUCTION default, so it must not silently run
    # the mode it is supposed to be testing.
    from app.agents.extraction_agent import extraction_context_mode
    assert extraction_context_mode() == 'prefetch', (
        f"running under {extraction_context_mode()!r}; this suite verifies the prefetch "
        "cutover — set EXTRACTION_CONTEXT_MODE=prefetch")

    usage_events = _install_usage_spy(monkeypatch)
    provider_calls = _install_provider_counter(monkeypatch)
    budget = {'steps': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'model_calls': 0}
    results: list[dict] = []
    for case in cases:
        before_events = len(usage_events)
        before_calls = len(provider_calls)
        res = _run_case(case)
        events = usage_events[before_events:]
        used = _steps(res['run_id'])
        model_calls = sum(e.get('model_calls', 1) for e in events) or used['steps']

        case_calls = provider_calls[before_calls:]
        extraction_calls = [c for c in case_calls if c['kind'] == 'extraction']
        if len(extraction_calls) != MAX_EXTRACTION_CALLS_PER_CASE:
            raise AssertionError(
                f"{case['id']}: {len(extraction_calls)} extraction provider calls "
                f"(expected {MAX_EXTRACTION_CALLS_PER_CASE}); kinds="
                f"{[c['kind'] for c in case_calls]} — prefetch has no second round to fall "
                "back to, so this is a defect, not a variance")
        if used['steps'] > MAX_STEPS_PER_CASE:
            raise AssertionError(
                f"{case['id']}: {used['steps']} LLM calls for one case "
                f"(> {MAX_STEPS_PER_CASE}) — unbounded repair/retry")
        budget['steps'] += used['steps']
        budget['prompt_tokens'] += used['prompt_tokens']
        budget['completion_tokens'] += used['completion_tokens']
        budget['model_calls'] += model_calls
        if budget['steps'] > MAX_STEPS_TOTAL:
            raise AssertionError(f'live smoke exceeded {MAX_STEPS_TOTAL} LLM calls: {budget}')

        verdict = evaluate_case(case, res['outcome'].envelope, res['counts'],
                                [r.failure_view() for r in res['outcome'].failures])
        extraction_view = [c for c in case_calls if c['kind'] == 'extraction']
        results.append({
            'id': case['id'], 'kind': case.get('kind', 'semantic'), 'covers': case['covers'],
            'text': case['text'],
            'checks': verdict['checks'], 'by_layer': verdict['by_layer'],
            'passed': verdict['passed'],
            'steps': used['steps'], 'model_calls': model_calls,
            'provider_calls': {
                # Step 15.1: the extraction/detection split, provider-measured.
                'extraction': len(extraction_view),
                'detection': sum(1 for c in case_calls if c['kind'] == 'detection'),
                'extraction_prompt_tokens': sum(c['prompt_tokens'] for c in extraction_view),
                'extraction_completion_tokens': sum(c['completion_tokens'] for c in extraction_view),
            },
            'context_plan': res['context_plan'],
            'by_step': _steps_detail(res['run_id']),
            'prompt_tokens': used['prompt_tokens'],
            'completion_tokens': used['completion_tokens'],
            'raw_envelope': res['raw'],
            'observed': {
                'status': res['outcome'].status,
                'entities': res['outcome'].envelope['entities'],
                'claims': res['outcome'].envelope['claims'],
                'rejected': [r.failure_view() for r in res['outcome'].failures],
                'imprecise_quotes': res['counts'].get('imprecise_quotes', 0),
            },
        })

    # §5, in total as well as per case: exactly one extraction request per case.
    extraction_total = sum(r['provider_calls']['extraction'] for r in results)
    assert extraction_total == len(results), (
        f'{extraction_total} extraction provider calls for {len(results)} cases — '
        'the prefetch cutover is one request per case, with no second round to fall back to')

    failed = [r for r in results if not r['passed'][SEMANTIC]]
    for r in results:
        layers = ' '.join(f"{layer}={'PASS' if r['passed'][layer] else 'FAIL'}"
                          for layer in (STRUCTURAL, REGISTRY, SEMANTIC))
        print(f"  [{('PASS' if r['passed'][SEMANTIC] else 'FAIL')}] {r['id']} "
              f"({r['kind']}/{r['covers']}) steps={r['steps']} in={r['prompt_tokens']} {layers}")
        for check in r['checks']:
            if not check['ok']:
                print(f"      {check['layer']:<10} {check['check']}: {check['detail']}")

    score = metrics(results)
    print('LIVE_SEMANTIC_METRICS')
    for key, value in score.items():
        print(f'  {key}={value}')

    estimated = round(budget['prompt_tokens'] / 1000 * PRICE_IN_PER_1K
                      + budget['completion_tokens'] / 1000 * PRICE_OUT_PER_1K, 6)
    by_step: dict[str, dict] = {}
    for r in results:
        for s in r['by_step']:
            agg = by_step.setdefault(s['step'], {'calls': 0, 'prompt_tokens': 0,
                                                 'completion_tokens': 0})
            for key in ('calls', 'prompt_tokens', 'completion_tokens'):
                agg[key] += s[key]
    cost = {**budget, 'cases': len(results), 'estimated_cost_usd': estimated,
            'price_per_1k_in': PRICE_IN_PER_1K, 'price_per_1k_out': PRICE_OUT_PER_1K,
            'prompt_tokens_per_step': round(budget['prompt_tokens']
                                            / max(1, budget['steps'])),
            'by_step': by_step}
    print('LIVE_SMOKE_COST_REPORT')
    for key, value in cost.items():
        print(f'  {key}={value}')

    run_record = {
        'ran_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'model': runtime().get('openai_model'),
        'extraction_context_mode': extraction_context_mode(),
        'provider_calls': {
            'extraction': sum(r['provider_calls']['extraction'] for r in results),
            'detection': sum(r['provider_calls']['detection'] for r in results),
            'extraction_prompt_tokens': sum(r['provider_calls']['extraction_prompt_tokens']
                                            for r in results),
            'extraction_completion_tokens': sum(r['provider_calls']['extraction_completion_tokens']
                                                for r in results),
        },
        'context_plan': {r['id']: r['context_plan'] for r in results if r['context_plan']},
        'metrics': score, 'cost': cost, 'cases': results,
    }
    LAST_RUN_PATH.write_text(json.dumps(run_record, ensure_ascii=False, indent=2),
                             encoding='utf-8')

    # Per-case prompt cost, merged into the offline composition measurement so the
    # artifact answers "how much per call, and how much of it is fixed overhead".
    if CONTEXT_COST_PATH.exists():
        artifact = json.loads(CONTEXT_COST_PATH.read_text(encoding='utf-8'))
    else:
        artifact = {'what': 'Where the prompt tokens of one extraction call come from.'}
    artifact['runs'] = [*artifact.get('runs', [])[-4:], {
        'ran_at': run_record['ran_at'], 'model': run_record['model'], 'cost': cost,
        'cases': [{'case': r['id'], 'steps': r['steps'], 'model_calls': r['model_calls'],
                   'prompt_tokens': r['prompt_tokens'],
                   'completion_tokens': r['completion_tokens'],
                   'chars_in': len(r['text'])} for r in results],
    }]
    CONTEXT_COST_PATH.write_text(json.dumps(artifact, ensure_ascii=False, indent=2),
                                 encoding='utf-8')

    if BASELINE_PATH.exists() and not only:
        # Only meaningful for a full-suite run: a filtered run deliberately covers less
        # than the baseline, which is not drift.
        baseline = json.loads(BASELINE_PATH.read_text(encoding='utf-8'))
        ids = {r['id'] for r in results}
        missing = [c['id'] for c in baseline['cases'] if c['id'] not in ids]
        assert not missing, f'baseline cases no longer run: {missing}'

    if os.getenv('LIVE_SMOKE_APPROVE_BASELINE') == '1':
        assert not failed, 'refusing to approve a baseline from a semantically failing run'
        BASELINE_PATH.write_text(json.dumps({
            **run_record, 'approved': True,
            'approval': 'human-reviewed: every structural, registry AND semantic '
                        'invariant passed on the real model',
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'baseline approved -> {BASELINE_PATH.name}')

    assert not failed, ('semantic failures: '
                        + ', '.join(f"{r['id']}[{','.join(c['check'] for c in r['checks'] if c['layer'] == SEMANTIC and not c['ok'])}]"
                                    for r in failed))
