"""Context Runtime P3: Context Cache + version-bound invalidation (spec §10).

The cache key hashes every version the compiled prompt depends on - prompt
content, skill/reference versions, registry/schema files, history state, the
packet - so any change produces a new key and the old entry is invalid by
construction. One row per key: rows = misses, sum(hits) = hits, hence the
observable hit rate.
"""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD_MODULES = (
    'app.ontology',
    'app.skills',
    'app.context.tokens',
    'app.context.policies',
    'app.context.items',
    'app.context.value',
    'app.context.manager',
    'app.context.planner',
    'app.context.packet',
    'app.context.cache',
    'app.context.history',
    'app.context.providers.base',
    'app.context.providers.skills',
    'app.context.providers.tools',
    'app.context.providers.history',
    'app.context.providers',
    'app.context.compiler',
    'app.context',
    'app.runtime.task',
    'app.prompt_profiles',
)


def _reload(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'wiki.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config
    import app.db
    importlib.reload(app.config)
    importlib.reload(app.db)
    for name in _RELOAD_MODULES:
        importlib.reload(importlib.import_module(name))
    app.db.init_db()


def test_identical_inputs_replay_the_cached_prompt(tmp_path):
    _reload(tmp_path)
    from app.context.cache import cache_stats
    from app.prompt_profiles import build_context

    first = build_context('personal', custom='简洁回答', persist=False)
    second = build_context('personal', custom='简洁回答', persist=False)

    assert first.render() == second.render()
    assert second.items == []                            # replay, not recompiled
    stats = cache_stats()
    assert stats['entries'] == 1 and stats['hits'] == 1
    assert stats['hit_rate'] == 0.5


def test_changing_the_custom_prompt_invalidates(tmp_path):
    _reload(tmp_path)
    from app.context.cache import cache_stats
    from app.prompt_profiles import build_context

    build_context('personal', custom='版本一', persist=False)
    build_context('personal', custom='版本二', persist=False)

    stats = cache_stats()
    assert stats['entries'] == 2 and stats['hits'] == 0  # new key, no reuse


def test_changing_history_state_misses(tmp_path):
    _reload(tmp_path)
    from app.context.cache import cache_stats
    from app.prompt_profiles import build_context

    build_context('personal', custom='X', persist=False)
    build_context('personal', custom='X', persist=False,
                  history=[{'role': 'user', 'content': '上一轮的问题'}])

    stats = cache_stats()
    assert stats['entries'] == 2 and stats['hits'] == 0


def test_registry_version_change_invalidates_by_construction(tmp_path):
    _reload(tmp_path)
    import app.context.cache as cache_module
    from app.context.cache import cache_stats
    from app.prompt_profiles import build_context

    build_context('personal', custom='X', persist=False)
    original = cache_module.registry_version
    cache_module.registry_version = lambda: 'changed-registry'
    try:
        build_context('personal', custom='X', persist=False)
    finally:
        cache_module.registry_version = original

    stats = cache_stats()
    assert stats['entries'] == 2 and stats['hits'] == 0  # vocab edit -> new key


def test_cache_disabled_writes_nothing(tmp_path):
    _reload(tmp_path)
    from app.context.cache import cache_stats
    from app.prompt_profiles import build_context

    build_context('personal', custom='X', persist=False, use_cache=False)
    build_context('personal', custom='X', persist=False, use_cache=False)

    assert cache_stats() == {'entries': 0, 'hits': 0, 'misses': 0, 'hit_rate': 0.0}


def test_extraction_role_compose_uses_the_cache(tmp_path):
    _reload(tmp_path)
    from app.context.cache import cache_stats
    from app.prompt_profiles import compose_prompt

    first = compose_prompt('extractor')
    before = cache_stats()
    second = compose_prompt('extractor')

    assert first == second
    # The replay adds hits and, crucially, no new entries: both the composed
    # prompt and the profile metadata path replay their cached ledgers.
    after = cache_stats()
    assert after['hits'] > before['hits']
    assert after['entries'] == before['entries']
