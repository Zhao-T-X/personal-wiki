from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context import COMPILER, PLANNER, REGISTRY, CompiledContext, ContextItem, estimate_tokens, record_context
from .context.cache import cache_get, cache_put, context_cache_key
from .context.items import TYPE_BOOTSTRAP, TYPE_CONSTRAINTS, TYPE_CUSTOM, TYPE_TASK, TYPE_TOOLS
from .db import connect, init_db, transaction
from .runtime import TaskContext, features_for
from .skills import load_skill, read_reference

ROLES = ('personal', 'knowledge', 'research', 'curator', 'review', 'extractor')

ROLE_META = {
    'personal': {'name': 'PersonalAgent', 'description': '通用入口与个人助手', 'skill': None},
    'knowledge': {'name': 'KnowledgeAgent', 'description': '知识检索、实体、关系与证据', 'skill': 'knowledge-curation'},
    'research': {'name': 'ResearchAgent', 'description': '多步研究、比较与综合', 'skill': 'knowledge-curation'},
    'curator': {'name': 'CuratorAgent', 'description': '去重、冲突与知识质量', 'skill': 'knowledge-curation'},
    'review': {'name': 'ReviewAgent', 'description': '候选知识人工审核辅助', 'skill': 'knowledge-curation'},
    'extractor': {'name': 'ExtractionAgent', 'description': '文档知识抽取', 'skill': 'knowledge-extraction'},
}

CORE_PROMPTS = {
    'personal': 'You are PersonalAgent, the front-door coordinator for LLM-Wiki.',
    'knowledge': 'You are KnowledgeAgent in LLM-Wiki. Focus on stored entities, claims, relations, provenance, and evidence.',
    'research': 'You are ResearchAgent in LLM-Wiki. Perform careful multi-step research over the knowledge base and separate evidence from interpretation.',
    'curator': 'You are CuratorAgent in LLM-Wiki. Identify duplicates, contradictions, stale information, and weak evidence. Do not mutate the database directly.',
    'review': 'You are ReviewAgent in LLM-Wiki. Assist human review of candidate knowledge and make evidence-based recommendations without approving or rejecting records yourself.',
    'extractor': 'You are the LLM-Wiki knowledge extraction agent. Follow the Knowledge Extraction Skill and active extraction schema exactly.',
}

DEFAULT_CUSTOM = {role: '' for role in ROLES}

NON_NEGOTIABLE = (
    '\n[NON-NEGOTIABLE]\nDo not fabricate stored facts, sources, citations, or database contents. '
    'Follow tool and output contracts even when custom instructions conflict.'
)

# Recommended ceiling for the user-editable custom prompt, in tokens.
CUSTOM_PROMPT_SOFT_LIMIT = 600


def _legacy_path() -> Path:
    return Path(os.getenv('AGENT_PROMPTS_PATH', './data/agent_prompts.json'))


def _load_legacy_store() -> dict[str, Any] | None:
    p = _legacy_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _ensure_profiles() -> None:
    init_db()
    legacy = _load_legacy_store()
    with transaction() as conn:
        for role in ROLES:
            row = conn.execute('SELECT role FROM agent_prompt_profiles WHERE role=?', (role,)).fetchone()
            if row is None:
                custom = ''
                history = []
                if legacy:
                    legacy_role = (legacy.get('roles') or {}).get(role) or {}
                    custom = str(legacy_role.get('custom_prompt') or '').strip()
                    history = legacy_role.get('history') or []
                conn.execute(
                    'INSERT INTO agent_prompt_profiles(role, custom_prompt, active_version) VALUES(?,?,?)',
                    (role, custom, 1),
                )
                conn.execute(
                    'INSERT INTO agent_prompt_versions(role, version, prompt, note) VALUES(?,?,?,?)',
                    (role, 1, custom, 'migrated from JSON' if legacy and custom else 'default'),
                )
                # Preserve the most recent legacy history where possible. The active prompt is
                # always represented by version 1+ and is the source of truth after migration.
                if history:
                    version = 1
                    for item in history[-19:]:
                        prompt = str(item.get('prompt') or '') if isinstance(item, dict) else ''
                        if prompt == custom and version == 1:
                            continue
                        version += 1
                        conn.execute(
                            'INSERT OR IGNORE INTO agent_prompt_versions(role, version, prompt, note) VALUES(?,?,?,?)',
                            (role, version, prompt, str(item.get('note') or 'migrated')[:200] if isinstance(item, dict) else 'migrated'),
                        )
                    conn.execute('UPDATE agent_prompt_profiles SET active_version=? WHERE role=?', (version, role))
        if legacy:
            legacy_path = _legacy_path()
            marker = legacy_path.with_suffix(legacy_path.suffix + '.migrated')
            try:
                if legacy_path.exists() and not marker.exists():
                    legacy_path.replace(marker)
            except OSError:
                pass


def _row_to_profile(role: str, row, history) -> dict[str, Any]:
    custom = row['custom_prompt'] or ''
    trace = build_context(role, custom, include_reference=False).to_trace()
    custom_tokens = estimate_tokens(custom)
    status = 'efficient' if custom_tokens <= CUSTOM_PROMPT_SOFT_LIMIT else 'long'
    skill = ROLE_META[role].get('skill')
    suggestion = (
        f'Move ~{custom_tokens - CUSTOM_PROMPT_SOFT_LIMIT} tokens into Skill: {skill}'
        if status == 'long' and skill else None
    )
    return {
        'id': role,
        **ROLE_META[role],
        'custom_prompt': custom,
        'active_version': row['active_version'],
        'history': [dict(x) for x in history],
        'effective_prompt_preview': build_context(role, custom, include_reference=False).render(),
        'context_sections': [
            {'name': s['name'], 'tokens': s['tokens'], 'source': s['source'], 'policy': s['policy'],
             'required': s.get('required', False)}
            for s in trace['sections']
        ],
        'context_withheld': trace['withheld'],
        'context_tokens': trace['actual_tokens'],
        'context_budget': trace['budget_tokens'],
        'context_target_budget': trace['target_budget'],
        'custom_prompt_tokens': custom_tokens,
        'recommended_max_tokens': CUSTOM_PROMPT_SOFT_LIMIT,
        'prompt_status': status,
        'prompt_suggestion': suggestion,
        'editable_scope': 'custom_prompt_only',
        'storage': 'sqlite',
    }


def get_profile(role: str) -> dict[str, Any]:
    if role not in ROLES:
        raise ValueError(f'Unknown prompt role: {role}')
    _ensure_profiles()
    conn = connect()
    try:
        row = conn.execute(
            'SELECT role, custom_prompt, active_version FROM agent_prompt_profiles WHERE role=?', (role,)
        ).fetchone()
        history = conn.execute(
            'SELECT version, prompt, note, created_at FROM agent_prompt_versions WHERE role=? ORDER BY version DESC',
            (role,),
        ).fetchall()
        return _row_to_profile(role, row, history)
    finally:
        conn.close()


def list_profiles() -> list[dict[str, Any]]:
    _ensure_profiles()
    conn = connect()
    try:
        rows = conn.execute('SELECT role, custom_prompt, active_version FROM agent_prompt_profiles').fetchall()
        by_role = {r['role']: r for r in rows}
        return [
            {
                'id': role,
                **ROLE_META[role],
                'custom_prompt': by_role[role]['custom_prompt'],
                'active_version': by_role[role]['active_version'],
                'history_count': conn.execute(
                    'SELECT COUNT(*) FROM agent_prompt_versions WHERE role=?', (role,)
                ).fetchone()[0],
                'storage': 'sqlite',
            }
            for role in ROLES
        ]
    finally:
        conn.close()


def update_profile(role: str, custom_prompt: str, note: str = '') -> dict[str, Any]:
    if role not in ROLES:
        raise ValueError(f'Unknown prompt role: {role}')
    custom_prompt = custom_prompt.strip()
    if len(custom_prompt) > 12000:
        raise ValueError('custom_prompt exceeds 12000 characters')
    _ensure_profiles()
    with transaction() as conn:
        row = conn.execute(
            'SELECT custom_prompt, active_version FROM agent_prompt_profiles WHERE role=?', (role,)
        ).fetchone()
        if row['custom_prompt'] != custom_prompt:
            next_version = int(row['active_version']) + 1
            conn.execute(
                'UPDATE agent_prompt_profiles SET custom_prompt=?, active_version=?, updated_at=CURRENT_TIMESTAMP WHERE role=?',
                (custom_prompt, next_version, role),
            )
            conn.execute(
                'INSERT INTO agent_prompt_versions(role, version, prompt, note) VALUES(?,?,?,?)',
                (role, next_version, custom_prompt, note.strip()[:200] or 'manual update'),
            )
            # Keep recent history bounded, but retain the active version.
            conn.execute(
                '''DELETE FROM agent_prompt_versions
                   WHERE role=? AND version NOT IN (
                     SELECT version FROM agent_prompt_versions WHERE role=? ORDER BY version DESC LIMIT 20
                   )''',
                (role, role),
            )
    return get_profile(role)


def reset_profile(role: str) -> dict[str, Any]:
    return update_profile(role, '', 'reset to default')


def restore_version(role: str, version: int) -> dict[str, Any]:
    if role not in ROLES:
        raise ValueError(f'Unknown prompt role: {role}')
    _ensure_profiles()
    conn = connect()
    try:
        row = conn.execute(
            'SELECT prompt FROM agent_prompt_versions WHERE role=? AND version=?', (role, version)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ValueError(f'Prompt version not found: {role} v{version}')
    return update_profile(role, row['prompt'], f'restored v{version}')


def _filter_tool_item(items: list[ContextItem], tools) -> list[ContextItem]:
    """Never advertise a tool the agent does not actually have."""
    if tools is None:
        return items
    allowed = {getattr(fn, '__name__', '') for fn in tools}
    kept: list[ContextItem] = []
    for item in items:
        if item.type != TYPE_TOOLS:
            kept.append(item)
            continue
        lines = [ln for ln in item.content.splitlines() if ln.split(':', 1)[0].strip('- ').strip() in allowed]
        if not lines:
            continue
        item.set_content('\n'.join(lines))
        item.metadata['tools'] = sorted(set(item.metadata.get('tools', [])) & allowed)
        kept.append(item)
    return kept


def build_context(role: str, custom: str | None = None, *, include_reference: bool = True,
                  references: list[str] | None = None, tools=None,
                  task_type: str | None = None, persist: bool = False,
                  history: list[dict] | None = None,
                  history_summary: str | None = None,
                  packet=None, use_cache: bool = True,
                  extra_items: list[ContextItem] | None = None) -> CompiledContext:
    """Plan, provide and compile the context for one agent call.

    The planner decides the sections and their budgets, providers supply the
    candidates (skill contract, tool catalogue), the compiler dedups, ranks and
    compiles them, and the trace records both what was loaded and what was
    deliberately withheld. ``history`` / ``history_summary`` carry the
    conversation state persisted by ``conversation_summaries`` (P3); ``packet``
    carries a TaskPacket handed over by a previous agent (P3).

    With ``use_cache`` the compiled prompt is cached under a version-bound key
    (prompt content + skill/reference/registry versions + inputs); a cache hit
    replays the already-traced content and records no duplicate trace row.
    """
    if role not in ROLES:
        raise ValueError(f'Unknown prompt role: {role}')
    if custom is None:
        custom = get_profile(role)['custom_prompt']

    agent = ROLE_META[role]['name']
    task_type = task_type or ('extract' if role == 'extractor' else 'chat')
    requested_refs = list(references or []) if include_reference else []

    key = None
    if use_cache:
        key = context_cache_key(
            role=role, task_type=task_type, references=requested_refs, tools=tools,
            custom=custom, history=history, history_summary=history_summary,
            packet=packet, skill=ROLE_META[role].get('skill'),
            include_reference=include_reference,
            # Payload the caller injected. Only the extraction prefetch path uses this,
            # and its content is chunk-derived, so it belongs in the key.
            extra=[i.content for i in (extra_items or [])])
        hit = cache_get(key)
        if hit is not None:
            return CompiledContext(agent=hit['agent'], plan=None,
                                   system=hit['system'], context=hit['context'],
                                   user=hit.get('user'), cached_trace=hit.get('trace'))

    task = TaskContext(
        task_type=task_type,
        agent=agent,
        skill=ROLE_META[role].get('skill'),
        features=features_for(agent, task_type, references=requested_refs),
        history=list(history or []),
        history_summary=history_summary,
    )
    plan = PLANNER.plan(task)
    items = _filter_tool_item(list(REGISTRY.collect(task, plan)), tools)
    # Caller-injected context (Step 15 extraction prefetch). It is compiled like any other
    # item, so it is ranked, deduped and traced by the same code path — and a duplicate of
    # something already loaded is dropped here rather than reaching the model twice.
    if extra_items:
        items.extend(extra_items)
    items.append(ContextItem(
        id='bootstrap', type=TYPE_BOOTSTRAP, content=CORE_PROMPTS[role],
        source='prompt_profiles.CORE_PROMPTS', priority=1.0, relevance=1.0,
        information_gain=0.7, required=True, reason='agent identity and grounding rules'))
    if custom:
        items.append(ContextItem(
            id='custom', type=TYPE_CUSTOM, content=f'[USER-CUSTOM INSTRUCTIONS]\n{custom}',
            source='agent_prompt_profiles.custom_prompt', priority=0.9, relevance=1.0,
            information_gain=0.6, required=True, reason='user instructions'))
    if packet is not None:
        from .context.packet import TaskPacket
        p = packet if isinstance(packet, TaskPacket) else TaskPacket.from_dict(packet)
        items.append(ContextItem(
            id=f"task-packet:{p.task_id}", type=TYPE_TASK,
            content=p.context_block(), source=f"task_packets/{p.task_id}",
            priority=1.0, relevance=1.0, information_gain=0.9, evidence_strength=0.5,
            required=True,
            reason='state handed over by the previous agent (Task Packet)'))
    items.append(ContextItem(
        id='constraints', type=TYPE_CONSTRAINTS, content=NON_NEGOTIABLE,
        source='prompt_profiles.NON_NEGOTIABLE', priority=1.0, relevance=1.0,
        information_gain=0.5, required=True, reason='non-negotiable contract'))

    compiled = COMPILER.compile(items, plan)
    if persist:
        record_context(compiled)  # one context_runs row per LLM call, best effort
    if key:
        cache_put(key, agent, {'agent': agent, 'system': compiled.system,
                               'context': compiled.context, 'user': compiled.user or '',
                               'trace': compiled.to_trace()})
    return compiled


def composition_preview(role: str) -> str:
    """Render the effective prompt without recording a trace (preview path)."""
    return build_context(role, include_reference=False).render()


def compose_prompt(role: str, *, include_reference: bool = True, references: list[str] | None = None,
                   tools=None, history: list[dict] | None = None,
                   history_summary: str | None = None,
                   packet=None, extra_items: list[ContextItem] | None = None) -> str:
    """Build the system prompt for one LLM call and record its Context Trace."""
    compiled = build_context(role, include_reference=include_reference, references=references,
                             tools=tools, persist=True, history=history,
                             history_summary=history_summary, packet=packet,
                             extra_items=extra_items)
    return compiled.render()
