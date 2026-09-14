"""Cache primitives: the data epoch and the ontology cache (Phase 6 §32/§33).

A cache is only as good as its invalidation, so the invalidation is what is
tested here: an entry must die the moment the data changes, and it must survive
when nothing changed. The ontology fingerprint gets the same treatment — it must
notice a real edit and must not re-read the registry on every call.
"""
from __future__ import annotations

import importlib
import json
import os

from app.cache import EpochCache, bump_data_epoch, data_epoch


# --- the epoch cache ----------------------------------------------------------

def test_entry_survives_while_the_data_stands_still():
    cache = EpochCache(maxsize=4, name='test')
    cache.put(('a', 1), [1, 2])
    assert cache.get(('a', 1)) == [1, 2]
    assert cache.stats()['hits'] == 1


def test_entry_dies_the_moment_the_data_moves():
    cache = EpochCache(maxsize=4, name='test')
    cache.put('k', 'v')
    assert cache.get('k') == 'v'
    bump_data_epoch()
    assert cache.get('k') is None            # invalidated, not merely stale
    assert cache.stats()['size'] == 0        # and actually evicted


def test_get_or_compute_computes_once_unless_data_changes():
    cache = EpochCache(maxsize=4, name='test')
    calls = []

    def factory():
        calls.append(1)
        return len(calls)

    assert cache.get_or_compute('k', factory) == 1
    assert cache.get_or_compute('k', factory) == 1
    assert len(calls) == 1

    bump_data_epoch()
    assert cache.get_or_compute('k', factory) == 2
    assert len(calls) == 2


def test_cache_is_bounded():
    cache = EpochCache(maxsize=2, name='test')
    for key in ('a', 'b', 'c'):
        cache.put(key, key)
    assert cache.stats()['size'] == 2
    assert cache.get('a') is None            # least recently used evicted


# --- the epoch is driven by writes, not by reads ------------------------------

def _reload(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config)
    importlib.reload(db)
    for name in ('app.repositories.base', 'app.repositories.document_repo',
                 'app.repositories.entity_repo', 'app.repositories.claim_repo'):
        module = __import__(name, fromlist=['x'])
        importlib.reload(module)
    db.init_db()
    return db


def test_a_write_bumps_the_epoch_and_a_read_does_not(tmp_path):
    _reload(tmp_path)
    from app.repositories.document_repo import DocumentRepository
    from app.repositories.entity_repo import EntityRepository

    before = data_epoch()
    EntityRepository().resolution_candidates(5)          # read
    assert data_epoch() == before, 'a read must never invalidate a cache'

    DocumentRepository().create(title='T', content='body')   # write
    assert data_epoch() > before, 'a write must invalidate derived caches'


# --- the ontology cache -------------------------------------------------------

def test_ontology_snapshot_reports_the_loaded_registry():
    from app.ontology import claim_registry_version, ontology_snapshot

    snapshot = ontology_snapshot()
    assert snapshot['claim_registry_version'] == claim_registry_version()
    assert snapshot['claim_predicates'] > 0
    assert 'has_ceo' in snapshot['write_surface'] or snapshot['write_surface']


def test_registry_version_is_cached_but_still_notices_an_edit(tmp_path, monkeypatch):
    import app.ontology as ontology

    schema_dir = tmp_path / 'schemas'
    schema_dir.mkdir()
    target = schema_dir / 'a-registry.json'
    target.write_text(json.dumps({'version': '1', 'x': 'aaa'}), encoding='utf-8')
    monkeypatch.setattr(ontology, '_schema_paths',
                        lambda: sorted(schema_dir.glob('*.json')))

    first = ontology.registry_version()
    assert ontology.registry_version() == first        # cached, same answer

    # Edit the file: the fingerprint is a stat, so the change is seen immediately
    # without re-reading every JSON on every call.
    target.write_text(json.dumps({'version': '1', 'x': 'bbbbbb'}), encoding='utf-8')
    assert ontology.registry_version() != first
