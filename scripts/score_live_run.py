"""Re-score a RECORDED live run against the semantic gold. Zero tokens, zero model calls.

The shape of a live run is not the same as its meaning, so a recorded run stays useful
after the fact: it can be re-scored whenever the contract, the metrics or the gold are
tightened, without spending another cent. The recorded raw envelope is pushed through
the real ``compile_extraction_items``, so the numbers match what persistence would see.

    python scripts/score_live_run.py --run live_baseline.json
    python scripts/score_live_run.py --run live_last_run.json --failures-out live_failures.json

``--failures-out`` writes the real model output of every semantically failing case, which
is what makes those failures replayable later (Step 13.1 §9).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

CASES_PATH = ROOT / 'tests' / 'fixtures' / 'extraction_cases' / 'micro_cases.json'
FIXTURE_DIR = CASES_PATH.parent


def load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding='utf-8'))['live_cases']


def score(run_path: Path) -> dict:
    from app.extraction_items import compile_extraction_items
    from live_contract_eval import evaluate_case, metrics

    run = json.loads(run_path.read_text(encoding='utf-8'))
    cases = {c['id']: c for c in load_cases()}
    results, failures = [], []
    for recorded in run['cases']:
        case = cases.get(recorded['id'])
        if case is None:
            continue
        outcome = compile_extraction_items(recorded['raw_envelope'])
        counts = {'imprecise_quotes': recorded.get('observed', {}).get('imprecise_quotes', 0)}
        verdict = evaluate_case(case, outcome.envelope, counts,
                                [r.failure_view() for r in outcome.failures])
        results.append({'id': recorded['id'], 'kind': case.get('kind', 'semantic'),
                        **verdict,
                        'envelope': outcome.envelope,
                        'raw_envelope': recorded['raw_envelope']})
        if not verdict['passed']['semantic']:
            failures.append({
                'id': recorded['id'], 'covers': case['covers'], 'text': case['text'],
                'semantic_expected': case['semantic_expected'],
                'failed_checks': [{'check': c['check'], 'detail': c['detail']}
                                  for c in verdict['semantic_failures']],
                'raw_llm_output': recorded['raw_envelope'],
                'observed': recorded.get('observed'),
            })
    return {'run': run_path.name, 'ran_at': run.get('ran_at'), 'model': run.get('model'),
            'metrics': metrics(results), 'results': results, 'failures': failures}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', default='live_baseline.json')
    ap.add_argument('--failures-out', default=None)
    ap.add_argument('--approve', action='store_true',
                    help='promote this recorded run to the approved baseline. Costs nothing: '
                         'approval is a human decision about an already-recorded run, and it '
                         'refuses to approve anything that failed.')
    args = ap.parse_args()

    report = score(FIXTURE_DIR / args.run)
    m = report['metrics']
    print(f"re-scored {report['run']} (model={report['model']}, ran_at={report['ran_at']}) — 0 model calls")
    print(f"  structural_compliance : {m['structural_compliance']}")
    print(f"  registry_compliance   : {m['registry_compliance']}")
    print(f"  semantic_accuracy     : {m['semantic_accuracy']}   "
          f"(checks {m['semantic_checks_passed']})")
    print(f"  abstention_behavior   : {m['abstention_behavior']}")
    print(f"  semantic failures     : {m['failed_cases'] or '(none)'}")
    print(f"  abstention failures   : {m['failed_abstention'] or '(none)'}")
    for result in report['results']:
        flag = 'PASS' if result['passed']['semantic'] else 'FAIL'
        print(f"    [{flag}] {result['id']:<32} structural={result['passed']['structural']} "
              f"registry={result['passed']['registry']} semantic={result['passed']['semantic']}")
        for check in result['semantic_failures']:
            print(f"           {check['check']}: {check['detail']}")

    if args.approve:
        assert not report['failures'] and not report['metrics']['failed_abstention'], (
            'refusing to approve a baseline from a failing run: '
            f"{report['metrics']['failed_cases']} {report['metrics']['failed_abstention']}")
        recorded = json.loads((FIXTURE_DIR / args.run).read_text(encoding='utf-8'))
        recorded['approved'] = True
        recorded['approval'] = ('human-reviewed: structural, registry, semantic accuracy and '
                                'abstention behaviour all passed on the real model')
        recorded['metrics'] = report['metrics']
        (FIXTURE_DIR / 'live_baseline.json').write_text(
            json.dumps(recorded, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"approved {args.run} -> live_baseline.json "
              f"(semantic={report['metrics']['semantic_accuracy']}, "
              f"abstention={report['metrics']['abstention_behavior']})")

    if args.failures_out:
        out = FIXTURE_DIR / args.failures_out
        entry = {'source_run': report['run'], 'model': report['model'],
                 'ran_at': report['ran_at'], 'cases': report['failures']}
        history: list[dict] = []
        if out.exists():
            existing = json.loads(out.read_text(encoding='utf-8'))
            history = list(existing.get('history') or [])
            if not history and existing.get('cases'):
                # Upgrade the earlier single-run shape without losing it.
                history.append({'source_run': existing.get('source_run'),
                                'model': existing.get('model'),
                                'ran_at': existing.get('ran_at'),
                                'cases': existing['cases']})
        # Keyed by run time, not file name: `live_last_run.json` is overwritten by every
        # live run, and the failure it recorded must survive the fix that removed it.
        history = [h for h in history if h.get('ran_at') != entry['ran_at']] + [entry]
        out.write_text(json.dumps({
            'what': 'Real model output for every case whose SEMANTIC gold failed. These are '
                    'replay fixtures, not summaries: the raw envelope is what the model produced. '
                    'Kept per run, so a later fix does not erase the evidence it fixed.',
            'history': history,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\nwrote {out.relative_to(ROOT)} '
              f'({len(report["failures"])} failing case(s) in {entry["source_run"]})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
