"""Context Planner: turn a task into an explicit plan before anything is loaded.

The plan answers, before a single token is spent:
- which sections may enter this context, and which provider supplies them,
- the budget for each section, and the hard / target / reserve envelope,
- the reasoning level the task is worth (adaptive reasoning),
- what stays lazy (references the agent can pull itself).

Phase 1 note: the templates below cover the context we build today - the agent
system prompt (bootstrap / skill / references / tools / custom / constraints).
Knowledge/evidence/history sections belong to the QA, research and extraction
payloads, which are Phase 2; adding them here without a consumer would be a
promise the runtime cannot keep yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .budget import NEVER_TRIM, budget_for
from .items import (TYPE_BOOTSTRAP, TYPE_CONSTRAINTS, TYPE_CUSTOM, TYPE_EVIDENCE, TYPE_EXAMPLES,
                    TYPE_EXTRACTION_CONTEXT, TYPE_HISTORY, TYPE_KNOWLEDGE, TYPE_REFERENCE,
                    TYPE_SKILL, TYPE_TASK, TYPE_TOOLS)
from .policies import Load
from ..runtime.task import TaskContext, TaskFeatures, reasoning_level, INTENT_EXTRACT

# Sections the compiler must not drop during allocation (they are the agent contract).
REQUIRED_TYPES = frozenset({TYPE_BOOTSTRAP, TYPE_SKILL, TYPE_CUSTOM, TYPE_CONSTRAINTS})

# Which provider supplies a section. 'core' means the caller supplies it directly
# (agent identity, user instructions, non-negotiable rules) - there is no provider
# to ask, and saying so keeps the plan honest in the Context Inspector.
_PROVIDER_BY_TYPE = {
    TYPE_SKILL: 'skills',
    TYPE_REFERENCE: 'skills',
    TYPE_TOOLS: 'tools',
    TYPE_KNOWLEDGE: 'knowledge',
    TYPE_EVIDENCE: 'evidence',
    TYPE_HISTORY: 'history',
}
CORE_PROVIDER = 'core'

# Budget shares for a grounded answer (ask / research): the evidence block is the
# payload that actually costs tokens, so it gets the largest slice - but not all
# of it, which is what forces the compiler to pick the most valuable hits.
_ANSWER_SHARES = {
    TYPE_KNOWLEDGE: 0.18,
    TYPE_EVIDENCE: 0.62,
    TYPE_HISTORY: 0.20,
}
ANSWER_TASK_TYPES = frozenset({'ask', 'research'})

# Sharing of the agent budget between sections of the system prompt.
_SYSTEM_SHARES = {
    TYPE_BOOTSTRAP: 0.10,
    TYPE_SKILL: 0.20,
    TYPE_REFERENCE: 0.35,
    # Deterministically prefetched context (Step 15). Only the extractor produces it, and
    # it replaces what would otherwise be a lazy reference fetch — so it is budgeted like
    # loaded content, not like a lazy pointer.
    TYPE_EXTRACTION_CONTEXT: 0.20,
    TYPE_TOOLS: 0.10,
    TYPE_CUSTOM: 0.15,
    TYPE_CONSTRAINTS: 0.05,
}


@dataclass
class SectionPlan:
    """One planned section: who provides it, how much it may cost, why it is here."""

    type: str
    provider: str
    budget: int = 0
    required: bool = False
    lazy: bool = False
    reason: str = ''

    def to_dict(self) -> dict:
        return {
            'type': self.type,
            'provider': self.provider,
            'budget': self.budget,
            'required': self.required,
            'lazy': self.lazy,
            'reason': self.reason,
        }


@dataclass
class ContextPlan:
    """The plan a compiler executes for one LLM call."""

    agent: str
    task_type: str
    reasoning_level: int
    hard_budget: int
    target_budget: int
    reserve_tokens: int
    sections: list[SectionPlan] = field(default_factory=list)
    features: TaskFeatures = field(default_factory=TaskFeatures)

    def section(self, type_: str) -> SectionPlan | None:
        for section in self.sections:
            if section.type == type_:
                return section
        return None

    def budget_for(self, type_: str) -> int:
        section = self.section(type_)
        return section.budget if section else 0

    @property
    def planned_tokens(self) -> int:
        return sum(s.budget for s in self.sections)

    def to_trace(self) -> dict:
        return {
            'agent': self.agent,
            'task_type': self.task_type,
            'reasoning_level': self.reasoning_level,
            'hard_budget': self.hard_budget,
            'target_budget': self.target_budget,
            'reserve_tokens': self.reserve_tokens,
            'features': self.features.to_dict(),
            'sections': [s.to_dict() for s in self.sections],
        }


class ContextPlanner:
    """Builds a `ContextPlan` from a `TaskContext` using deterministic rules."""

    def plan(self, task: TaskContext, *, budget: int | None = None,
             overrides: dict[str, int] | None = None) -> ContextPlan:
        hard = budget if budget is not None else budget_for(task.agent, overrides)
        # Target = what we aim to spend; reserve = what we keep for tool results
        # and the model's own output inside the same envelope (spec section 18).
        target = int(hard * 0.75)
        reserve = hard - target

        sections: list[SectionPlan] = []
        shares = _ANSWER_SHARES if task.task_type in ANSWER_TASK_TYPES else _SYSTEM_SHARES
        for type_, share in shares.items():
            if type_ == TYPE_REFERENCE:
                # References are never inlined on spec: the agent pulls them with
                # read_skill_reference when the task actually needs the detail.
                sections.append(SectionPlan(
                    type=TYPE_REFERENCE, provider='skills', budget=0, required=False, lazy=True,
                    reason='lazy: load on demand via read_skill_reference',
                ))
                continue
            # `required` marks what the compiler may not drop during allocation;
            # `NEVER_TRIM` additionally protects an item from budget trimming.
            # The skill contract is required but still trimmable when a context is
            # genuinely over budget, which is why the two sets differ.
            required = type_ in REQUIRED_TYPES
            sections.append(SectionPlan(
                type=type_,
                provider=_PROVIDER_BY_TYPE.get(type_, CORE_PROVIDER),
                budget=max(60, int(hard * share)),
                required=required,
                reason='required by the agent contract' if required else 'supporting material',
            ))

        level = reasoning_level(task.features)
        return ContextPlan(
            agent=task.agent,
            task_type=task.task_type,
            reasoning_level=level,
            hard_budget=hard,
            target_budget=target,
            reserve_tokens=reserve,
            sections=sections,
            features=task.features,
        )


# Sections the compiler renders into the system message, in order. The prefetched
# extraction context sits right after the skill contract it elaborates, and before the
# tool catalogue — it is what the model reads instead of a reference round.
SYSTEM_ORDER = (TYPE_BOOTSTRAP, TYPE_SKILL, TYPE_EXTRACTION_CONTEXT, TYPE_CUSTOM,
                TYPE_CONSTRAINTS, TYPE_TOOLS)
# Sections rendered into the dynamic context block.
CONTEXT_ORDER = (TYPE_TASK, TYPE_KNOWLEDGE, TYPE_EVIDENCE, TYPE_EXAMPLES, TYPE_HISTORY)
