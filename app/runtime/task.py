"""Task description consumed by the Context Planner.

`TaskContext` is the *input* of the Context Runtime: what the agent is being asked
to do, plus the cheap deterministic signals the planner is allowed to look at
(complexity, conflict level, knowledge coverage, evidence requirement). Nothing
here calls an LLM - the planner must stay free of extra model calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Intent vocabulary.
INTENT_CHAT = 'chat'
INTENT_EXTRACT = 'extract'
INTENT_ASK = 'ask'
INTENT_RESEARCH = 'research'
INTENT_CURATE = 'curate'
INTENT_REVIEW = 'review'


@dataclass
class TaskFeatures:
    """Deterministic signals about the task (all 0..1 unless noted)."""

    intent: str = INTENT_CHAT
    complexity: float = 0.3
    knowledge_coverage: float = 1.0
    conflict_level: float = 0.0
    evidence_requirement: float = 0.5
    freshness: str = 'normal'          # normal | recent | stale
    references: list[str] = field(default_factory=list)   # explicitly requested skill references
    needs_events: bool = False
    needs_claims: bool = True
    needs_entities: bool = True

    def to_dict(self) -> dict:
        return {
            'intent': self.intent,
            'complexity': self.complexity,
            'knowledge_coverage': self.knowledge_coverage,
            'conflict_level': self.conflict_level,
            'evidence_requirement': self.evidence_requirement,
            'freshness': self.freshness,
            'references': list(self.references),
        }


@dataclass
class TaskContext:
    """One task handed to an agent."""

    task_type: str                     # chat | extract | ask | research | curate | review
    agent: str                         # KnowledgeAgent / ExtractionAgent / ...
    goal: str = ''
    skill: str | None = None
    document_id: str | None = None
    features: TaskFeatures = field(default_factory=TaskFeatures)
    # Conversation transcript for multi-turn tasks: [{'role', 'content'}, ...].
    # The history provider compresses it (recent messages + summary); empty by
    # default because today's callers are single-turn (persistence arrives P3).
    history: list[dict] = field(default_factory=list)
    # Pre-computed rolling summary from conversation_summaries (P3).
    # None = no persisted conversation state (provider recompresses ``history``);
    # a string - even empty - means the state came from persistence.
    history_summary: str | None = None
    # Ids only: the actual payloads are fetched by tools when needed.
    entity_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)

    def ids(self) -> dict[str, list[str]]:
        return {
            'entity_ids': self.entity_ids,
            'claim_ids': self.claim_ids,
            'evidence_ids': self.evidence_ids,
            'document_ids': self.document_ids,
        }


def features_for(agent: str, task_type: str, **overrides) -> TaskFeatures:
    """Default features per (agent, task type), overridable by the caller."""
    base = {
        'extract': TaskFeatures(intent=INTENT_EXTRACT, complexity=0.4, evidence_requirement=0.9,
                                needs_entities=True, needs_claims=True, needs_events=True),
        'ask': TaskFeatures(intent=INTENT_ASK, complexity=0.3, evidence_requirement=0.8),
        'research': TaskFeatures(intent=INTENT_RESEARCH, complexity=0.8, evidence_requirement=0.9),
        'curate': TaskFeatures(intent=INTENT_CURATE, complexity=0.5, evidence_requirement=0.7),
        'review': TaskFeatures(intent=INTENT_REVIEW, complexity=0.4, evidence_requirement=0.8),
        'chat': TaskFeatures(intent=INTENT_CHAT, complexity=0.2, evidence_requirement=0.3),
    }.get(task_type, TaskFeatures())
    for key, value in overrides.items():
        if hasattr(base, key):
            setattr(base, key, value)
    return base


def reasoning_level(features: TaskFeatures) -> int:
    """Adaptive reasoning level 0-5 (see the design spec, section 27).

    0 direct · 1 knowledge retrieval · 2 deep retrieval · 3 research ·
    4 multi-agent · 5 human review
    """
    if features.intent == INTENT_RESEARCH:
        level = 3
    elif features.intent in (INTENT_REVIEW,):
        level = 4
    elif features.intent in (INTENT_EXTRACT, INTENT_CURATE, INTENT_CHAT):
        level = 0 if features.complexity < 0.3 else 1
    else:
        level = 1

    if features.complexity >= 0.6:
        level += 1
    if features.knowledge_coverage < 0.5:
        level += 1
    if features.conflict_level >= 0.3:
        level += 1
    if features.evidence_requirement >= 0.9 and features.knowledge_coverage < 0.8:
        level += 1
    return max(0, min(level, 5))
