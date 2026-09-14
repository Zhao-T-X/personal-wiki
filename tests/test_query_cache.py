"""§33 Query Cache — the read-through cache on ``ClaimRepository.related()``.

``related(subject, predicate)`` is the hottest read in the system: the direct-fact
lookup asks it once per question, and the correction planner asks it on every
plan. Caching it is only safe if the cache is *never* allowed to outlive the data
it describes, so the properties pinned here are:

* a repeated identical query is served from memory (hits, not SQL);
* **any write invalidates it immediately** — the cached answer can never hide a
  claim that was just written (spec §33: never serve stale knowledge);
* the cached rows are handed out as copies, so a caller editing a result cannot
  corrupt what the next caller is served;
* each input in the key (predicate / limit / exclude_id) is kept separate.

Isolation mirrors ``tests/test_correction.py``: point ``DATABASE_PATH`` at a temp
file, reload the modules that bind configuration at import time, then init. No
test here touches the network or the model.
"""
from __future__ import annotations

import importlib
import os
import sys

_RELOAD = (
    'app.repositories.base',
    'app.repositories.document_repo',
    'app.repositories.entity_repo',
    'app.repositories.claim_repo',
    'app.repositories.relation_repo',
    'app.repositories.evidence_repo',
    'app.repositories.event_repo',
    'app.repositories.idea_repo',
    'app.repositories.question_repo',
    'app.repositories.research_repo',
    'app.repositories.run_repo',
    'app.repositories.catalog_repo',
    'app.repositories.operation_repo',
    'app.resolution',
    'app.knowledge',
    'app.claim_relations',
    'app.service',
)


def _reload(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config)
    importlib.reload(db)
    for name in _RELOAD:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


def _seed(tmp_path, claims, *, extra=()):
    """Fresh DB + one document/chunk, then one claim per ``(subject, predicate, object)``.

    Claims go straight through the repository (no ontology gate): this suite is
    about the cache, not about predicate resolution. A module reload in
    ``_reload`` also installs a brand-new, empty cache, so hit/miss counters here
    always start from zero. Returns the id of the first subject, the created claim
    ids, and the doc/chunk an appended claim can reuse.
    """
    _reload(tmp_path)
    from app.db import transaction
    from app.repositories.claim_repo import ClaimRepository
    from app.repositories.document_repo import DocumentRepository
    from app.resolution import resolve_or_create_entity

    docs = DocumentRepository()
    doc_id = docs.create(title='Source', content='seeded')
    chunk_id = docs.replace_chunks(doc_id, [('seeded', 0, 0, 6)])[0]['id']

    names = sorted({s for s, _, _ in claims} | {o for _, _, o in claims} | set(extra))
    ids: dict[str, str] = {}
    with transaction() as conn:
        for name in names:
            ids[name] = resolve_or_create_entity(
                conn, name=name, entity_types=['Organization'], aliases=[],
                description=None, properties={})

    repo = ClaimRepository()
    claim_ids = []
    for subject, predicate, obj in claims:
        claim_ids.append(repo.insert(
            subject_id=ids[subject], predicate=predicate, object_id=ids[obj], object_text=obj,
            content=f'{subject} {predicate} {obj}.', context={}, claim_type='factual',
            polarity='positive', modality='asserted', confidence=0.9, status='candidate',
            created_by='test', source_document_id=doc_id, source_chunk_id=chunk_id,
            source_start_offset=0, source_end_offset=6, source_quote='seeded'))

    return {'entities': ids, 'subject_id': ids[claims[0][0]],
            'doc_id': doc_id, 'chunk_id': chunk_id, 'claim_ids': claim_ids}


def _append_claim(seed, subject, predicate, obj):
    """A later write for the same ``(subject, predicate)`` — used to prove invalidation."""
    from app.db import transaction
    from app.repositories.claim_repo import ClaimRepository
    from app.resolution import resolve_or_create_entity

    with transaction() as conn:
        subject_id = resolve_or_create_entity(conn, name=subject, entity_types=['Organization'],
                                              aliases=[], description=None, properties={})
        object_id = resolve_or_create_entity(conn, name=obj, entity_types=['Organization'],
                                             aliases=[], description=None, properties={})
    claim_id = ClaimRepository().insert(
        subject_id=subject_id, predicate=predicate, object_id=object_id, object_text=obj,
        content=f'{subject} {predicate} {obj}.', context={}, claim_type='factual',
        polarity='positive', modality='asserted', confidence=0.9, status='candidate',
        created_by='test', source_document_id=seed['doc_id'], source_chunk_id=seed['chunk_id'],
        source_start_offset=0, source_end_offset=6, source_quote='seeded')
    return subject_id, claim_id


def test_repeated_related_query_hits_the_cache(tmp_path):
    seed = _seed(tmp_path, [('Apple', 'has_ceo', 'Tim')])
    from app.repositories.claim_repo import ClaimRepository, related_cache_stats

    repo = ClaimRepository()
    first = repo.related(subject_id=seed['subject_id'], predicate='has_ceo',
                         exclude_id='', limit=10)
    after_first = related_cache_stats()
    second = repo.related(subject_id=seed['subject_id'], predicate='has_ceo',
                          exclude_id='', limit=10)
    after_second = related_cache_stats()

    assert after_first['name'] == 'claim_related'
    assert after_first['hits'] == 0 and after_first['misses'] == 1   # first call computed it
    assert after_second['hits'] == 1                                 # second call reused it
    assert after_second['size'] == 1
    assert first == second
    assert [row['id'] for row in first] == seed['claim_ids']


def test_a_write_immediately_invalidates_the_cache(tmp_path):
    """The one that matters: a write between two identical reads must be seen."""
    seed = _seed(tmp_path, [('Apple', 'has_ceo', 'Tim')])
    from app.repositories.claim_repo import ClaimRepository, related_cache_stats

    repo = ClaimRepository()
    warm = repo.related(subject_id=seed['subject_id'], predicate='has_ceo',
                        exclude_id='', limit=10)
    assert [row['id'] for row in warm] == seed['claim_ids']

    # A new claim sharing subject and predicate is a write; it bumps the data epoch.
    _, new_id = _append_claim(seed, 'Apple', 'has_ceo', 'Alice')

    after = repo.related(subject_id=seed['subject_id'], predicate='has_ceo',
                         exclude_id='', limit=10)
    ids = {row['id'] for row in after}
    # A stale cache would still hold exactly the pre-write row; the new claim is
    # the proof that the entry was discarded rather than served.
    assert new_id in ids
    assert ids == set(seed['claim_ids']) | {new_id}

    # ...and the refreshed answer is itself cached, with the pre-write copy gone.
    assert repo.related(subject_id=seed['subject_id'], predicate='has_ceo',
                        exclude_id='', limit=10) == after
    stats = related_cache_stats()
    assert stats['misses'] >= 2      # warm-up + post-write recompute
    assert stats['size'] >= 1


def test_cached_rows_are_returned_as_copies(tmp_path):
    seed = _seed(tmp_path, [('Apple', 'has_ceo', 'Tim')])
    from app.repositories.claim_repo import ClaimRepository

    repo = ClaimRepository()
    first = repo.related(subject_id=seed['subject_id'], predicate='has_ceo',
                         exclude_id='', limit=10)
    # A caller annotating or editing a row must not reach into the shared entry.
    first[0]['object_text'] = 'MUTATED'
    first[0]['injected'] = True
    first.append({'id': 'ghost'})

    second = repo.related(subject_id=seed['subject_id'], predicate='has_ceo',
                          exclude_id='', limit=10)
    assert first is not second
    assert len(second) == 1
    assert second[0]['object_text'] == 'Tim'
    assert 'injected' not in second[0]
    assert [row['id'] for row in second] == seed['claim_ids']


def test_inputs_are_part_of_the_key(tmp_path):
    seed = _seed(tmp_path, [('Apple', 'has_ceo', 'Tim'),
                            ('Apple', 'has_ceo', 'Alice'),
                            ('Apple', 'founded_by', 'Steve')])
    from app.repositories.claim_repo import ClaimRepository

    repo = ClaimRepository()
    sid = seed['subject_id']

    ceo = repo.related(subject_id=sid, predicate='has_ceo', exclude_id='', limit=10)
    founder = repo.related(subject_id=sid, predicate='founded_by', exclude_id='', limit=10)

    # Different predicates must not bleed into each other.
    assert {row['predicate'] for row in ceo} == {'has_ceo'}
    assert {row['predicate'] for row in founder} == {'founded_by'}
    assert len(ceo) == 2 and len(founder) == 1

    # A different limit is a different entry.
    limited = repo.related(subject_id=sid, predicate='has_ceo', exclude_id='', limit=1)
    assert len(limited) == 1

    # ...and so is a different exclusion.
    tim_id = seed['claim_ids'][0]
    excluded = repo.related(subject_id=sid, predicate='has_ceo',
                            exclude_id=tim_id, limit=10)
    assert {row['id'] for row in excluded} == {seed['claim_ids'][1]}


def test_transaction_bound_reads_bypass_the_cache(tmp_path):
    """A read inside a caller's transaction is never cached.

    That transaction can roll back, and a rollback does not move the data epoch —
    so a cached entry from uncommitted rows could survive as a phantom. The cache
    only serves the standalone (own-connection) path, which is what every hot
    caller uses.
    """
    seed = _seed(tmp_path, [('Apple', 'has_ceo', 'Tim')])
    from app.db import transaction
    from app.repositories.claim_repo import ClaimRepository, related_cache_stats

    before = related_cache_stats()
    with transaction() as conn:
        inside = ClaimRepository(conn).related(subject_id=seed['subject_id'],
                                               predicate='has_ceo', exclude_id='', limit=10)
    after = related_cache_stats()

    assert [row['id'] for row in inside] == seed['claim_ids']
    # Nothing was admitted to the cache by the transaction-bound read.
    assert after['hits'] == before['hits']
    assert after['misses'] == before['misses']
    assert after['size'] == before['size'] == 0
