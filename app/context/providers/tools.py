"""Tool provider: a declarative catalogue with capability matching.

The authoritative tool schemas are sent by the execution runtime (AgentScope), not
by this provider - the catalogue here exists so the runtime can (a) tell the model
which capabilities it has in one compact block and (b) account for the cost of each
tool's schema in the Context Trace.

Capability matching keeps a task from seeing tools it cannot use: an extraction
task gets the skill tools, a curation task gets the entity/claim tools.
"""
from __future__ import annotations

from ..items import TYPE_TOOLS, ContextItem
from ..planner import ContextPlan
from ...runtime.task import TaskContext, INTENT_CURATE, INTENT_EXTRACT, INTENT_REVIEW

# name -> purpose, capabilities, estimated schema cost in tokens.
TOOL_CATALOG: dict[str, dict] = {
    'search_knowledge': {
        'purpose': 'hybrid retrieval (FTS5 + semantic + RRF) over documents',
        'capabilities': ['knowledge', 'retrieval', 'evidence'],
        'estimated_tokens': 70,
    },
    'get_entities': {
        'purpose': 'read several entities as compact summary cards in one call',
        'capabilities': ['knowledge', 'entity'],
        'estimated_tokens': 75,
    },
    'get_entity': {
        'purpose': 'read one entity with its claims and aliases',
        'capabilities': ['knowledge', 'entity'],
        'estimated_tokens': 65,
    },
    'get_entity_graph': {
        'purpose': 'one-hop neighbours of an entity',
        'capabilities': ['knowledge', 'graph'],
        'estimated_tokens': 60,
    },
    'list_skills': {
        'purpose': 'list the skills available to this agent',
        'capabilities': ['skill'],
        'estimated_tokens': 40,
    },
    'read_skill_reference': {
        'purpose': 'load one skill reference document on demand',
        'capabilities': ['skill'],
        'estimated_tokens': 45,
    },
}

# Which capabilities each intent is allowed to use.
_INTENT_CAPABILITIES = {
    INTENT_EXTRACT: {'skill'},
    INTENT_CURATE: {'skill', 'knowledge', 'entity', 'graph'},
    INTENT_REVIEW: {'skill', 'knowledge', 'entity', 'evidence'},
}


class ToolProvider:
    name = 'tools'

    def __init__(self, catalog: dict[str, dict] | None = None):
        self.catalog = catalog or TOOL_CATALOG

    def select(self, task: TaskContext) -> list[str]:
        """Capability match: only the tools this task can legitimately use."""
        allowed = _INTENT_CAPABILITIES.get(task.features.intent)
        if allowed is None:
            return sorted(self.catalog)
        return sorted(
            name for name, spec in self.catalog.items()
            if set(spec['capabilities']) & allowed
        )

    def provide(self, task: TaskContext, plan: ContextPlan) -> list[ContextItem]:
        names = self.select(task)
        if not names:
            return []
        lines = [f'- {name}: {self.catalog[name]["purpose"]}' for name in names]
        return [ContextItem(
            id='tools',
            type=TYPE_TOOLS,
            content='\n'.join(lines),
            source='context.providers.tools.TOOL_CATALOG',
            priority=0.8, relevance=0.9, information_gain=0.6, evidence_strength=0.5,
            required=False,
            metadata={
                'tools': names,
                'schema_tokens': sum(self.catalog[n]['estimated_tokens'] for n in names),
            },
            reason=f'capability match for intent={task.features.intent}',
        )]
