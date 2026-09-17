"""Step 9.1 boundary check against real SQLite.

Part A — a deterministic scenario on a real (throw-away) SQLite DB: KEEP / REVIEW /
legacy entities, a fallback-created entity, and every semantic pool + the Review queue.

Part B — read the developer's real ``./data/wiki.db`` (read-only) and report how many
entities sit in each eligibility band and how many would enter the semantic pool vs the
Review queue.

Run: .venv\\Scripts\\python.exe scripts\\entity_eligibility_boundary_check.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix='elig_boundary_'))
os.environ['SETTINGS_PATH'] = str(_TMP / 'settings.json')
os.environ['DATABASE_PATH'] = str(_TMP / 'boundary.db')


def _row(label, in_resolution, in_pool, in_review, in_scan):
    print(f'  {label:26s} resolution={str(in_resolution):5s} linking_pool={str(in_pool):5s} '
          f'scan_pairs={str(in_scan):5s} review_queue={str(in_review)}')


def part_a() -> None:
    from app.db import init_db, transaction
    from app.domain.entity_eligibility import get_entity_eligibility
    from app.integrity import entity_pool
    from app.repositories import EntityRepository
    from app.resolution import find_similar_entities, resolve_or_create_entity, scan_duplicate_entities

    init_db()
    repo = EntityRepository()

    def mk(eid, name, eligibility=None):
        props = {} if eligibility is None else {'eligibility': eligibility}
        repo.insert(eid, type='Organization', types=['Organization'], name=name,
                    aliases=[], description=None, properties=props)

    mk('bk1', 'Acme KEEP Inc', 'keep')
    mk('bk2', 'Acme KEEP Computer', 'keep')
    mk('br1', 'Acme REVIEW Inc', 'review')
    mk('br2', 'Acme REVIEW Computer', 'review')
    mk('bl1', 'Acme LEGACY Inc', None)
    mk('bl2', 'Acme LEGACY Computer', None)

    # Fallback-created entity through the real persistence path (claim references an
    # undeclared subject → auto-created).
    from app import service
    from app.extraction import normalize_extraction
    from app.knowledge import persist_extraction
    content = f'seed {uuid.uuid4().hex[:8]}'
    doc = service.create_document(title='boundary', content=content, source_type='note')
    chunk = service.write_chunks(doc, content)[0]
    envelope = {
        'entities': [{'name': 'Acme Declared', 'types': ['Concept'], 'aliases': []}],
        'claims': [{'subject': 'Acme Fallback Subject', 'predicate': 'used_for',
                    'object': 'Acme Declared', 'claim_type': 'factual', 'polarity': 'positive',
                    'modality': 'asserted', 'context': {}, 'confidence': 0.9,
                    'source_chunk': chunk['id'], 'evidence_quote': 'seed'}],
        'events': [], 'ideas': [], 'questions': [],
    }
    with transaction() as conn:
        persist_extraction(conn, document_id=doc, extraction=normalize_extraction(envelope))

    resolution = {r['name'] for r in repo.resolution_candidates(500)}
    pool = {e['name'] for e in entity_pool()}
    review = {r['name'] for r in repo.candidates(500)}
    pairs = scan_duplicate_entities(None, pair_limit=100)
    pair_names = set()
    for p in pairs:
        pair_names |= {p['entity_a']['name'], p['entity_b']['name']}
    similar = {h['name'] for h in find_similar_entities(None, 'bk1', limit=10)}

    print('\n== Part A: boundary scenario (real SQLite) ==')
    _row('KEEP', 'Acme KEEP Inc' in resolution, 'Acme KEEP Inc' in pool,
         'Acme KEEP Inc' in review, 'Acme KEEP Computer' in similar)
    _row('REVIEW', 'Acme REVIEW Inc' in resolution, 'Acme REVIEW Inc' in pool,
         'Acme REVIEW Inc' in review, 'Acme REVIEW Inc' in pair_names)
    _row('legacy (no key)', 'Acme LEGACY Inc' in resolution, 'Acme LEGACY Inc' in pool,
         'Acme LEGACY Inc' in review, 'Acme LEGACY Inc' in pair_names)

    fb = repo.by_name('Acme Fallback Subject')
    fb_props = repo.get(fb['id'])['properties'] if fb else {}
    print(f"  fallback entity eligibility = {get_entity_eligibility(fb_props).value!r} "
          f"(properties non-empty: {bool(fb_props)})")

    ok = (
        'Acme KEEP Inc' in resolution and 'Acme KEEP Inc' in pool and 'Acme KEEP Inc' not in review
        and 'Acme REVIEW Inc' not in resolution and 'Acme REVIEW Inc' not in pool and 'Acme REVIEW Inc' in review
        and 'Acme LEGACY Inc' not in resolution and 'Acme LEGACY Inc' not in pool and 'Acme LEGACY Inc' in review
        and bool(fb_props)
    )
    print(f"  Boundary CLOSED (scenario): {ok}")


def part_b() -> None:
    db = ROOT / 'data' / 'wiki.db'
    print(f'\n== Part B: real DB {db} ==')
    if not db.exists():
        print('  (no real DB present)')
        return
    conn = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        rows = conn.execute('SELECT status, properties_json FROM entities').fetchall()
    finally:
        conn.close()
    bands = {'keep': 0, 'review': 0, 'missing(legacy)': 0}
    def eligible(props):
        try:
            return (json.loads(props or '{}') or {}).get('eligibility')
        except Exception:
            return None
    review_queue = 0
    for status, props in rows:
        value = eligible(props)
        if value == 'keep':
            bands['keep'] += 1
        elif value == 'review':
            bands['review'] += 1
        else:
            bands['missing(legacy)'] += 1
        if status == 'candidate' and value != 'keep':
            review_queue += 1
    print(f'  entities total      = {len(rows)}')
    print(f'  semantic pool (KEEP)= {bands["keep"]}')
    print(f'  review band         = {bands["review"]}')
    print(f'  legacy (no key)     = {bands["missing(legacy)"]}')
    print(f'  review queue now    = {review_queue}  (REVIEW + legacy, status=candidate)')


def main() -> int:
    part_a()
    part_b()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
