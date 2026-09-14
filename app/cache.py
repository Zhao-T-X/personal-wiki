"""Process-local caches guarded by a data epoch.

A cache is only as good as its invalidation. Rather than sprinkle TTLs and
best-effort evictions around the codebase, every write in the persistence layer
bumps **one monotonic counter**; a cache entry remembers the epoch it was
produced under and is discarded the moment the counter moves. Any write therefore
invalidates every derived cache by construction, and no call site has to remember
to do it.

    Repository.write()  ->  bump_data_epoch()
                                 |
    EpochCache.get(key)  ->  entry.epoch == data_epoch() ? value : MISS

Scope: **process-local.** The application runs as a single process, which this
fully covers. A multi-process deployment would need a shared epoch (a row in
SQLite, say); that is called out rather than papered over with a TTL — a TTL
would be wrong in the other direction, serving stale knowledge that a write
already superseded.

Pure logic: standard library only, no database, no framework, no LLM.
"""
from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from typing import Any, Callable, Hashable

_MISS = object()

_epoch = 0
_epoch_lock = RLock()


def data_epoch() -> int:
    """The current epoch. Every successful (or attempted) write moves it."""
    with _epoch_lock:
        return _epoch


def bump_data_epoch() -> int:
    """Invalidate everything derived from the database. Called by Repository.write."""
    global _epoch
    with _epoch_lock:
        _epoch += 1
        return _epoch


class EpochCache:
    """A bounded mapping whose entries expire when the data changes.

    Least-recently-used eviction keeps memory bounded; epoch checking keeps it
    *correct*. Both are cheap and both matter, so neither is optional.
    """

    def __init__(self, maxsize: int = 512, name: str = 'cache') -> None:
        self.maxsize = max(1, int(maxsize))
        self.name = name
        self._entries: OrderedDict[Hashable, tuple[int, Any]] = OrderedDict()
        self._lock = RLock()
        self._hits = 0
        self._misses = 0

    def get(self, key: Hashable, default: Any = None) -> Any:
        epoch = data_epoch()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return default
            stored_epoch, value = entry
            if stored_epoch != epoch:
                # The data moved underneath us: this entry describes a world that
                # no longer exists.
                del self._entries[key]
                self._misses += 1
                return default
            self._entries.move_to_end(key)
            self._hits += 1
            return value

    def put(self, key: Hashable, value: Any) -> None:
        epoch = data_epoch()
        with self._lock:
            self._entries[key] = (epoch, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self.maxsize:
                self._entries.popitem(last=False)

    def get_or_compute(self, key: Hashable, factory: Callable[[], Any]) -> Any:
        """Return the cached value, computing and storing it on a miss.

        ``factory`` runs outside the lock, so a slow computation cannot serialise
        every other reader; a redundant computation on a race is harmless.
        """
        value = self.get(key, _MISS)
        if value is not _MISS:
            return value
        value = factory()
        self.put(key, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict:
        with self._lock:
            return {'name': self.name, 'size': len(self._entries), 'maxsize': self.maxsize,
                    'hits': self._hits, 'misses': self._misses, 'epoch': data_epoch()}
