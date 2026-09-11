"""ContextManager: collect items and enforce the token budget.

Deliberately storage-agnostic - it never touches SQLite. Persistence is the job of
`app/context/trace.py`, so the manager stays testable and the compiler can reuse it
for the single global trim implementation.

Usage::

    cm = ContextManager('ExtractionAgent')
    cm.add('bootstrap', core_prompt, source='prompt_profiles.CORE_PROMPTS', required=True)
    cm.add('evidence', chunk_text, source='chunk:abc', summarizable=True)
    cm.add('document_ids', '', source='retrieval', id_only=True)
    context = cm.build()
    context.render()                    # effective text
    record_context(context)             # Context Trace -> SQLite

Design rules (see docs/superpowers/specs/2026-09-11-context-runtime-design.md):
- every item declares *why* it is loaded (`reason`) and where it comes from (`source`);
- `NEVER_LOAD` and `RETRIEVE_LATER` items never reach the prompt;
- compression follows `budget.COMPRESSION_ORDER`, and the task-carrying types in
  `budget.NEVER_TRIM` are never trimmed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import budget as budget_mod
from .items import ContextItem, Section
from .policies import Load, classify
from .tokens import estimate_tokens, truncate_to_tokens

# Below this share of its original size an item is dropped rather than trimmed.
MIN_KEEP_RATIO = 0.25


def _settings_overrides() -> dict[str, int]:
    """Per-agent budgets configured by the user (`agent_context_budgets`)."""
    try:
        from ..config import runtime
        value = runtime().get('agent_context_budgets')
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


@dataclass
class AgentContext:
    """The context the manager produced, before/after budget enforcement."""

    agent: str
    budget: int
    sections: list[ContextItem] = field(default_factory=list)
    optimizations: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(s.estimated_tokens for s in self.sections)

    @property
    def trimmed_tokens(self) -> int:
        return sum(s.saved_tokens for s in self.sections)

    @property
    def required_tokens(self) -> int:
        """Tokens carried by items that hold the task itself."""
        return sum(s.estimated_tokens for s in self.sections if s.type in budget_mod.NEVER_TRIM)

    @property
    def efficiency(self) -> float:
        """Share of the context that directly carries the task.

        A low value means most of the context is supporting material (knowledge,
        evidence, references, history); a *collapsing* value over time is the
        signal to move rules out of the prompt and into a skill.
        """
        if not self.total_tokens:
            return 1.0
        return round(self.required_tokens / self.total_tokens, 4)

    @property
    def over_budget(self) -> bool:
        return self.total_tokens > self.budget

    def render(self) -> str:
        """Render the effective context in item order."""
        return '\n'.join(s.content for s in self.sections if s.content)

    def by_name(self, name: str) -> ContextItem | None:
        for s in self.sections:
            if s.type == name:
                return s
        return None

    def trace(self) -> dict:
        """Alias of `to_trace()` kept for callers written before the rename."""
        return self.to_trace()

    def to_trace(self) -> dict:
        """Serialisable description of this context (Context Trace)."""
        return {
            'agent': self.agent,
            'budget_tokens': self.budget,
            'actual_tokens': self.total_tokens,
            'trimmed_tokens': self.trimmed_tokens,
            'efficiency': self.efficiency,
            'over_budget': self.over_budget,
            'optimizations': list(self.optimizations),
            'sections': [s.to_trace() | {'index': i} for i, s in enumerate(self.sections)],
        }


class ContextManager:
    """Collects context items and enforces the budget."""

    def __init__(self, agent_name: str, *, budget: int | None = None,
                 overrides: dict[str, int] | None = None):
        self.agent_name = agent_name
        if budget is not None:
            self.budget = budget
        else:
            self.budget = budget_mod.budget_for(agent_name, overrides or _settings_overrides())
        self._sections: list[ContextItem] = []

    # ------------------------------------------------------------------ add
    def add(self, name: str, content: str | None, *, source: str = '',
            reason: str = '', required: bool = False, summarizable: bool = False,
            id_only: bool = False, **kwargs) -> ContextItem | None:
        """Classify and record a candidate item.

        Returns the stored item, or ``None`` when it was rejected (``NEVER_LOAD``)
        or empty. ``RETRIEVE_LATER`` items are recorded without content so the
        trace still shows the intent.
        """
        text = content or ''
        tokens = estimate_tokens(text)
        policy = classify(name, tokens, required=required, summarizable=summarizable, id_only=id_only)

        if policy is Load.NEVER_LOAD:
            return None
        if policy is Load.RETRIEVE_LATER:
            item = ContextItem(id=f'{name}:lazy', type=name, content='', source=source,
                               load_policy=policy,
                               reason=reason or 'not needed now - fetch by id with a tool', **kwargs)
            return self.adopt(item)
        if not text.strip():
            return None
        item = ContextItem(
            id=f'{name}:{len(self._sections)}', type=name, content=text, source=source,
            load_policy=policy, required=required or name in budget_mod.NEVER_TRIM,
            reason=reason or ('required by the current task' if required else 'relevant supporting material'),
            **kwargs,
        )
        return self.adopt(item)

    def adopt(self, item: ContextItem) -> ContextItem:
        """Record an item that was produced elsewhere (e.g. by a provider)."""
        if item.original_tokens == 0:
            item.original_tokens = item.estimated_tokens
        item.loaded = item.load_policy is Load.LOAD and bool(item.content)
        self._sections.append(item)
        return item

    # -------------------------------------------------------------- measure
    def estimate_tokens(self) -> int:
        """Current token total before optimisation."""
        return sum(s.estimated_tokens for s in self._sections)

    # ------------------------------------------------------------- optimise
    def optimize(self, context: AgentContext) -> list[str]:
        """Compress items until the budget is met. Mutates ``context``."""
        actions: list[str] = []
        if context.total_tokens <= context.budget:
            return actions

        candidates = sorted(
            (s for s in context.sections if budget_mod.compressible(s.type) and s.content),
            key=lambda s: budget_mod.compression_rank(s.type),
        )
        for section in candidates:
            if context.total_tokens <= context.budget:
                break
            overflow = context.total_tokens - context.budget
            keep_tokens = max(0, section.estimated_tokens - overflow)
            # A section cut down to a fragment misleads more than it helps, so
            # anything below MIN_KEEP_RATIO is dropped outright.
            if keep_tokens < section.estimated_tokens * MIN_KEEP_RATIO:
                actions.append(f'drop {section.type} ({section.estimated_tokens} tokens, not needed for this budget)')
                section.set_content('')
                continue
            shortened = truncate_to_tokens(section.content, keep_tokens)
            before = section.estimated_tokens
            section.set_content(shortened)
            actions.append(f'trim {section.type} {before}->{section.estimated_tokens} tokens')

        if context.total_tokens > context.budget:
            actions.append(
                f'over budget: {context.total_tokens} > {context.budget} tokens with nothing left to compress')
        return actions

    # ---------------------------------------------------------------- build
    def build(self) -> AgentContext:
        """Apply the budget policy and return the effective context."""
        context = AgentContext(agent=self.agent_name, budget=self.budget, sections=list(self._sections))
        context.optimizations = self.optimize(context)
        return context


__all__ = ['AgentContext', 'ContextManager', 'Section', 'MIN_KEEP_RATIO']
