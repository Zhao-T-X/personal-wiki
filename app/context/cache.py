"""Context Cache (Context Runtime P3, spec §10).

A compiled context is a pure function of its inputs - role, custom prompt,
task type, references, tools, conversation state, packet - *and* of the file
versions those inputs are compiled from (SKILL.md, its references, the
predicate registries, the extraction schema). The cache key therefore hashes
all of them together:

- any version change (edit a registry JSON, a SKILL.md, the custom prompt)
  produces a different key -> the old entry is invalid *by construction*;
- one row per distinct key means `rows = misses`, `sum(hits) = hits`, so the
  hit rate is observable with no separate counter (spec: "缓存命中率可观测").

Cached is only the *rendered prompt*; the Context Trace of the compiling call
stays the single audit record for that content - a cache hit replays exactly
the content that was already traced.
"""
from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

from ..db import connect
from ..ontology import registry_version

# Bump when the cached payload shape changes, so stale rows are never replayed.
CACHE_SCHEMA = 'context-cache-v1'


def context_cache_key(*, role: str, task_type: str, references: list[str] | None,
                      tools: list | None, custom: str, history: list[dict] | None,
                      history_summary: str | None, packet: Any,
                      skill: str | None, include_reference: bool = True) -> str:
    """Composite version-bound cache key (spec §10: 任一版本变化 → 自动失效)."""
    from ..skills import reference_version, skill_version

    parts: list[Any] = [
        CACHE_SCHEMA,
        role, task_type, include_reference,
        sorted(references or []),
        [getattr(fn, '__name__', str(fn)) for fn in (tools or [])],
        custom or '',
        history_summary,
        history or [],
        packet.to_dict() if hasattr(packet, 'to_dict') else packet,
        registry_version(),
    ]
    if skill:
        parts.append(skill_version(skill))
        parts.extend(reference_version(skill, r) for r in sorted(references or []))
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode('utf-8')).hexdigest()


def cache_get(key: str) -> dict | None:
    """The cached prompt payload, or None. Records the hit when present."""
    if not key:
        return None
    try:
        conn = connect()
        row = conn.execute('SELECT prompt_json FROM context_cache WHERE cache_key=?',
                           (key,)).fetchone()
        if row is None:
            conn.close()
            return None
        conn.execute("UPDATE context_cache SET hits=hits+1, last_hit_at=CURRENT_TIMESTAMP "
                     'WHERE cache_key=?', (key,))
        conn.commit()
        conn.close()
        return json.loads(row['prompt_json'])
    except Exception as exc:
        print(f'[context.cache] read failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        return None


def cache_put(key: str, agent: str, payload: dict) -> None:
    """Store a compiled prompt (best effort - the cache must never break a call)."""
    try:
        conn = connect()
        conn.execute(
            '''INSERT INTO context_cache(cache_key,agent,prompt_json) VALUES(?,?,?)
               ON CONFLICT(cache_key) DO NOTHING''',
            (key, agent, json.dumps(payload, ensure_ascii=False)))
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f'[context.cache] write failed: {type(exc).__name__}: {exc}', file=sys.stderr)


def cache_stats() -> dict:
    """Hit-rate observability (spec §13: 缓存命中率可观测)."""
    try:
        conn = connect()
        row = conn.execute('SELECT COUNT(*) entries, COALESCE(SUM(hits),0) hits '
                           'FROM context_cache').fetchone()
        conn.close()
        entries, hits = row['entries'], row['hits']
        misses = entries
    except Exception as exc:
        print(f'[context.cache] stats failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        entries = hits = misses = 0
    total = hits + misses
    return {'entries': entries, 'hits': hits, 'misses': misses,
            'hit_rate': round(hits / total, 4) if total else 0.0}
