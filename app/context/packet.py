"""Task Packet: agents exchange *state*, never the whole conversation.

A packet is what one agent hands to the next (or what a task is resumed from):
the goal, the facts already established, the open questions, the constraints, and
the ids of the knowledge involved. Payloads stay behind their ids and are fetched
by tools when - and only when - they are needed.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from .tokens import estimate_tokens


@dataclass
class TaskPacket:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal: str = ''
    context_summary: str = ''
    known_facts: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    required_actions: list[str] = field(default_factory=list)
    entity_ids: list[str] = field(default_factory=list)
    claim_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    document_ids: list[str] = field(default_factory=list)
    required_output: dict[str, Any] = field(default_factory=dict)
    agent: str = ''
    created_at: str = ''
    updated_at: str = ''

    # Lists that are stored as JSON inside the packet payload.
    LIST_FIELDS = ('known_facts', 'open_questions', 'constraints', 'required_actions',
                   'entity_ids', 'claim_ids', 'evidence_ids', 'document_ids')
    SCALAR_FIELDS = ('task_id', 'goal', 'context_summary', 'agent')

    def to_dict(self) -> dict:
        return {
            'task_id': self.task_id,
            'goal': self.goal,
            'context_summary': self.context_summary,
            'known_facts': list(self.known_facts),
            'open_questions': list(self.open_questions),
            'constraints': list(self.constraints),
            'required_actions': list(self.required_actions),
            'entity_ids': list(self.entity_ids),
            'claim_ids': list(self.claim_ids),
            'evidence_ids': list(self.evidence_ids),
            'document_ids': list(self.document_ids),
            'required_output': dict(self.required_output),
            'agent': self.agent,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'TaskPacket':
        packet = cls(task_id=str(data.get('task_id') or uuid.uuid4()),
                     goal=str(data.get('goal') or ''),
                     context_summary=str(data.get('context_summary') or ''),
                     required_output=dict(data.get('required_output') or {}),
                     agent=str(data.get('agent') or ''),
                     created_at=str(data.get('created_at') or ''),
                     updated_at=str(data.get('updated_at') or ''))
        for name in cls.LIST_FIELDS:
            value = data.get(name)
            setattr(packet, name, [str(v) for v in value] if isinstance(value, list) else [])
        return packet

    def estimate_tokens(self) -> int:
        """Cost of handing this packet over (it should stay small by design)."""
        return estimate_tokens(json.dumps(self.to_dict(), ensure_ascii=False))

    def context_block(self, *, summary_chars: int = 300, item_chars: int = 120,
                      max_items: int = 5) -> str:
        """Compact prompt block for the receiving agent (small by design).

        This is the whole packet as the next agent sees it: goal, what the
        previous agent established, facts / open questions / constraints, and
        id *counts* - payloads stay behind their ids and are fetched by tools.
        """
        from .history import clip

        lines = ['[TASK PACKET]',
                 f'goal: {clip(self.goal, summary_chars)}',
                 f'handoff from: {self.agent or "unknown agent"}']
        if self.context_summary:
            lines.append(f'established: {clip(self.context_summary, summary_chars)}')
        for fact in self.known_facts[:max_items]:
            lines.append(f'- fact: {clip(fact, item_chars)}')
        for question in self.open_questions[:3]:
            lines.append(f'- open: {clip(question, item_chars)}')
        for constraint in self.constraints[:3]:
            lines.append(f'- constraint: {clip(constraint, item_chars)}')
        lines.append('ids: ' + ', '.join([
            f'{len(self.entity_ids)} entities', f'{len(self.claim_ids)} claims',
            f'{len(self.evidence_ids)} evidence', f'{len(self.document_ids)} documents']))
        return '\n'.join(lines)

    @classmethod
    def from_task(cls, task, *, goal: str = '', summary: str = '') -> 'TaskPacket':
        """Build a packet from a `TaskContext` - ids travel, payloads do not."""
        return cls(goal=goal or task.goal, context_summary=summary, agent=task.agent,
                   entity_ids=list(task.entity_ids), claim_ids=list(task.claim_ids),
                   evidence_ids=list(task.evidence_ids), document_ids=list(task.document_ids))


# --------------------------------------------------------------------- storage

def save_packet(packet: TaskPacket) -> str:
    """Persist a packet in SQLite (best effort). Returns the packet id."""
    from ..db import transaction
    try:
        with transaction() as conn:
            conn.execute(
                '''INSERT INTO task_packets(id, goal, context_summary, agent, payload_json, estimated_tokens, updated_at)
                   VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(id) DO UPDATE SET goal=excluded.goal,
                     context_summary=excluded.context_summary, agent=excluded.agent,
                     payload_json=excluded.payload_json, estimated_tokens=excluded.estimated_tokens,
                     updated_at=CURRENT_TIMESTAMP''',
                (packet.task_id, packet.goal, packet.context_summary, packet.agent,
                 json.dumps(packet.to_dict(), ensure_ascii=False), packet.estimate_tokens()),
            )
    except Exception as exc:
        import sys
        print(f'[context.packet] write failed: {type(exc).__name__}: {exc}', file=sys.stderr)
    return packet.task_id


def get_packet(task_id: str) -> dict | None:
    """Read one packet, or None when it does not exist."""
    from ..db import connect
    conn = connect()
    try:
        row = conn.execute('SELECT * FROM task_packets WHERE id=?', (task_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item['payload'] = json.loads(item.pop('payload_json') or '{}')
        return item
    finally:
        conn.close()


def list_packets(limit: int = 50) -> list[dict]:
    """Recent packets, newest first."""
    from ..db import connect
    conn = connect()
    try:
        rows = conn.execute('SELECT * FROM task_packets ORDER BY created_at DESC LIMIT ?',
                            (max(1, min(limit, 200)),)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item['payload'] = json.loads(item.pop('payload_json') or '{}')
            out.append(item)
        return out
    finally:
        conn.close()
