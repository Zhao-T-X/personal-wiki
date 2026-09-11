"""Agent Runtime: task description, routing, escalation and cost policy.

Phase 1 ships the task description the Context Planner consumes. The cost-aware
router, adaptive escalation and result evaluation land in Phase 3, together with
semantic caching and research early stop.
"""
from .task import (INTENT_ASK, INTENT_CHAT, INTENT_CURATE, INTENT_EXTRACT, INTENT_RESEARCH,
                   INTENT_REVIEW, TaskContext, TaskFeatures, features_for, reasoning_level)

__all__ = [
    'TaskContext', 'TaskFeatures', 'features_for', 'reasoning_level',
    'INTENT_CHAT', 'INTENT_EXTRACT', 'INTENT_ASK', 'INTENT_RESEARCH',
    'INTENT_CURATE', 'INTENT_REVIEW',
]
