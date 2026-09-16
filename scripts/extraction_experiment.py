"""Run the Extraction Before/After experiment on the Golden Corpus.

Drives the REAL persistence pipeline (service.create_document -> write_chunks ->
normalize_extraction -> persist_extraction) with the entity eligibility gate
toggled off (Before) and on (After), then prints a report.

The only substituted step is the LLM inference: the golden extraction envelope in
tests/golden_corpus/corpus.json stands in for "what a good model returns". Every
downstream rule (eligibility, claim->Resource fallback, snapshot) is the shipping
code.

Usage:
    python scripts/extraction_experiment.py
"""
from __future__ import annotations

import os
import sys
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Must set the database location before importing the app (connect() reads it lazily).
_TMP = Path(tempfile.mkdtemp(prefix='extraction_exp_'))
os.environ['DATABASE_PATH'] = str(_TMP / 'experiment.db')

from app.db import init_db  # noqa: E402
from app.experiments import run_experiment, load_corpus  # noqa: E402


def _print_report(report: dict) -> None:
    print('\n' + '=' * 72)
    print('EXTRACTION BEFORE / AFTER  —  Golden Corpus', report.get('corpus_version'))
    print('=' * 72)
    agg = report.get('aggregate', {})
    for metric, vals in agg.items():
        b, a = vals.get('before'), vals.get('after')
        arrow = 'v' if (b is not None and a is not None and a < b) else ('^' if (b is not None and a is not None and a > b) else '=')
        print(f'  {metric:22s}  Before={b}  After={a}   {arrow}')

    negatives = ['relations', 'claim_relations', '出库点检日志',
                 'docs/design/context-runtime-design.md']
    for d in report.get('documents', []):
        print('\n' + '-' * 72)
        print(f"Document: {d.get('name')}  ({d.get('id')})")
        for phase in ('before', 'after'):
            m = d[phase]
            print(f"  [{phase.upper()}] kept={len(m['kept'])} verified={len(m['verified'])} "
                  f"quarantined={len(m['quarantined'])} precision={m['precision']} "
                  f"recall={m['recall']} unsupported={m['unsupported_rate']} "
                  f"dropped={m['dropped_entities']} review={m['review_entities']}")
            if m['leaked_non_entities']:
                print(f"      leaked non-entities: {m['leaked_non_entities']}")
        after_kept = set(d['after']['kept'])
        hit = [n for n in negatives if n in after_kept]
        print("  [PASS] permanent negative cases correctly excluded/quarantined" if not hit
              else f"  [FAIL] STILL PRESENT AFTER FIX: {hit}")

    for e in report.get('errors', []):
        print('\n' + '!' * 72)
        print(f"DOCUMENT FAILED: {e.get('name')} ({e.get('id')})")
        print('  before:', e.get('before', {}).get('error'))
        print('  after :', e.get('after', {}).get('error'))


def main() -> int:
    init_db()
    corpus = load_corpus()
    if not corpus.get('documents'):
        print('No golden corpus found at', Path(__file__).resolve().parents[1] / 'tests' / 'golden_corpus' / 'corpus.json')
        return 2
    report = run_experiment(corpus)
    _print_report(report)
    out = ROOT / 'tests' / 'golden_corpus' / 'last_report.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\nReport saved to {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
