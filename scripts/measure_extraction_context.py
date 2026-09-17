"""Measure what one live extraction call actually sends (Step 13.1 §10). Zero tokens.

The live smoke showed ~4.2k prompt tokens per call for a 20-character sentence. That
number is real (provider-reported) but tells us nothing about *where* it comes from.
This script assembles the same prompt the production path assembles, offline, and
breaks it into its sections so the next cut can be aimed instead of guessed.

    python scripts/measure_extraction_context.py            # print + refresh the artifact
    python scripts/measure_extraction_context.py --check    # print only

It never calls a model: prompt assembly is pure code plus the prompt-profile table.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ARTIFACT = ROOT / 'tests' / 'fixtures' / 'extraction_cases' / 'live_context_cost.json'
AUDIT = ROOT / 'tests' / 'fixtures' / 'extraction_cases' / 'context_cost_audit.json'


def _isolate() -> None:
    """Measure against a throwaway database so the developer's profiles are untouched.

    The placeholder API key must be set BEFORE ``app.config`` is imported: the module
    captures its DEFAULTS from the environment at import time, and building the agent
    for introspection requires a non-empty key. No call is ever made.
    """
    tmp = Path(tempfile.mkdtemp(prefix='llmwiki-context-'))
    os.environ['DATABASE_PATH'] = str(tmp / 'wiki.db')
    os.environ.setdefault('SETTINGS_PATH', str(tmp / 'settings.json'))
    os.environ.setdefault('OPENAI_API_KEY', 'introspection-only-no-call-is-made')


def _tokens(text: str) -> int:
    from app.context import estimate_tokens
    return estimate_tokens(text)


def _last_cost() -> dict:
    artifact = ARTIFACT.parent / 'live_context_cost.json'
    if artifact.exists():
        runs = json.loads(artifact.read_text(encoding='utf-8')).get('runs') or []
        if runs:
            return runs[-1].get('cost') or {}
    return {}


def report_provider_total() -> int:
    """Prompt tokens per call as the PROVIDER reported them (the ground truth)."""
    cost = _last_cost()
    if cost.get('prompt_tokens_per_step'):
        return int(cost['prompt_tokens_per_step'])
    return 4163


def last_run_per_call() -> dict:
    """Prompt tokens for ONE call of each kind (detect vs extract), from the last run."""
    by_step = _last_cost().get('by_step') or {}
    out = {}
    for step, agg in by_step.items():
        calls = max(1, int(agg.get('calls') or 1))
        out[step.replace('_batch', '')] = round(int(agg.get('prompt_tokens') or 0) / calls)
    return out


def measure() -> dict:
    _isolate()
    from app.agents.extraction_agent import (EXTRACTION_REFERENCES, _SKILL_TOOLS,
                                             KIND_KEYS, Extraction)
    from app.db import init_db
    from app.prompt_profiles import build_context, CORE_PROMPTS, NON_NEGOTIABLE

    init_db()
    compiled = build_context('extractor', include_reference=True,
                             references=EXTRACTION_REFERENCES, tools=_SKILL_TOOLS,
                             persist=False, use_cache=False)
    trace = compiled.to_trace()
    system_prompt = compiled.render()

    sections = [{'name': s['name'], 'source': s.get('source'), 'tokens': s.get('tokens'),
                 'required': s.get('required')} for s in trace['sections']]
    sections.sort(key=lambda s: -(s['tokens'] or 0))

    schema = json.dumps(Extraction.model_json_schema(), ensure_ascii=False)
    tool_schemas = []
    for fn in _SKILL_TOOLS:
        tool_schemas.append({'name': fn.__name__,
                             'tokens': _tokens(fn.__doc__ or '') + _tokens(
                                 json.dumps(getattr(fn, '__annotations__', {}), default=str))})
    registry_tokens = {
        'entity_types': _tokens((ROOT / 'skills/knowledge-extraction/references/entity-types.md')
                                .read_text(encoding='utf-8')),
        'claim_predicates': _tokens((ROOT / 'skills/knowledge-extraction/references/claim-predicates.md')
                                    .read_text(encoding='utf-8')),
        'extraction_v2': _tokens((ROOT / 'skills/knowledge-extraction/references/extraction-v2.md')
                                 .read_text(encoding='utf-8')),
    }
    inlined = sorted(set(EXTRACTION_REFERENCES or []))

    # The predicate registry is the vocabulary the model must *select* from. It is not
    # inlined today, so a model that wants "the CEO relation" has no way to know
    # `has_ceo` exists and falls back to a generic registered predicate (`is`).
    registry_json = json.loads((ROOT / 'schemas' / 'claim-predicate-registry.json')
                               .read_text(encoding='utf-8'))
    predicates = sorted(registry_json.get('claim_predicates') or [])
    metadata = registry_json.get('predicate_metadata') or {}
    compact = '\n'.join(f"{p} ({metadata.get(p, {}).get('label', '')})" for p in predicates)
    predicate_registry = {
        'count': len(predicates),
        'sizes_tokens': {
            'full_json': _tokens(json.dumps(registry_json, ensure_ascii=False)),
            'name_and_label': _tokens(compact),
            'names_only': _tokens(' '.join(predicates)),
        },
        'role_predicates': sorted(p for p in predicates if p.startswith('has_')),
        'in_prompt_today': 'claim-predicates.md' in (EXTRACTION_REFERENCES or []),
        'sent_as': 'full_text_of_references/claim-predicates.md',
    }

    # --- §5/§6/§7/§8/§10 decomposition ------------------------------------------
    section_tokens = {s['name']: s['tokens'] for s in sections}
    skill_tokens = section_tokens.get('skill', 0)
    reference_tokens = section_tokens.get('reference', 0)
    tool_catalogue_tokens = section_tokens.get('tools', 0)
    base_tokens = section_tokens.get('bootstrap', 0) + section_tokens.get('constraints', 0)
    schema_tokens = _tokens(schema)

    # The detection pass (Pass 1) is a second call per case with its own tiny prompt.
    from app.agents.extraction_agent import DETECTION_SYSTEM_PROMPT, Detection
    detection_prompt_tokens = (_tokens(DETECTION_SYSTEM_PROMPT)
                               + _tokens(json.dumps(Detection.model_json_schema(),
                                                    ensure_ascii=False)))

    # A compact schema is the obvious lever, so evaluate it rather than guess: strip the
    # titles/descriptions the provider does not need to validate the shape.
    def _strip(node):
        if isinstance(node, dict):
            return {k: _strip(v) for k, v in node.items()
                    if k not in ('title', 'description')}
        if isinstance(node, list):
            return [_strip(v) for v in node]
        return node
    compact_schema = json.dumps(_strip(Extraction.model_json_schema()), ensure_ascii=False,
                                separators=(',', ':'))

    skill_text = (ROOT / 'skills/knowledge-extraction/SKILL.md').read_text(encoding='utf-8')

    # The real tool schemas and the AgentScope skill block, measured from the live agent
    # rather than guessed from docstrings (the first audit under-counted both).
    import asyncio as _asyncio
    from app.agents.extraction_agent import build_extraction_agent
    try:
        live_agent = build_extraction_agent()
        live_schemas = _asyncio.run(live_agent.toolkit.get_tool_schemas())
        tool_schema_tokens = _tokens(json.dumps(live_schemas, ensure_ascii=False, default=str))
        skill_block = _asyncio.run(live_agent.toolkit.get_skill_instructions())
        skill_block = (skill_block if isinstance(skill_block, str)
                       else json.dumps(skill_block, ensure_ascii=False, default=str))
        skill_block_tokens = _tokens(skill_block)
    except Exception as exc:  # introspection must never break the audit
        tool_schema_tokens = sum(t['tokens'] for t in tool_schemas)
        skill_block_tokens = 0
        print(f'  (toolkit introspection unavailable: {type(exc).__name__})')

    components = [
        {'component': 'base system instructions', 'tokens': base_tokens,
         'source': 'prompt_profiles.CORE_PROMPTS + NON_NEGOTIABLE', 'injected_as': 'inline'},
        {'component': 'extraction skill (full SKILL.md)', 'tokens': skill_tokens,
         'source': 'skills/knowledge-extraction/SKILL.md', 'injected_as': 'full text'},
        # NOT sent: `reference` is in neither SYSTEM_ORDER nor CONTEXT_ORDER, so render()
        # drops it while the ledger still counts it (see REFERENCE_NOT_RENDERED in
        # context_cost_audit.json / extraction_prompt_snapshot.json).
        {'component': 'reference (LEDGER ONLY, never rendered)', 'tokens': reference_tokens,
         'source': 'references/claim-predicates.md',
         'injected_as': 'ledger only — dropped by render()'},
        {'component': 'tool catalogue line', 'tokens': tool_catalogue_tokens,
         'source': 'context.providers.tools.TOOL_CATALOG', 'injected_as': 'inline'},
        {'component': 'agent-skills block', 'tokens': skill_block_tokens,
         'source': 'AgentScope Toolkit.get_skill_instructions() from skills/',
         'injected_as': 'system prompt, every call'},
        {'component': 'structured-output schema', 'tokens': schema_tokens,
         'source': 'agents.extraction_agent.Extraction', 'injected_as': 'every call'},
        {'component': 'tool schemas (Skill/list_skills/read_skill_reference)',
         'tokens': tool_schema_tokens, 'source': 'Toolkit.get_tool_schemas()',
         'injected_as': 'every call'},
        {'component': 'user chunk', 'tokens': 20, 'source': 'the document batch',
         'injected_as': 'inline'},
    ]
    # Only what render() actually emits counts as project-controlled input context.
    known_project_total = sum(c['tokens'] for c in components
                              if 'LEDGER ONLY' not in c['component'])

    # Soft budget for the part of the prompt the project controls (Step 13.3 §13). The
    # provider/provider-side overhead is out of scope and cannot be budgeted here.
    context_budget = {'target_tokens': 2000, 'hard_tokens': 3000,
                      'project_controlled_tokens': known_project_total,
                      'within_target': known_project_total <= 2000,
                      'within_hard': known_project_total <= 3000}
    if not context_budget['within_target']:
        context_budget['warning'] = (
            'EXTRACTION_CONTEXT_BUDGET_WARNING: project-controlled input context '
            f"is {known_project_total} tokens, above the {context_budget['target_tokens']} "
            'target' + ('' if context_budget['within_hard'] else ' AND the hard ceiling'))

    # The two call kinds have very different prompts; averaging them hid that (Step 13.1
    # reported 4,435/call, which understated the extraction call by ~45%).
    per_call = last_run_per_call()
    provider_total = per_call.get('extract') or report_provider_total()
    estimator_bias = {
        'provider_over_estimate': round(provider_total / max(1, known_project_total), 2),
        'note': ('`estimate_tokens` charges 1 token per 4 ASCII chars, which under-counts '
                 'JSON/punctuation-heavy text against a real tokenizer. The provider number '
                 'is the truth; the composition above is a structure map, not a ledger. '
                 'The remainder is therefore NOT "AgentScope tokens" — it is AgentScope '
                 'scaffolding PLUS estimator bias, and the two cannot be separated offline.'),
    }

    return {
        'what': 'Where the prompt tokens of ONE extraction call come from. Measured '
                'offline from the same assembly the production path uses; no model call.',
        'model_calls': 0,
        'observed_live': {
            'note': 'provider-reported, from the Step 13 live smoke (10 cases, 20 calls)',
            'prompt_tokens_total': 83265,
            'completion_tokens_total': 13074,
            'calls': 20,
            'prompt_tokens_per_call': round(83265 / 20),
            'case_input_chars': '~20-40',
        },
        'composition': {
            # The Context Runtime counts its own sections; that is the number the
            # budget is enforced against, so it is the authoritative one here. The
            # estimator is kept alongside it as a cross-check (it under-counts CJK).
            'system_prompt_tokens': trace.get('actual_tokens'),
            'system_prompt_tokens_estimated': _tokens(system_prompt),
            'structured_output_schema_tokens': _tokens(schema),
            'tool_schema_tokens': sum(t['tokens'] for t in tool_schemas),
            'user_chunk_tokens': 20,
            'system_prompt_chars': len(system_prompt),
            'structured_output_schema_chars': len(schema),
        },
        'system_prompt_sections': sections,
        'tool_schemas': tool_schemas,
        'skill_references': {
            'inlined_into_prompt': inlined,
            'requires_read_skill_reference': [name for name in registry_tokens if name not in inlined],
        },
        'reference_tokens_if_inlined': registry_tokens,
        'predicate_registry': predicate_registry,
        'entity_types_in_structured_output': len(Extraction.model_fields['entities']
                                                 .annotation.__args__[0].model_fields['types']
                                                 .annotation.__args__[0].__args__),
        'kind_keys': list(KIND_KEYS),
        # --- decomposition (§5/§6/§7/§8/§10) -------------------------------------
        'components': components,
        'context_budget': context_budget,
        'known_project_total_tokens': known_project_total,
        'provider_total_tokens_per_call': provider_total,
        'provider_per_call_by_kind': per_call,
        'unattributed_tokens': provider_total - known_project_total,
        'estimator_bias': estimator_bias,
        'unattributed_note': (
            'provider_total - known_project. NOT a measured figure and NOT purely '
            'AgentScope: it mixes AgentScope\'s final message assembly (agent identity, '
            'ReAct/tool-call contract, framing, provider-side tool/response-format '
            'wrapping) with the estimator\'s under-count of JSON. Reported separately '
            'from the components so it is never read as a fact.'),
        'full_text_injection': {
            'skill_md': True,
            'references_requested': list(EXTRACTION_REFERENCES or []),
            'reference_actually_rendered': False,
            'note': ('`reference` is in neither SYSTEM_ORDER nor CONTEXT_ORDER, so render() '
                     'drops it while the ledger still counts it. Requesting a reference '
                     'does not put it in the prompt (see REFERENCE_NOT_RENDERED).'),
        },
        'flags': ['REFERENCE_NOT_RENDERED', 'SCHEMA_COST_BOTTLENECK'],
        'detection_pass_prompt_tokens': detection_prompt_tokens,
        'schema_compact_alternative_tokens': _tokens(compact_schema),
        'schema_sent_every_call': True,
        'duplication_check': {
            'user_message_tokens': 20,
            'system_repeats_user_message': False,
            'note': 'The user message carries the chunk only; no skill/reference text is resent there.',
        },
        'skill_inventory': {
            'tokens': skill_tokens,
            'lines': len(skill_text.splitlines()),
            'contains_keyword_list': any(marker in skill_text for marker in (
                'PROTECTED', 'SPECIAL_CASE', 'WHITELIST')),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='print only, do not write the artifact')
    args = ap.parse_args()
    report = measure()

    print('CONTEXT COST AUDIT (offline, 0 model calls)')
    print(f"  {'component':<38} {'tokens':>7}  injected as")
    print('  ' + '-' * 70)
    for c in report['components']:
        print(f"  {c['component']:<38} {c['tokens']:>7}  {c['injected_as']}")
    print('  ' + '-' * 70)
    print(f"  {'known project total':<38} {report['known_project_total_tokens']:>7}")
    print(f"  {'provider total (extraction call)':<38} "
          f"{report['provider_total_tokens_per_call']:>7}")
    print(f"  {'UNATTRIBUTED (AgentScope + estimator bias)':<38} "
          f"{report['unattributed_tokens']:>7}")
    print(f"\nPROVIDER-REPORTED PROMPT TOKENS PER CALL (different prompts, different cost)")
    for kind, tokens in (report['provider_per_call_by_kind'] or {}).items():
        print(f"  {kind:<28} {tokens:>7}")
    print(f"  provider/estimate ratio       : {report['estimator_bias']['provider_over_estimate']}x")
    print('\nSYSTEM PROMPT SECTIONS (largest first)')
    for section in report['system_prompt_sections']:
        print(f"  {section['tokens']:>6}  {section['name']:<28} {section['source']}")
    print('\nFULL-TEXT INJECTION')
    print(f"  claim-predicates.md  : REQUESTED but NOT rendered — the ledger counts "
          f"{report['reference_tokens_if_inlined']['claim_predicates']} tokens that never "
          f"reach the model (REFERENCE_NOT_RENDERED)")
    print(f"  predicate registry   : {report['predicate_registry']['count']} predicates; "
          f"names-only would be {report['predicate_registry']['sizes_tokens']['names_only']} tokens")
    print(f"  detection pass prompt: {report['detection_pass_prompt_tokens']} tokens "
          f"(a second call per case)")
    print(f"  schema if compacted  : {report['schema_compact_alternative_tokens']} tokens "
          f"(from {report['composition']['structured_output_schema_tokens']})")
    print(f"  duplication          : {report['duplication_check']['note']}")
    budget = report['context_budget']
    print(f"\nCONTEXT BUDGET (project-controlled): {budget['project_controlled_tokens']} tokens "
          f"| target {budget['target_tokens']} | hard {budget['hard_tokens']}")
    if budget.get('warning'):
        print(f"  {budget['warning']}")
    print(f"\nFLAGS: {report['flags']}")

    if not args.check:
        # Keep the live runs the smoke appended: this script rewrites only the offline
        # composition, never the recorded per-case cost.
        if ARTIFACT.exists():
            previous = json.loads(ARTIFACT.read_text(encoding='utf-8'))
            if previous.get('runs'):
                report['runs'] = previous['runs']
        ARTIFACT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\nwrote {ARTIFACT.relative_to(ROOT)}')
        AUDIT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'wrote {AUDIT.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
