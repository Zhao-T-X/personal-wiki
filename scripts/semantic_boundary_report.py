"""Render the semantic-boundary Before/After report from the two experiment JSONs."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
R = ROOT / 'tests' / 'golden_corpus'
METRICS = ['entity_total', 'claim_total', 'duplicate_candidates', 'unlinked_object_proposals',
           'non_entity_object_proposals', 'review_total', 'object_link_suggestions',
           'entity_eligibility', 'object_classes']


def main() -> None:
    before = json.loads((R / 'semantic_before.json').read_text(encoding='utf-8'))
    after = json.loads((R / 'semantic_after.json').read_text(encoding='utf-8'))
    print('GLOBAL METRIC'.ljust(30), 'BEFORE'.rjust(12), 'AFTER'.rjust(12))
    for k in METRICS:
        print(k.ljust(30), str(before['global'].get(k, '-')).rjust(12),
              str(after['global'].get(k, '-')).rjust(12))

    bmap = {d['doc']: d for d in before['documents']}
    amap = {d['doc']: d for d in after['documents']}
    for doc, bd in bmap.items():
        ad = amap.get(doc, {})
        print('\n==', doc)
        if 'entities' not in bd or 'entities' not in ad:
            print('   before:', bd.get('error', 'ok'), '| after:', ad.get('error', 'ok'))
            continue
        be, ae = set(bd['entities']), set(ad['entities'])
        print(f'   entities BEFORE={len(be)} AFTER={len(ae)}')
        print('   removed:', sorted(be - ae))
        print('   added  :', sorted(ae - be))
        classes: dict[str, list[str]] = {}
        for text, cls in bd.get('object_classes', {}).items():
            classes.setdefault(cls, []).append(text)
        print('   before object classes:', {k: len(v) for k, v in classes.items()})
        samples = {k: v[:3] for k, v in classes.items() if k != 'entity'}
        print('   sample non-entity objects:', samples)


if __name__ == '__main__':
    main()
