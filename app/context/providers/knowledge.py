"""Knowledge provider: summary-first, never the whole object.

Default knowledge context is a one-line summary per entity involved in the
evidence (name, types, a short description). A full entity profile - claims,
relations, events, all evidence - is what the agent can pull by id when it
actually needs to verify something, which is why it is not loaded here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..items import TYPE_KNOWLEDGE, ContextItem
from ..planner import ContextPlan
from ...runtime.task import TaskContext

SUMMARY_CHARS = 140
DEFAULT_ENTITY_LIMIT = 3


@dataclass
class EntitySummary:
    """The compact form of an entity that is allowed into a context."""

    name: str
    types: list[str] = field(default_factory=list)
    summary: str = ''
    status: str = ''

    @classmethod
    def from_dict(cls, data: dict) -> 'EntitySummary':
        types = data.get('types')
        if isinstance(types, str):
            types = [types]
        if not types and data.get('type'):
            types = [data['type']]
        return cls(
            name=str(data.get('name') or ''),
            types=[str(t) for t in (types or [])],
            summary=str(data.get('summary') or data.get('description') or ''),
            status=str(data.get('status') or ''),
        )

    def line(self) -> str:
        label = '/'.join(self.types) if self.types else 'Entity'
        text = ' '.join(self.summary.split())[:SUMMARY_CHARS]
        suffix = f' — {text}' if text else ''
        return f'- {self.name} [{label}]{suffix}'.rstrip()


def render_summaries(summaries: list[EntitySummary]) -> str:
    return '\n'.join(s.line() for s in summaries if s.name)


def knowledge_items(summaries: list[EntitySummary]) -> list[ContextItem]:
    """One compact knowledge item; empty when there is nothing worth adding."""
    content = render_summaries(summaries)
    if not content:
        return []
    return [ContextItem(
        id='knowledge:summaries',
        type=TYPE_KNOWLEDGE,
        content=content,
        source='knowledge_store.entity_summaries',
        priority=0.7,
        relevance=0.8,
        information_gain=0.6,
        evidence_strength=0.6,
        required=False,
        metadata={'entities': [s.name for s in summaries]},
        reason=f'summary-first: {len(summaries)} entity summaries instead of full profiles',
    )]


class KnowledgeProvider:
    """Serves pre-collected entity summaries (the caller owns the query)."""

    name = 'knowledge'

    def __init__(self, summaries: list[EntitySummary] | None = None):
        self.summaries = list(summaries or [])

    def provide(self, task: TaskContext, plan: ContextPlan) -> list[ContextItem]:
        if not self.summaries:
            return []
        return knowledge_items(self.summaries[:DEFAULT_ENTITY_LIMIT])
