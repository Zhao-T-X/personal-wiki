from __future__ import annotations
from ..agents import ask_personal, ask_knowledge, ask_research, ask_curator, ask_review

ROLES = {'personal': ask_personal, 'knowledge': ask_knowledge, 'research': ask_research, 'curator': ask_curator, 'review': ask_review}


def infer_role(message: str) -> str:
    text = message.lower()
    if any(k in text for k in ('审查', '审核', 'review', 'candidate', '候选')):
        return 'review'
    if any(k in text for k in ('整理', '去重', '冲突', '矛盾', 'curate', 'dedup')):
        return 'curator'
    if any(k in text for k in ('研究', '调研', '比较', 'research', '分析')):
        return 'research'
    if any(k in text for k in ('知识', '实体', '关系', 'claim', 'entity', 'graph')):
        return 'knowledge'
    return 'personal'


_AGENT_NAMES = {'personal': 'PersonalAgent', 'knowledge': 'KnowledgeAgent',
                'research': 'ResearchAgent', 'curator': 'CuratorAgent', 'review': 'ReviewAgent'}


async def run_research_pipeline(question: str) -> dict:
    """Agent-to-agent research chain (Context Runtime P3, spec §7).

    KnowledgeAgent gathers what the knowledge base already knows, hands the
    result to ResearchAgent as a persisted TaskPacket - agents exchange *state*,
    never context. If the knowledge step fails, research proceeds packet-less
    rather than failing the whole task.
    """
    import sys
    from ..context.packet import TaskPacket, save_packet

    knowledge_answer = ''
    try:
        knowledge_answer = await ROLES['knowledge'](question)
    except Exception as exc:
        print(f'[workflow.research] knowledge step failed: '
              f'{type(exc).__name__}: {exc}', file=sys.stderr)

    packet = None
    if knowledge_answer.strip():
        packet = TaskPacket(goal=question, agent='KnowledgeAgent',
                            context_summary=knowledge_answer,
                            required_actions=['research beyond the knowledge base',
                                              'compare sources and cite evidence'])
        save_packet(packet)

    answer = await ROLES['research'](question, packet=packet.to_dict() if packet else None)
    return {'answer': answer, 'agent': 'research', 'available_agents': list(ROLES),
            'packet_id': packet.task_id if packet else None}


async def run_agent(message: str, role: str = 'auto', conversation_id: str = '') -> dict:
    selected = infer_role(message) if role == 'auto' else role
    if selected not in ROLES:
        raise ValueError(f'Unknown agent role: {selected}')

    # Multi-turn (P3): roll the persisted compressed state forward for this
    # turn; a failed agent call never reaches end_turn, so it does not pollute
    # the history.
    state = None
    history_summary, history = '', []
    if conversation_id:
        from ..context.history_store import begin_turn
        state = begin_turn(conversation_id, _AGENT_NAMES[selected], message)
        history_summary, history = state['summary'], state['recent']

    answer = await ROLES[selected](message, history_summary=history_summary, history=history)

    result = {'answer': answer, 'agent': selected, 'available_agents': list(ROLES)}
    if conversation_id:
        if state is not None:
            from ..context.history_store import end_turn
            saved = end_turn(conversation_id, _AGENT_NAMES[selected], state, answer)
            result['conversation_id'] = conversation_id
            result['history'] = {'messages_seen': saved['messages_seen'],
                                 'summary_tokens': saved['stats']['summary_tokens'],
                                 'window_messages': len(saved['recent'])}
        else:
            # begin_turn failed to produce state (defensive): report honestly.
            result['conversation_id'] = conversation_id
            result['history'] = {'messages_seen': 1, 'summary_tokens': 0, 'window_messages': 1}
    return result
