"""Semantic-boundary Before/After experiment on REAL documents via the REAL pipeline.

Drives the exact production import path — ``service.create_document`` →
``service.write_chunks`` → ``service.index_document`` (real LLM extraction → normalize
→ persist → autolink) — on a set of real project documents, in one of two phases:

  --phase before   eligibility gate OFF  (original behaviour, pre-fix)
  --phase after    eligibility gate ON   (semantic-boundary fix)

It runs against a throw-away SQLite database but uses the real configured LLM, so no
API key juggling and the developer's own wiki is never touched.

Metrics recorded per document: entities created, claims, free-text (unlinked) object
claims, dropped/review entities, auto-created subjects, object links. Globally: the
integrity scan's duplicate candidates and object-link proposals (incl. how many are
NOT entity-like — the "literal treated as an entity" noise), and the Review inbox.

Usage:
    .venv\\Scripts\\python -m scripts.semantic_boundary_experiment --phase before
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Real project documents, chosen because they mention the exact constructs that
# produce scattered entities (standards, DTOs, relations, chunking, evidence packs).
CORPUS = [
    'docs/standards/10-EXTRACTION-V2-STANDARD.md',
    'docs/standards/11-NORMALIZATION-ENGINE-STANDARD.md',
    'docs/architecture/DOMAIN-MODEL.md',
    'docs/architecture/KNOWLEDGE-OPERATIONS.md',
    'docs/superpowers/specs/2026-09-11-context-runtime-design.md',
]


def _configure_env(phase: str) -> Path:
    """Point the app at a throw-away DB + the real LLM config, and set phase flags.

    Must run before importing any app module (config reads env at import time).
    """
    tmp = Path(tempfile.mkdtemp(prefix=f'sembound_{phase}_'))
    real = ROOT / 'data' / 'settings.json'
    settings: dict = {}
    if real.exists():
        try:
            settings = json.loads(real.read_text(encoding='utf-8'))
        except Exception:
            settings = {}
    settings['database_path'] = str(tmp / 'wiki.db')
    settings.setdefault('openai_api_key', os.getenv('OPENAI_API_KEY', ''))
    settings.setdefault('openai_base_url', os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1'))
    settings.setdefault('openai_model', os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'))
    settings.setdefault('openai_embedding_model', os.getenv('OPENAI_EMBEDDING_MODEL', 'text-embedding-3-small'))
    settings['auto_embed'] = False
    (tmp / 'settings.json').write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
    os.environ['SETTINGS_PATH'] = str(tmp / 'settings.json')
    os.environ['DATABASE_PATH'] = str(tmp / 'wiki.db')
    os.environ['AUTO_EMBED'] = 'false'
    on = 'true' if phase == 'after' else 'false'
    os.environ['EXTRACTION_ELIGIBILITY'] = on
    os.environ['OBJECT_CLASSIFICATION'] = on
    return tmp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--phase', choices=['before', 'after'], required=True)
    ap.add_argument('--limit', type=int, default=len(CORPUS))
    ap.add_argument('--maxchars', type=int, default=6000)
    args = ap.parse_args()

    _configure_env(args.phase)

    from app.db import connect, init_db
    from app import service
    from app.config import runtime
    from app.integrity import scan as integrity_scan
    from app.review import inbox
    from app.object_classification import classify_object

    init_db()
    cfg = runtime()
    if not cfg.get('openai_api_key'):
        print('LLM not configured (no openai_api_key). Cannot run the real pipeline.')
        return 3
    print(f"[{args.phase}] model={cfg.get('openai_model')} db={cfg.get('database_path')}")

    documents = []
    for rel in CORPUS[:args.limit]:
        path = ROOT / rel
        if not path.exists():
            print(f'  skip (missing): {rel}')
            continue
        content = path.read_text(encoding='utf-8', errors='ignore')[:args.maxchars]
        doc_id = service.create_document(title=path.name, content=content, source_type='note',
                                         source_uri=rel, metadata={'semantic_experiment': args.phase})
        service.write_chunks(doc_id, content)
        try:
            report = asyncio.run(service.index_document(doc_id, use_llm=True,
                                                        experiment_tag=f'sembound-{args.phase}'))
        except Exception as exc:  # a single bad LLM claim must not abort the corpus
            documents.append({'doc': rel, 'error': f'{type(exc).__name__}: {exc}'})
            print(f'  {rel}: ERROR {exc}')
            continue
        counts = report.get('counts', {}) or {}
        conn = connect()
        try:
            free_text = conn.execute(
                "SELECT COUNT(*) c FROM claims WHERE source_document_id=? AND object_id IS NULL "
                "AND TRIM(COALESCE(object_text,''))!=''", (doc_id,)).fetchone()['c']
            raw = conn.execute(
                "SELECT summary_json FROM llm_runs WHERE task_type='extract' AND document_id=? "
                "ORDER BY created_at DESC LIMIT 1", (doc_id,)).fetchone()
        finally:
            conn.close()
        summary = json.loads(raw['summary_json']) if raw and raw['summary_json'] else {}
        extraction = summary.get('extraction', {}) or {}
        ent_names = [e['name'] for e in extraction.get('entities', [])]
        obj_texts = [c.get('object') for c in extraction.get('claims', []) if c.get('object')]
        obj_classes = {t: classify_object(t)[0] for t in dict.fromkeys(obj_texts)}
        documents.append({
            'doc': rel, 'entity_created': counts.get('entities'), 'claims': counts.get('claims'),
            'dropped': counts.get('dropped_entities', 0), 'review': counts.get('review_entities', 0),
            'auto_subjects': counts.get('auto_created_subjects', 0),
            'object_links': counts.get('object_links', 0), 'free_text_objects': free_text,
            'entities': ent_names, 'object_texts': obj_texts, 'object_classes': obj_classes,
        })
        print(f"  {rel}: entities={counts.get('entities')} claims={counts.get('claims')} "
              f"free_text={free_text} dropped={counts.get('dropped_entities', 0)} "
              f"review={counts.get('review_entities', 0)} links={counts.get('object_links', 0)}")

    conn = connect()
    try:
        entity_total = conn.execute('SELECT COUNT(*) c FROM entities').fetchone()['c']
        claim_total = conn.execute('SELECT COUNT(*) c FROM claims').fetchone()['c']
    finally:
        conn.close()

    scan = integrity_scan(duplicate_limit=200, unlinked_limit=200)
    proposals = scan['unlinked_claims']
    non_entity_props = [p for p in proposals
                        if classify_object(p.get('object_text') or '')[0] != 'entity']
    ib = inbox()
    report = {
        'phase': args.phase,
        'documents': documents,
        'scan_counts': scan['counts'],
        'global': {
            'entity_total': entity_total, 'claim_total': claim_total,
            'duplicate_candidates': scan['counts']['duplicate_entities'],
            'unlinked_object_proposals': scan['counts']['unlinked_claims'],
            'non_entity_object_proposals': len(non_entity_props),
            'review_total': ib['total'],
            'object_link_suggestions': ib['maintenance']['object_link_suggestions'],
        },
    }
    out = ROOT / 'tests' / 'golden_corpus' / f'semantic_{args.phase}.json'
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\nGLOBAL', json.dumps(report['global'], ensure_ascii=False, indent=2))
    print(f'\nSaved {out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
