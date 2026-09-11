"""Context Provider interface.

A provider turns part of the runtime state into candidate `ContextItem`s. It does
not decide what finally reaches the model: the planner sets section budgets and
the compiler ranks, dedups and trims. Providers only have to be honest about what
they offer (source, cost, relevance) and about what they deliberately withhold.
"""
from __future__ import annotations

import sys
from typing import Protocol, runtime_checkable

from ..items import ContextItem
from ..planner import ContextPlan
from ...runtime.task import TaskContext


@runtime_checkable
class ContextProvider(Protocol):
    """One source of context candidates."""

    name: str

    def provide(self, task: TaskContext, plan: ContextPlan) -> list[ContextItem]:
        """Return candidate items for this task. Must not raise on empty state."""
        ...


class ProviderRegistry:
    """Ordered registry so the compiler only asks for what a plan declares."""

    def __init__(self) -> None:
        self._providers: dict[str, ContextProvider] = {}

    def register(self, provider: ContextProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> ContextProvider | None:
        return self._providers.get(name)

    def names(self) -> list[str]:
        return sorted(self._providers)

    def collect(self, task: TaskContext, plan: ContextPlan) -> list[ContextItem]:
        """Run each provider a plan declares, exactly once, in plan order.

        A provider may serve several sections (the skill provider supplies both the
        contract and its references), so sections are collapsed by provider name -
        otherwise the same candidates would be collected once per section.
        """
        items: list[ContextItem] = []
        seen: set[str] = set()
        for section in plan.sections:
            if section.provider in seen:
                continue
            seen.add(section.provider)
            provider = self._providers.get(section.provider)
            if provider is None:
                continue
            try:
                items.extend(provider.provide(task, plan))
            except Exception as exc:  # a broken provider must not break the LLM call
                print(f'[context.provider] {section.provider} failed: '
                      f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return items
