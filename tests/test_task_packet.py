from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD_MODULES = (
    'app.context.tokens',
    'app.context.budget',
    'app.context.policies',
    'app.context.items',
    'app.context.value',
    'app.context.manager',
    'app.context.planner',
    'app.context.compiler',
    'app.context.trace',
    'app.context.packet',
    'app.context',
    'app.runtime.task',
    'app.runtime',
)


def _reload_db(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'wiki.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config
    import app.db
    importlib.reload(app.config)
    importlib.reload(app.db)
    for name in _RELOAD_MODULES:
        importlib.reload(importlib.import_module(name))
    app.db.init_db()
    return app.db


def test_packet_round_trips_through_a_dict(tmp_path):
    _reload_db(tmp_path)
    from app.context import TaskPacket

    packet = TaskPacket(goal='compare RAG and fine-tuning', context_summary='two approaches under debate',
                        known_facts=['RAG improves grounding'], open_questions=['which is cheaper?'],
                        constraints=['cite sources'], required_actions=['summarise'],
                        entity_ids=['e1'], claim_ids=['c1'], evidence_ids=['ev1'], document_ids=['d1'],
                        required_output={'format': 'markdown'}, agent='ResearchAgent')
    again = TaskPacket.from_dict(packet.to_dict())

    assert again.goal == packet.goal
    assert again.known_facts == ['RAG improves grounding']
    assert again.evidence_ids == ['ev1']
    assert again.required_output == {'format': 'markdown'}


def test_packet_from_task_carries_ids_and_not_payloads(tmp_path):
    _reload_db(tmp_path)
    from app.context import TaskPacket
    from app.runtime import TaskContext, features_for

    task = TaskContext(task_type='ask', agent='KnowledgeAgent', goal='why is RAG worse here?',
                       features=features_for('KnowledgeAgent', 'ask'),
                       entity_ids=['e1', 'e2'], claim_ids=['c1'], evidence_ids=['ev1', 'ev2'])
    packet = TaskPacket.from_task(task, summary='benchmark regression')

    assert packet.goal == 'why is RAG worse here?'
    assert packet.entity_ids == ['e1', 'e2']
    assert packet.evidence_ids == ['ev1', 'ev2']
    assert packet.agent == 'KnowledgeAgent'
    assert 'RAG improves' not in str(packet.to_dict())      # no payloads travel


def test_packet_stays_cheap(tmp_path):
    _reload_db(tmp_path)
    from app.context import TaskPacket

    packet = TaskPacket(goal='goal', context_summary='summary',
                        known_facts=[f'fact {i}' for i in range(10)],
                        evidence_ids=[f'ev{i}' for i in range(20)])
    assert packet.estimate_tokens() < 300


def test_packets_persist_in_sqlite(tmp_path):
    db = _reload_db(tmp_path)
    from app.context import TaskPacket, get_packet, list_packets, save_packet

    first = TaskPacket(goal='first', agent='KnowledgeAgent')
    second = TaskPacket(goal='second', agent='ResearchAgent')
    save_packet(first)
    save_packet(second)

    stored = get_packet(first.task_id)
    assert stored is not None
    assert stored['goal'] == 'first'
    assert stored['payload']['goal'] == 'first'
    assert stored['estimated_tokens'] > 0

    listed = list_packets()
    assert len(listed) == 2
    assert {p['agent'] for p in listed} == {'KnowledgeAgent', 'ResearchAgent'}

    conn = db.connect()
    count = conn.execute('SELECT COUNT(*) FROM task_packets').fetchone()[0]
    conn.close()
    assert count == 2


def test_saving_a_packet_again_updates_it(tmp_path):
    _reload_db(tmp_path)
    from app.context import TaskPacket, get_packet, list_packets, save_packet

    packet = TaskPacket(goal='draft')
    save_packet(packet)
    packet.goal = 'final'
    packet.known_facts.append('decided to keep evidence ids')
    save_packet(packet)

    assert len(list_packets()) == 1                      # upsert, not a second row
    stored = get_packet(packet.task_id)
    assert stored['goal'] == 'final'
    assert stored['payload']['known_facts'] == ['decided to keep evidence ids']


def test_get_packet_returns_none_for_unknown_id(tmp_path):
    _reload_db(tmp_path)
    from app.context import get_packet

    assert get_packet('nope') is None
