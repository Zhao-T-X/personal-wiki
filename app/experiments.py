"""Extraction Before/After experiment harness (task: real-button-driven regression).

This is NOT a second extraction pipeline. It drives the *exact* code path the
import button uses — ``service.create_document`` → ``service.write_chunks`` →
``normalize_extraction`` → ``persist_extraction`` — and only swaps the one step a
sandbox cannot perform: the model's inference. The envelope it feeds in is a
human-annotated golden extraction (see ``tests/golden_corpus``), i.e. "what a good
model would return". Everything downstream — normalization, the entity eligibility
gate, persistence, the claim→Resource fallback, and the runlog snapshot — is the
real, shipping code.

For each corpus document it runs the pipeline twice:

* **Before** — eligibility gate disabled (old behavior: every proposed entity is
  kept, every unknown claim subject becomes a Resource).
* **After**  — eligibility gate enabled (the new behavior).

It then diffs the two runs on the four metrics the task asks for and records a
snapshot per run under ``llm_runs`` so the UI can replay it.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import config as _config

# DB-bound collaborators are imported lazily inside the functions below. The claim
# consistency harness (tests/claim_entries.py) reloads app.db / app.repositories /
# app.knowledge to switch databases, and a module-level binding would freeze the
# pre-reload object. Lazy imports always resolve the live module.

_CORPUS_PATH = Path(__file__).resolve().parents[1] / 'tests' / 'golden_corpus' / 'corpus.json'


@contextmanager
def _gate(enabled: bool):
    """Force the eligibility gate for one phase, beating any persisted settings file."""
    key = 'extraction_eligibility_enabled'
    overrides = _config._OVERRIDES
    had = key in overrides
    prev = overrides.get(key)
    overrides[key] = bool(enabled)
    try:
        yield
    finally:
        if had:
            overrides[key] = prev
        else:
            overrides.pop(key, None)


def corpus_path() -> Path:
    return _CORPUS_PATH


def _create_document(*, title: str, content: str, source_type: str = 'note',
                     source_uri=None, metadata=None) -> str:
    # Local re-implementation of service.create_document that avoids importing
    # app.service (which pulls in the LLM SDK). The experiment only needs the
    # document row + chunks, not the model.
    from .repositories import DocumentRepository
    return DocumentRepository().create(title=title, content=content, source_type=source_type,
                                      source_uri=source_uri, metadata=metadata)


def _write_chunks(document_id: str, content: str):
    from .chunking import chunk_text
    from .repositories import DocumentRepository
    chunks = chunk_text(content)
    return DocumentRepository().replace_chunks(
        document_id, [(c.content, i, c.start_offset, c.end_offset) for i, c in enumerate(chunks)])


def load_corpus() -> dict:
    if not _CORPUS_PATH.exists():
        return {'version': None, 'documents': []}
    return json.loads(_CORPUS_PATH.read_text(encoding='utf-8'))


def _first_chunk(document_id: str) -> tuple[str, str]:
    from .repositories import DocumentRepository
    rows = DocumentRepository().chunks(document_id)
    return (rows[0]['id'], rows[0]['content']) if rows else ('', '')


def _persisted_entities(ids: list[str]) -> list[dict]:
    """Entities actually written by this run (KEEP + REVIEW). DROPped candidates are
    absent because they were never persisted, so this counts exactly what reached the
    pool — including entities the model declared but no claim referenced."""
    from .repositories import EntityRepository
    return [{'name': r['name'], 'status': r['status'], 'type': r['type']}
            for r in EntityRepository().by_ids(ids)]


def run_golden_document(doc: dict, *, eligibility_enabled: bool, tag: str | None = None) -> dict:
    """Run one corpus document through the real persistence pipeline once.

    Returns the snapshot: run id, counts, persisted entity names/statuses, and the
    per-entity eligibility verdicts.
    """
    from .db import transaction
    from .extraction import normalize_extraction
    from .knowledge import persist_extraction
    from .runlog import record_run
    with _gate(eligibility_enabled):
        # The product de-duplicates documents by content hash, so the Before and After
        # runs (identical input) would collide. Tag the content per run with a uuid — it
        # only shifts chunking, not the entity names/types the gate decides on.
        run_marker = 'before' if not eligibility_enabled else 'after'
        import uuid as _uuid
        content = doc['content'] + f'\n\n[experiment-run:{run_marker}-{_uuid.uuid4().hex[:8]}]'
        document_id = _create_document(
            title=doc.get('name', doc['id']), content=content,
            source_type='note', source_uri=doc.get('source'),
            metadata={'corpus_id': doc['id'], 'experiment': True,
                      'eligibility': 'on' if eligibility_enabled else 'off'})
        _write_chunks(document_id, content)

        chunk_id, chunk_content = _first_chunk(document_id)
        envelope = json.loads(json.dumps(doc['extraction'], ensure_ascii=False))
        # Give every claim a valid provenance anchor so the real persistence path
        # (which localizes evidence_quote against the chunk) runs unchanged.
        for c in envelope.get('claims', []):
            c['source_chunk'] = chunk_id
            c['evidence_quote'] = chunk_content
        try:
            normalized = normalize_extraction(envelope)
        except Exception as exc:  # surface schema/compile problems, don't swallow
            return {'document_id': document_id, 'error': f'normalize failed: {exc}',
                    'eligibility_on': eligibility_enabled}

        with record_run('extract', document_id=document_id, agent_role='extractor') as run:
            with transaction() as wconn:
                counts = persist_extraction(wconn, document_id=document_id, extraction=normalized)
            persisted = _persisted_entities(counts.get('entity_ids', []))
            summary = {
                **counts,
                'extraction_version': None,
                'experiment_tag': tag,
                'eligibility_on': eligibility_enabled,
                'extraction': normalized,
                'persisted_entities': persisted,
            }
            run.summary = summary
        return {'document_id': document_id, 'run_id': run.id, 'counts': counts,
                'persisted': persisted, 'eligibility_on': eligibility_enabled}


def _metrics(doc: dict, run: dict) -> dict:
    expected = set(doc.get('expected_entities', []))
    non_entities = set(doc.get('expected_non_entities', []))
    persisted = run.get('persisted', [])
    kept_names = {p['name'] for p in persisted}
    verified = {p['name'] for p in persisted if p['status'] == 'verified'}
    candidate = {p['name'] for p in persisted if p['status'] == 'candidate'}

    matched = kept_names & expected
    precision = round(len(matched) / max(1, len(kept_names)), 3)
    recall = round(len(matched) / max(1, len(expected)), 3)
    leaked = sorted(kept_names & non_entities)
    # False positives = kept but neither expected nor explicitly a known non-entity.
    fp = sorted(kept_names - expected - non_entities)
    return {
        'kept': sorted(kept_names),
        'verified': sorted(verified),
        'quarantined': sorted(candidate),
        'precision': precision,
        'recall': recall,
        'unsupported_rate': run.get('counts', {}).get('unsupported_entity_rate'),
        'dropped_entities': run.get('counts', {}).get('dropped_entities', 0),
        'review_entities': run.get('counts', {}).get('review_entities', 0),
        'leaked_non_entities': leaked,
        'false_positives': fp,
    }


def run_experiment(corpus: dict | None = None) -> dict:
    """Run the whole corpus Before (gate off) and After (gate on).

    Returns a structured report the script prints and the UI can render.
    """
    corpus = corpus or load_corpus()
    docs = corpus.get('documents', [])
    per_doc = []
    errors = []
    for doc in docs:
        before = run_golden_document(doc, eligibility_enabled=False, tag='before')
        after = run_golden_document(doc, eligibility_enabled=True, tag='after')
        if 'error' in before or 'error' in after:
            errors.append({'id': doc['id'], 'name': doc.get('name'),
                          'before': before, 'after': after})
            continue
        per_doc.append({
            'id': doc['id'], 'name': doc.get('name'), 'source': doc.get('source'),
            'before': _metrics(doc, before), 'after': _metrics(doc, after),
            'before_run_id': before.get('run_id'), 'after_run_id': after.get('run_id'),
        })
    # Aggregate over successful documents only.
    def agg(key):
        bs = [d['before'][key] for d in per_doc]
        as_ = [d['after'][key] for d in per_doc]
        return {'before': round(sum(bs) / max(1, len(bs)), 3) if bs else None,
                'after': round(sum(as_) / max(1, len(as_)), 3) if as_ else None}
    report = {
        'corpus_version': corpus.get('version'),
        'documents': per_doc,
        'errors': errors,
        'aggregate': {
            'precision': agg('precision'),
            'recall': agg('recall'),
            'unsupported_rate': agg('unsupported_rate'),
            'dropped_entities': agg('dropped_entities'),
            'review_entities': agg('review_entities'),
        },
    }
    return report


def run_summary_view(summary: dict | None) -> dict:
    """Flatten a stored extraction run summary for the comparison UI."""
    if not summary:
        return {}
    persisted = summary.get('persisted_entities') or []
    kept = [p['name'] for p in persisted]
    counts = summary.get('counts', {}) or summary
    return {
        'kept': kept,
        'total_entities': counts.get('entities'),
        'dropped_entities': counts.get('dropped_entities', 0),
        'review_entities': counts.get('review_entities', 0),
        'unsupported_entity_rate': counts.get('unsupported_entity_rate'),
        'claims': counts.get('claims'),
        'experiment_tag': summary.get('experiment_tag'),
        'eligibility_on': summary.get('eligibility_on'),
        'extraction_version': summary.get('extraction_version'),
    }


def diff_summaries(before: dict | None, after: dict | None) -> dict:
    b, a = run_summary_view(before), run_summary_view(after)
    bset, aset = set(b.get('kept', [])), set(a.get('kept', []))
    return {
        'before': b, 'after': a,
        'removed': sorted(bset - aset),
        'added': sorted(aset - bset),
        'kept_both': sorted(bset & aset),
    }
