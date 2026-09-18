"""Skill provider: the skill contract plus an honest reference catalogue.

Progressive disclosure in practice:
- the SKILL.md contract is loaded (it is what tells the agent how to work),
- references are loaded only when the task asks for them by name,
- every reference that is *not* loaded is still reported as a withheld item with
  its potential token cost, so the trace shows both what we spent and what we
  deliberately left on the shelf.
"""
from __future__ import annotations

from ..items import TYPE_REFERENCE, TYPE_SKILL, ContextItem
from ..planner import ContextPlan
from ..policies import Load
from ...runtime.task import TaskContext
from ...skills import (load_skill, read_reference, reference_costs,
                       reference_version, references_for, skill_source, skill_version)


class SkillProvider:
    name = 'skills'

    def provide(self, task: TaskContext, plan: ContextPlan) -> list[ContextItem]:
        skill = task.skill
        if not skill:
            return []

        items = [ContextItem(
            id=f'skill:{skill}',
            type=TYPE_SKILL,
            content=f'[SKILL]\n{load_skill(skill)}',
            source=skill_source(skill),
            priority=1.0, relevance=1.0, information_gain=0.8, evidence_strength=0.5,
            required=True,
            version=skill_version(skill),
            metadata={'estimated_tokens': sum(t for _, t in reference_costs(skill))},
            reason='the agent contract for this task',
        )]

        # Explicit requests win; the load_when selector adds what the task's
        # features justify (defaults-shaped tasks add nothing - P1 baseline).
        requested = list(dict.fromkeys(
            list(task.features.references) + references_for(skill, task.features)))
        for ref in requested:
            try:
                body = read_reference(skill, ref)
            except ValueError:
                continue
            items.append(ContextItem(
                id=f'reference:{skill}/{ref}',
                type=TYPE_REFERENCE,
                content=body,
                source=f'skills/{skill}/references/{ref}',
                priority=0.7, relevance=0.9, information_gain=0.7, evidence_strength=0.5,
                version=reference_version(skill, ref),
                metadata={'estimated_tokens': dict(reference_costs(skill)).get(ref)},
                reason=('requested by the task features' if ref in task.features.references
                        else 'selected by load_when rules from the task features'),
            ))

        for name, tokens in reference_costs(skill):
            if name in requested:
                continue
            items.append(ContextItem(
                id=f'reference-lazy:{skill}/{name}',
                type=TYPE_REFERENCE,
                content='',
                source=f'skills/{skill}/references/{name}',
                priority=0.3, relevance=0.4, information_gain=0.5, evidence_strength=0.4,
                load_policy=Load.RETRIEVE_LATER,
                version=reference_version(skill, name),
                metadata={'estimated_tokens': tokens},
                reason='lazy: available through read_skill_reference',
            ))
        return items
