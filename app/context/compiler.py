"""Context Compiler: dedup, rank, allocate, compress, compile.

It is the only place that decides what finally reaches the model. The manager's
budget pass is reused for the final global trim so there is exactly one trimming
implementation in the codebase.

Two different cost numbers are reported, and they must not be confused:

``waste``        share of the loaded context above the plan's target budget -
                 "we spent more than we planned to".
``saved_tokens`` tokens we did not spend because the item was lazy, never-load,
                 or dropped/trimmed by the budget pass - "what the runtime
                 avoided loading".
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .items import ContextItem, CORE_TYPES
from .manager import ContextManager
from .planner import CONTEXT_ORDER, SYSTEM_ORDER, ContextPlan
from .policies import Load
from .tokens import estimate_tokens
from .value import rank, utility, utility_per_1k


# `render()` emits only the system and context blocks, and each block is built from a
# fixed order of item types. An item whose type is in NEITHER order is counted by the
# ledger yet never reaches the model — that is what happened to `reference` (see
# REFERENCE_NOT_RENDERED in tests/fixtures/extraction_cases/). Keeping the two numbers
# apart is what stops a cost report from banking tokens that were never spent.
RENDERED_TYPES: tuple[str, ...] = tuple(SYSTEM_ORDER) + tuple(CONTEXT_ORDER)


@dataclass
class CompiledContext:
    """The compiled result of one plan: messages plus the ledger behind them."""

    agent: str
    plan: ContextPlan
    items: list[ContextItem] = field(default_factory=list)
    withheld: list[dict] = field(default_factory=list)
    optimizations: list[str] = field(default_factory=list)
    system: str = ''
    context: str = ''
    user: str | None = None
    # Cache replay (P3): plan is None and the ledger is the one recorded when
    # this exact content was first compiled - identical content, same ledger.
    cached_trace: dict | None = None

    @property
    def total_tokens(self) -> int:
        return sum(i.estimated_tokens for i in self.items)

    @property
    def trimmed_tokens(self) -> int:
        return sum(i.saved_tokens for i in self.items)

    @property
    def saved_tokens(self) -> int:
        return sum(w.get('tokens', 0) for w in self.withheld) + self.trimmed_tokens

    @property
    def rendered_tokens(self) -> int:
        """Tokens that actually reach the model, as opposed to what the ledger holds."""
        return sum(i.estimated_tokens for i in self.items if i.type in RENDERED_TYPES)

    @property
    def not_rendered(self) -> list[dict]:
        """Loaded items that ``render()`` drops, itemised.

        They still count against the budget (the planner reserved room for them), but they
        are not in the request — so a *sent* token total must exclude them.
        """
        return [{'type': i.type, 'source': i.source, 'tokens': i.estimated_tokens}
                for i in self.items if i.content.strip() and i.type not in RENDERED_TYPES]

    @property
    def required_tokens(self) -> int:
        return sum(i.estimated_tokens for i in self.items if i.required)

    @property
    def efficiency(self) -> float:
        """Share of the loaded context that directly carries the task."""
        if not self.total_tokens:
            return 1.0
        return round(self.required_tokens / self.total_tokens, 4)

    @property
    def waste(self) -> float:
        """Share of the loaded context spent beyond the plan's target budget."""
        if not self.total_tokens:
            return 0.0
        over = max(0, self.total_tokens - self.plan.target_budget)
        return round(over / self.total_tokens, 4)

    @property
    def over_budget(self) -> bool:
        return self.total_tokens > self.plan.hard_budget

    def render(self) -> str:
        parts = [b for b in (self.system, self.context, self.user) if b]
        return '\n'.join(parts)

    def to_trace(self) -> dict:
        """Context Trace payload (same shape the manager produces)."""
        if self.plan is None:
            if self.cached_trace is not None:
                return {**self.cached_trace, 'cached': True}
            raise ValueError('no plan and no cached ledger - rebuild the context')
        return {
            'agent': self.agent,
            'budget_tokens': self.plan.hard_budget,
            'actual_tokens': self.total_tokens,
            # What the model actually receives. `actual_tokens` is the ledger (budget
            # accounting); the two differ whenever an item is loaded but not rendered.
            'rendered_tokens': self.rendered_tokens,
            'not_rendered': self.not_rendered,
            'trimmed_tokens': self.trimmed_tokens,
            'saved_tokens': self.saved_tokens,
            'efficiency': self.efficiency,
            'waste': self.waste,
            'over_budget': self.over_budget,
            'reasoning_level': self.plan.reasoning_level,
            'target_budget': self.plan.target_budget,
            'optimizations': list(self.optimizations),
            'components': {
                'system': estimate_tokens(self.system),
                'context': estimate_tokens(self.context),
                'user': estimate_tokens(self.user or ''),
            },
            'sections': [i.to_trace() for i in self.items],
            'withheld': list(self.withheld),
        }


def _fingerprint(content: str) -> str:
    normalised = ' '.join(content.split())
    return hashlib.sha1(normalised.encode('utf-8')).hexdigest()


class ContextCompiler:
    """Compiles candidate items into the final messages for one LLM call."""

    def compile(self, items: list[ContextItem], plan: ContextPlan, *,
                user_message: str | None = None) -> CompiledContext:
        compiled = CompiledContext(agent=plan.agent, plan=plan, user=user_message)

        # 1. never_load and retrieve_later never reach the model.
        for item in items:
            if item.load_policy is Load.NEVER_LOAD:
                compiled.withheld.append(self._withheld(item, 'never_load'))
            elif item.load_policy is Load.RETRIEVE_LATER:
                compiled.withheld.append(self._withheld(item, 'retrieve_later'))
            elif not item.content.strip():
                continue
            else:
                compiled.items.append(item)

        # 2. dedup identical payloads, keeping the higher-value copy.
        compiled.items = self._dedup(compiled.items, compiled)

        # 3. per-section budget: required first, then best value per token.
        compiled.items = self._allocate(compiled)

        # 4. global trim through the single budget implementation.
        manager = ContextManager(plan.agent, budget=plan.hard_budget)
        for item in compiled.items:
            manager.adopt(item)
        context = manager.build()
        compiled.items = context.sections
        compiled.optimizations.extend(context.optimizations)

        # 5. compile the messages.
        compiled.system = self._render(compiled.items, SYSTEM_ORDER)
        compiled.context = self._render(compiled.items, CONTEXT_ORDER)
        return compiled

    # ------------------------------------------------------------------ steps
    def _allocate(self, compiled: CompiledContext) -> list[ContextItem]:
        by_type: dict[str, list[ContextItem]] = {}
        for item in compiled.items:
            by_type.setdefault(item.type, []).append(item)

        kept: list[ContextItem] = []
        for type_, group in by_type.items():
            budget = compiled.plan.budget_for(type_)
            if type_ in CORE_TYPES or budget <= 0:
                kept.extend(group)          # core sections are never dropped by allocation
                continue
            used = 0
            for item in rank(group):
                if item.required or used + item.estimated_tokens <= budget:
                    kept.append(item)
                    used += item.estimated_tokens
                else:
                    compiled.withheld.append(self._withheld(item, 'section_budget'))

        order = {t: i for i, t in enumerate(SYSTEM_ORDER + CONTEXT_ORDER)}
        return sorted(kept, key=lambda i: (order.get(i.type, len(order)), -utility(i)))

    def _dedup(self, items: list[ContextItem], compiled: CompiledContext) -> list[ContextItem]:
        best: dict[str, ContextItem] = {}
        for item in items:
            key = _fingerprint(item.content)
            current = best.get(key)
            if current is None or utility(item) > utility(current):
                if current is not None:
                    compiled.withheld.append(self._withheld(current, 'duplicate'))
                best[key] = item
            else:
                compiled.withheld.append(self._withheld(item, 'duplicate'))
        return list(best.values())

    def _render(self, items: list[ContextItem], order: tuple[str, ...]) -> str:
        index = {t: i for i, t in enumerate(order)}
        selected = [i for i in items if i.type in index]
        selected.sort(key=lambda i: index[i.type])
        return '\n'.join(i.content for i in selected if i.content)

    def _withheld(self, item: ContextItem, reason: str) -> dict:
        # A lazy item carries no payload, so report the cost it *would* have had.
        tokens = int(item.metadata.get('estimated_tokens') or item.estimated_tokens or 0)
        return {
            'id': item.id,
            'name': item.type,
            'type': item.type,
            'tokens': tokens,
            'saved_tokens': tokens,
            'utility_per_1k': utility_per_1k(item),
            'source': item.source,
            'reason': reason,
        }
