"""conversation_summaries persistence (Context Runtime P3, spec §7/§10).

The stored row *is* the compressed conversation state - a rolling summary of
everything that fell out of the recent window plus the recent window itself -
so a new turn needs exactly one row read and one upsert, and a full transcript
is never stored or replayed (criterion 5: agents never exchange full history).

Turn protocol:

    state = begin_turn(conversation_id, agent, user_message)   # read + roll
    ... build the agent with state['summary'] / state['recent'] ...
    end_turn(conversation_id, agent, state, assistant_reply)    # roll + save

A turn whose agent call fails simply never reaches ``end_turn``: failed turns
do not pollute the history.
"""
from __future__ import annotations

import json
from typing import Any

from ..db import connect, loads
from .history import compress_history
from .tokens import estimate_tokens, truncate_to_tokens

# Hard ceiling for the merged summary. Each turn adds at most two short lines;
# when the cap is hit the *oldest* summary lines are dropped - the recent
# window and the newest goals survive, runaway growth does not.
MAX_SUMMARY_TOKENS = 1200


def load_conversation(conversation_id: str, agent: str) -> dict[str, Any] | None:
    """The persisted compressed state for one conversation, or None."""
    if not conversation_id:
        return None
    conn = connect()
    row = conn.execute(
        'SELECT summary,recent_json,messages_seen FROM conversation_summaries '
        'WHERE conversation_id=? AND agent=?', (conversation_id, agent)).fetchone()
    conn.close()
    if row is None:
        return None
    return {'summary': row['summary'], 'recent': loads(row['recent_json'], []),
            'messages_seen': row['messages_seen']}


def _merge_summary(previous: str, delta: str) -> str:
    merged = '\n'.join(part for part in (previous.strip(), delta.strip()) if part)
    return truncate_to_tokens(merged, MAX_SUMMARY_TOKENS)


def _roll(summary: str, messages: list[dict]) -> dict[str, Any]:
    compressed = compress_history(messages)
    return {
        'summary': _merge_summary(summary, compressed['summary']),
        'recent': compressed['recent'],
        'stats': {**compressed['stats'], 'summary_tokens': estimate_tokens(
            _merge_summary(summary, compressed['summary']))},
    }


def begin_turn(conversation_id: str, agent: str, user_message: str) -> dict[str, Any]:
    """Roll the persisted state forward to include the incoming user message.

    Returns ``{'summary', 'recent', 'messages_seen', 'stats'}``; ``recent``
    always ends with the current user message, so the model sees it verbatim.
    """
    prior = load_conversation(conversation_id, agent) or {
        'summary': '', 'recent': [], 'messages_seen': 0}
    rolled = _roll(prior['summary'], list(prior['recent']) + [
        {'role': 'user', 'content': user_message}])
    return {**rolled, 'messages_seen': prior['messages_seen'] + 1}


def end_turn(conversation_id: str, agent: str, state: dict[str, Any],
             assistant_reply: str) -> dict[str, Any]:
    """Fold the assistant reply in and persist the compressed state (upsert)."""
    rolled = _roll(state['summary'], list(state['recent']) + [
        {'role': 'assistant', 'content': assistant_reply}])
    messages_seen = state.get('messages_seen', 0) + 1
    conn = connect()
    conn.execute(
        '''INSERT INTO conversation_summaries(conversation_id,agent,summary,recent_json,messages_seen)
           VALUES(?,?,?,?,?)
           ON CONFLICT(conversation_id,agent) DO UPDATE SET
             summary=excluded.summary, recent_json=excluded.recent_json,
             messages_seen=excluded.messages_seen, updated_at=CURRENT_TIMESTAMP''',
        (conversation_id, agent, rolled['summary'],
         json.dumps(rolled['recent'], ensure_ascii=False), messages_seen))
    conn.commit()
    conn.close()
    return {**rolled, 'messages_seen': messages_seen}


def forget_conversation(conversation_id: str, agent: str | None = None) -> int:
    """Drop persisted conversation state (used by tests and 'new chat')."""
    conn = connect()
    if agent:
        cur = conn.execute('DELETE FROM conversation_summaries WHERE conversation_id=? AND agent=?',
                           (conversation_id, agent))
    else:
        cur = conn.execute('DELETE FROM conversation_summaries WHERE conversation_id=?',
                           (conversation_id,))
    conn.commit()
    conn.close()
    return cur.rowcount
