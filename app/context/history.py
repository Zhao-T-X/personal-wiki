"""History compression (Context Runtime P2c, spec §7).

History is the only context section that grows with *use* rather than with the
task. The rule: the model sees the recent 3-5 messages verbatim plus a compact
summary of everything older - never the full transcript.

The summary here is deterministic and extractive (user goals + last action +
counts); P3 replaces its persistence with the ``conversation_summaries`` table,
but the shape a provider consumes stays this one.
"""
from __future__ import annotations

from typing import Any

from .tokens import estimate_tokens

DEFAULT_KEEP_RECENT = 4          # spec §7: "最近 3–5 条消息"
_GOAL_CHARS = 160
_ACTION_CHARS = 200


def clip(text: str, limit: int) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= limit else text[:limit].rstrip() + '…'


def compress_history(messages: list[dict[str, Any]] | None,
                     keep_recent: int = DEFAULT_KEEP_RECENT) -> dict[str, Any]:
    """Split a transcript into ``recent`` verbatim messages + an older-summary.

    Returns ``{'recent': [...], 'summary': str, 'stats': {...}}``. ``stats``
    carries the honest token delta so the trace can show what compression bought.
    """
    msgs = [m for m in (messages or []) if isinstance(m, dict) and m.get('content')]
    if not msgs:
        return {'recent': [], 'summary': '', 'stats': {
            'messages_in': 0, 'messages_recent': 0, 'messages_compressed': 0,
            'tokens_before': 0, 'tokens_after': 0}}

    keep = max(0, int(keep_recent))
    older, recent = msgs[:-keep] if keep else msgs, msgs[-keep:] if keep else []

    lines: list[str] = []
    users = [m for m in older if m.get('role') == 'user']
    if users:
        lines.append(f'- user goal: {clip(users[0]["content"], _GOAL_CHARS)}')
        for extra in users[1:3]:
            lines.append(f'- follow-up: {clip(extra["content"], _GOAL_CHARS)}')
    assistants = [m for m in older if m.get('role') == 'assistant']
    if assistants:
        lines.append(f'- last action: {clip(assistants[-1]["content"], _ACTION_CHARS)}')
    if older:
        lines.insert(0, f'({len(older)} earlier messages compressed)')

    def _tokens(items: list[dict[str, Any]]) -> int:
        return estimate_tokens(' '.join(f"{m.get('role')} {m.get('content')}" for m in items))

    summary = '\n'.join(lines)
    after = _tokens(recent) + estimate_tokens(summary)
    return {
        'recent': recent,
        'summary': summary,
        'stats': {
            'messages_in': len(msgs),
            'messages_recent': len(recent),
            'messages_compressed': len(older),
            'tokens_before': _tokens(msgs),
            'tokens_after': after,
        },
    }
