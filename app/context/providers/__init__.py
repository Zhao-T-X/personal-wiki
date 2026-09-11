"""Context providers.

Shipped with a real consumer:
- `skills` / `tools` - the agent system prompt (Phase 1),
- `evidence` / `knowledge` - the grounded answer path (Phase 2).

The `history` provider compresses conversation transcripts (Phase 2c;
persistence arrives Phase 3). The `registry` subset matcher lives in
app/ontology.py and feeds the extraction repair loop.
"""
from .base import ContextProvider, ProviderRegistry
from .evidence import (EvidenceHit, EvidenceProvider, apply_escalation, conflicting_hits,
                       dedup_hits, evidence_items, render)
from .history import HistoryProvider
from .knowledge import EntitySummary, KnowledgeProvider, knowledge_items
from .skills import SkillProvider
from .tools import ToolProvider

render_evidence = render

__all__ = [
    'ContextProvider', 'ProviderRegistry',
    'SkillProvider', 'ToolProvider', 'HistoryProvider',
    'EvidenceHit', 'EvidenceProvider', 'evidence_items', 'apply_escalation', 'dedup_hits',
    'conflicting_hits', 'render', 'render_evidence',
    'EntitySummary', 'KnowledgeProvider', 'knowledge_items',
]
