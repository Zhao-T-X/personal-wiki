"""Context Runtime P3: Task Packet on the real agent-to-agent chain (spec §7).

The research flow is the first real handoff: KnowledgeAgent gathers what the
knowledge base already knows, the result is persisted as a TaskPacket, and
ResearchAgent receives the packet as a compact required context block -
agents exchange *state*, never context.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD_MODULES = (
    'app.context.tokens',
    'app.context.history',
    'app.context.packet',
    'app.context.policies',
    'app.context.items',
    'app.context.value',
    'app.context.manager',
    'app.context.planner',
    'app.context.providers.base',
    'app.context.providers.skills',
    'app.context.providers.tools',
    'app.context.providers.history',
    'app.context.providers',
    'app.context.compiler',
    'app.context',
    'app.runtime.task',
    'app.prompt_profiles',
)


def _reload(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'wiki.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config
    import app.db
    importlib.reload(app.config)
    importlib.reload(app.db)
    for name in _RELOAD_MODULES:
        importlib.reload(importlib.import_module(name))
    app.db.init_db()


def test_packet_context_block_stays_small_by_design(tmp_path):
    _reload(tmp_path)
    from app.context.packet import TaskPacket
    from app.context.tokens import estimate_tokens

    packet = TaskPacket(goal='对比 Qdrant 与 pgvector',
                        agent='KnowledgeAgent',
                        context_summary='知识库已有结论。' + '背景' * 900,
                        known_facts=['Qdrant 支持 HNSW', 'pgvector 0.7 支持 HNSW'] * 4,
                        open_questions=['延迟对比缺少基准'],
                        constraints=['必须可自托管'],
                        entity_ids=['e1', 'e2'], document_ids=['d1'])
    block = packet.context_block()

    assert block.startswith('[TASK PACKET]')
    assert estimate_tokens(block) < 500                  # small by design
    assert '4 entities' not in block                     # id *counts*, not payloads
    assert '2 entities' in block


def test_pipeline_hands_over_a_persisted_packet(tmp_path):
    _reload(tmp_path)
    from app.context.packet import list_packets
    import app.workflows.agent_workflow as workflow

    received: dict = {}

    async def fake_knowledge(message, **kwargs):
        return '知识库结论：Qdrant 与 pgvector 都支持 HNSW。'

    async def fake_research(message, *, history_summary=None, history=None, packet=None):
        received['message'] = message
        received['packet'] = packet
        return '研究完成'

    original = dict(workflow.ROLES)
    workflow.ROLES = {'knowledge': fake_knowledge, 'research': fake_research}
    try:
        result = asyncio.run(workflow.run_research_pipeline('对比 Qdrant 与 pgvector'))
    finally:
        workflow.ROLES = original

    assert received['packet'] is not None
    assert received['packet']['goal'] == '对比 Qdrant 与 pgvector'
    assert 'HNSW' in received['packet']['context_summary']
    assert result['packet_id'] == received['packet']['task_id']
    stored = list_packets()
    assert len(stored) == 1 and stored[0]['agent'] == 'KnowledgeAgent'


def test_pipeline_proceeds_packetless_when_knowledge_fails(tmp_path):
    _reload(tmp_path)
    import app.workflows.agent_workflow as workflow

    received: dict = {}

    async def broken_knowledge(message, **kwargs):
        raise RuntimeError('LLM API key is not configured')

    async def fake_research(message, *, history_summary=None, history=None, packet=None):
        received['packet'] = packet
        return '仍然完成'

    original = dict(workflow.ROLES)
    workflow.ROLES = {'knowledge': broken_knowledge, 'research': fake_research}
    try:
        result = asyncio.run(workflow.run_research_pipeline('问题'))
    finally:
        workflow.ROLES = original

    assert received['packet'] is None
    assert result['packet_id'] is None
    assert result['answer'] == '仍然完成'


def test_build_context_injects_the_packet_as_required_task_item(tmp_path):
    _reload(tmp_path)
    from app.context.items import TYPE_TASK
    from app.context.packet import TaskPacket, save_packet
    from app.prompt_profiles import build_context

    packet = TaskPacket(goal='研究目标', agent='KnowledgeAgent', context_summary='已知结论')
    save_packet(packet)

    compiled = build_context('research', custom='X', persist=False,
                             use_cache=False, packet=packet.to_dict())
    packets = [i for i in compiled.items if i.type == TYPE_TASK]

    assert len(packets) == 1
    assert packets[0].source == f"task_packets/{packet.task_id}"
    assert packets[0].required
    assert '[TASK PACKET]' in packets[0].content
    assert '已知结论' in compiled.render()
