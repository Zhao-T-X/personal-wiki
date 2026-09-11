"""History provider: recent messages verbatim + a compressed older-summary.

Spec §7: never hand an agent the full transcript. At most two items are
emitted - the rolling summary (recomputed from ``task.history``, or reused
from ``conversation_summaries`` via ``task.history_summary``) and the recent
window - and the provider stays silent (zero items, zero cost) for
single-turn tasks.
"""
from __future__ import annotations

from ..history import compress_history
from ..items import TYPE_HISTORY, ContextItem
from ..planner import ContextPlan
from ...runtime.task import TaskContext


def _render(messages: list[dict]) -> str:
    return '\n'.join(f"{m.get('role')}: {m.get('content')}" for m in messages)


class HistoryProvider:
    name = 'history'

    def provide(self, task: TaskContext, plan: ContextPlan) -> list[ContextItem]:
        messages = list(getattr(task, 'history', []) or [])
        # None = no persisted conversation state; a string (even '') means the
        # state came from conversation_summaries.
        persisted = getattr(task, 'history_summary', None) is not None
        persisted_summary = str(getattr(task, 'history_summary', '') or '')
        if not messages and not persisted_summary:
            return []

        items: list[ContextItem] = []

        if persisted:
            # Rolling summary persisted by conversation_summaries: reuse it as
            #-is instead of recompressing a transcript we no longer have.
            if persisted_summary:
                items.append(ContextItem(
                    id='history:summary',
                    type=TYPE_HISTORY,
                    content=f'[HISTORY SUMMARY]\n{persisted_summary}',
                    source='conversation_summaries',
                    priority=0.5, relevance=0.7, information_gain=0.6, evidence_strength=0.3,
                    metadata={'summary_tokens': len(persisted_summary) // 4,
                              'persisted': True},
                    reason='rolling summary persisted by conversation_summaries',
                ))
            recent_messages = messages          # already the persisted window
        else:
            compressed = compress_history(messages)
            if compressed['summary']:
                items.append(ContextItem(
                    id='history:summary',
                    type=TYPE_HISTORY,
                    content=f'[HISTORY SUMMARY]\n{compressed["summary"]}',
                    source='context.history.compress_history',
                    priority=0.5, relevance=0.7, information_gain=0.6, evidence_strength=0.3,
                    metadata=compressed['stats'],
                    reason=(f"compressed {compressed['stats']['messages_compressed']} of "
                            f"{compressed['stats']['messages_in']} messages"),
                ))
            recent_messages = compressed['recent']
        if recent_messages:
            items.append(ContextItem(
                id='history:recent',
                type=TYPE_HISTORY,
                content=_render(recent_messages),
                source='conversation_summaries' if persisted else 'task.history',
                priority=0.6, relevance=0.8, information_gain=0.7, evidence_strength=0.3,
                reason='recent conversation window, verbatim',
            ))
        return items
