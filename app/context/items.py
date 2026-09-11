"""The single unit of context: `ContextItem`.

Everything that could enter an LLM call is represented as a `ContextItem`, so the
planner can rank it, the compiler can drop/defer it, and the trace can explain it.
An item carries both its payload (`content`) and the signals used to decide
whether the payload is worth its tokens (relevance, information gain, freshness,
evidence strength, task priority).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .policies import Load
from .tokens import estimate_tokens

# Item types used across the runtime. Keep the vocabulary closed so the UI and the
# planner templates can rely on it.
TYPE_BOOTSTRAP = 'bootstrap'
TYPE_TASK = 'task'
TYPE_SKILL = 'skill'
TYPE_REFERENCE = 'reference'
TYPE_TOOLS = 'tools'
TYPE_REGISTRY = 'registry'
TYPE_KNOWLEDGE = 'knowledge'
TYPE_EVIDENCE = 'evidence'
TYPE_HISTORY = 'history'
TYPE_EXAMPLES = 'examples'
TYPE_CONSTRAINTS = 'constraints'
TYPE_CUSTOM = 'custom'
TYPE_OUTPUT_CONTRACT = 'output_contract'

# Types that carry the task itself; the compiler never drops these.
CORE_TYPES = frozenset({TYPE_BOOTSTRAP, TYPE_TASK, TYPE_CONSTRAINTS, TYPE_CUSTOM, TYPE_OUTPUT_CONTRACT})


@dataclass
class ContextItem:
    """One candidate piece of context, with the signals used to value it."""

    id: str
    type: str
    content: str = ''
    source: str = ''

    priority: float = 0.5
    relevance: float = 0.5
    information_gain: float = 0.5
    evidence_strength: float = 0.5
    freshness: float = 1.0

    load_policy: Load = Load.LOAD
    required: bool = False

    version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    estimated_tokens: int = 0
    original_tokens: int = 0
    loaded: bool = True
    reason: str = ''

    def __post_init__(self) -> None:
        self.estimated_tokens = estimate_tokens(self.content)
        self.original_tokens = self.original_tokens or self.estimated_tokens
        self.loaded = self.load_policy is Load.LOAD and bool(self.content)

    # Kept so callers that speak in terms of "section name/policy" keep working.
    @property
    def name(self) -> str:
        return self.type

    @property
    def policy(self) -> Load:
        return self.load_policy

    @property
    def trimmed(self) -> bool:
        return self.saved_tokens > 0

    @property
    def chars(self) -> int:
        return len(self.content)

    @property
    def saved_tokens(self) -> int:
        return max(0, self.original_tokens - self.estimated_tokens)

    @property
    def tokens(self) -> int:
        return self.estimated_tokens

    def set_content(self, content: str) -> None:
        """Replace the payload (used by the compressor when trimming)."""
        self.content = content
        self.estimated_tokens = estimate_tokens(content)
        self.loaded = bool(content)

    def to_trace(self) -> dict:
        """Serialisable description used by Context Trace and the inspector."""
        return {
            'id': self.id,
            'name': self.type,
            'type': self.type,
            'policy': self.load_policy.value,
            'source': self.source,
            'reason': self.reason,
            'tokens': self.estimated_tokens,
            'chars': self.chars,
            'loaded': self.loaded,
            'required': self.required,
            'priority': round(self.priority, 4),
            'relevance': round(self.relevance, 4),
            'trimmed': self.saved_tokens > 0,
            'version': self.version,
        }


# Backwards-compatible alias: earlier revisions called this object a Section.
Section = ContextItem
