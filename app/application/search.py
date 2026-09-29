"""Search Application — every retrieval, the single source QA / Research read from.

The routes used to call ``retrieval`` directly. That put the retrieval surface one layer
below the adapter, which is fine for a single call but meant no shared owner and no
place to enforce the SearchFacade red line (retrieval must not derive current knowledge
itself — see ``docs/architecture/PUBLIC-ENTRYPOINTS.md``). Centralising the two search
entry points here gives Search a home and keeps the route handler to validation + call.
"""
from __future__ import annotations

from ..retrieval import search as _search, search_knowledge as _search_knowledge


def search(q: str, limit: int = 10, semantic: bool = True):
    """Flat chunk search — the shape the knowledge space and command palette read."""
    if not q.strip():
        return []
    return _search(q, max(1, min(limit, 50)), semantic=semantic)


def search_knowledge(q: str, limit: int = 8, semantic: bool = True):
    """Knowledge-first search: the product view over the same recall as ``search``."""
    if not q.strip():
        return {'knowledge': [], 'results': []}
    return _search_knowledge(q, max(1, min(limit, 50)), semantic=semantic)
