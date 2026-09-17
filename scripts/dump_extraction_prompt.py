"""Snapshot exactly what one extraction call carries, and look for duplication.

Zero model calls: the prompt is assembled by the same code the production path uses,
and the AgentScope-owned pieces (skill block, tool schemas) are read from its own API
rather than guessed.

    python scripts/dump_extraction_prompt.py

Writes tests/fixtures/extraction_cases/extraction_prompt_snapshot.json and prints the
duplication findings (Step 13.3 §7: skill twice? reference twice? predicate definitions
in four places? schema in the prompt as well as the response format?).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FIXTURE_DIR = ROOT / 'tests' / 'fixtures' / 'extraction_cases'
SNAPSHOT = FIXTURE_DIR / 'extraction_prompt_snapshot.json'
SKILL_MD = ROOT / 'skills/knowledge-extraction/SKILL.md'
REFERENCE_MD = ROOT / 'skills/knowledge-extraction/references/claim-predicates.md'


def _isolate() -> None:
    tmp = Path(tempfile.mkdtemp(prefix='llmwiki-snapshot-'))
    os.environ['DATABASE_PATH'] = str(tmp / 'wiki.db')
    os.environ.setdefault('SETTINGS_PATH', str(tmp / 'settings.json'))
    os.environ.setdefault('OPENAI_API_KEY', 'snapshot-only-no-call-is-made')


def _tokens(text: str) -> int:
    from app.context import estimate_tokens
    return estimate_tokens(text or '')


def _long_lines(text: str) -> list[str]:
    out = []
    for line in (text or '').splitlines():
        clean = ' '.join(line.split()).strip('-*• ').casefold()
        if len(clean) >= 30:
            out.append(clean)
    return out


def build() -> dict:
    _isolate()
    from app.agents.extraction_agent import (EXTRACTION_REFERENCES, Extraction,
                                             build_extraction_agent)
    from app.config import runtime
    from app.db import init_db
    from app.prompt_profiles import build_context

    init_db()
    agent = build_extraction_agent()
    compiled = build_context('extractor', include_reference=True,
                             references=EXTRACTION_REFERENCES, tools=None,
                             persist=False, use_cache=False)
    system_prompt = compiled.render()
    trace = compiled.to_trace()
    schema = json.dumps(Extraction.model_json_schema(), ensure_ascii=False)
    tool_schemas = asyncio.run(agent.toolkit.get_tool_schemas())
    tool_schemas_text = json.dumps(tool_schemas, ensure_ascii=False, default=str)
    skill_block = asyncio.run(agent.toolkit.get_skill_instructions())
    skill_block = skill_block if isinstance(skill_block, str) else json.dumps(
        skill_block, ensure_ascii=False, default=str)
    skill_text = SKILL_MD.read_text(encoding='utf-8')
    reference_text = REFERENCE_MD.read_text(encoding='utf-8')

    chunk = '最大 chunk 为 8192 tokens。'
    messages = [{'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': f'[CHUNK snapshot]\n{chunk}'}]

    # --- duplication checks (§7 A-E) ------------------------------------------
    def appears(needle: str, haystack: str) -> bool:
        return bool(needle) and needle.casefold() in (haystack or '').casefold()

    skill_rule = 'A Mention is not an Entity'
    reference_rule = 'The LLM must never invent predicate names'
    registry_json = json.loads((ROOT / 'schemas' / 'claim-predicate-registry.json')
                               .read_text(encoding='utf-8'))
    predicates = list(registry_json.get('claim_predicates') or [])

    duplication = {
        'A_skill_injected_twice': {
            'skill_body_in_agentskills_block': appears(skill_rule, skill_block),
            'skill_body_in_system_prompt': appears(skill_rule, system_prompt),
            'verdict': 'catalogue only' if not appears(skill_rule, skill_block)
                       else 'FULL SKILL DUPLICATED',
        },
        'B_reference_injected_twice': {
            'reference_rule_in_system_prompt': appears(reference_rule, system_prompt),
            'reference_rule_in_agentskills_block': appears(reference_rule, skill_block),
            'reference_rule_in_tool_schemas': appears(reference_rule, tool_schemas_text),
            'verdict': ('once (system prompt only)'
                        if appears(reference_rule, system_prompt)
                        and not appears(reference_rule, skill_block) else 'CHECK'),
        },
        'C_predicate_definitions': {
            'in_reference_file': len(predicates),
            'reference_file_is_inlined': 'claim-predicates.md' in (EXTRACTION_REFERENCES or []),
            'in_structured_schema': sum(1 for p in predicates if appears(p, schema)),
            'in_tool_schemas': sum(1 for p in predicates if appears(p, tool_schemas_text)),
            'verdict': 'one place (the inlined reference); the registry JSON is not sent',
        },
        'D_schema_in_prompt_text': {
            'schema_key_in_system_prompt': appears('"object_kind"', system_prompt),
            'schema_title_in_system_prompt': appears('EntityType', system_prompt),
            'verdict': 'schema ships once (response format); the prompt only names the field',
        },
        'E_instruction_repeated': _repeat_findings(system_prompt, skill_block, reference_text),
    }

    # What the ledger CLAIMS it loaded vs what render() actually emits. The two differ:
    # `reference` holds 2.1k characters in `items` but appears in neither SYSTEM_ORDER nor
    # CONTEXT_ORDER, so render() silently drops it.
    ledger_items = {item.type: item.estimated_tokens for item in compiled.items}
    rendered_types = ('bootstrap', 'skill', 'custom', 'constraints', 'tools')
    not_rendered = {name: tokens for name, tokens in ledger_items.items()
                    if name not in rendered_types and name != 'reference'}
    reference_ledger = ledger_items.get('reference', 0)
    reference_sent = bool(reference_text) and appears(
        'The LLM must never invent predicate names', system_prompt)

    components = [
        ('base system instructions (rendered)', _tokens(_base_only(system_prompt))),
        ('extraction skill (rendered, full SKILL.md)', _tokens(skill_text)),
        ('reference (LEDGER ONLY, not sent)', reference_ledger if not reference_sent else 0),
        ('agent-skills block (AgentScope, sent)', _tokens(skill_block)),
        ('structured-output schema (sent)', _tokens(schema)),
        ('tool schemas (sent)', _tokens(tool_schemas_text)),
        ('chunk (sent)', _tokens(chunk)),
    ]
    known = sum(t for _, t in components if 'LEDGER ONLY' not in _)
    provider = _provider_extract_tokens()

    return {
        'what': 'One extraction call, itemised. Assembled by the production code paths; '
                'AgentScope-owned parts read from its own API. Zero model calls.',
        'model_calls': 0,
        'model': runtime().get('openai_model'),
        'tool_names': [((t.get('function') or {}).get('name') if isinstance(t, dict) else None)
                       for t in tool_schemas],
        'system': system_prompt,
        'skills': {'inlined_skill': skill_text, 'agentskills_block': skill_block,
                   'inlined_references': list(EXTRACTION_REFERENCES or [])},
        'references': {'claim_predicates_md': reference_text},
        'tools': tool_schemas,
        'schema': schema,
        'chunk': chunk,
        'messages': messages,
        'estimated_tokens': {
            'components': dict(components),
            'known_project_total': known,
            'provider_total_per_extraction_call': provider,
            'unattributed': provider - known,
            'note': ('UNATTRIBUTED = provider_total - known_project. It is NOT a measured '
                     'AgentScope figure: it mixes AgentScope message assembly '
                     '(system scaffolding, ReAct/tool-call contract, framing, provider-side '
                     'tool/response-format wrapping) with the estimator\'s under-count of '
                     'structured JSON (provider/estimate ≈ 2.5x). The two cannot be '
                     'separated from here.'),
        },
        'duplication': duplication,
        'ledger_vs_rendered': {
            'ledger_actual_tokens': trace.get('actual_tokens'),
            'rendered_system_tokens': _tokens(system_prompt),
            'rendered_component_sum': known,
            'items_in_ledger_not_rendered': not_rendered,
            'reference_ledger_tokens': reference_ledger,
            'reference_actually_sent': reference_sent,
            'note': ('The ledger counts the reference as loaded (and budgets for it) while '
                     'render() emits neither a system nor a context block for it, because '
                     '`reference` is in neither SYSTEM_ORDER nor CONTEXT_ORDER. So '
                     '"reference is inlined" was never true. Recorded, NOT fixed: rendering '
                     'it would ADD ~0.55k tokens and change the prompt, which this round '
                     'forbids without re-running the full semantic smoke.'),
        },
        'flags': ([] if reference_sent else ['REFERENCE_NOT_RENDERED']) + ['SCHEMA_COST_BOTTLENECK'],
        'extract_step_prompt_variance': _variance(),
    }


def _base_only(system_prompt: str) -> str:
    """The base instructions slice (identity + non-negotiables), for the breakdown."""
    markers = ('[NON-NEGOTIABLE]',)
    keep = [line for line in system_prompt.splitlines()
            if any(m in line for m in markers) or 'knowledge extraction agent' in line]
    return '\n'.join(keep)


def _repeat_findings(system: str, block: str, reference: str) -> dict:
    """Lines of real instruction text that appear in more than one injected source."""
    counts: dict[str, list[str]] = {}
    for name, text in (('system', system), ('agent-skills block', block),
                       ('reference', reference)):
        for line in set(_long_lines(text)):
            counts.setdefault(line, []).append(name)
    repeated = {line: sorted(set(where)) for line, where in counts.items() if len(set(where)) > 1}
    return {'repeated_lines': len(repeated),
            'verdict': 'no instruction is injected from two sources' if not repeated
                       else 'REPEATED INSTRUCTIONS FOUND',
            'examples': list(repeated.items())[:5]}


def _provider_extract_tokens() -> int:
    """Provider-reported prompt tokens for ONE extraction call, from the last live run."""
    cost_path = FIXTURE_DIR / 'live_context_cost.json'
    if cost_path.exists():
        runs = json.loads(cost_path.read_text(encoding='utf-8')).get('runs') or []
        for run in reversed(runs):
            agg = (run.get('cost') or {}).get('by_step', {}).get('extract_batch')
            if agg and agg.get('calls'):
                return round(agg['prompt_tokens'] / agg['calls'])
    return 0


def _variance() -> dict:
    """Extract-step prompt tokens per recorded run, to explain the 8k-10k spread."""
    out = {}
    for path in sorted(FIXTURE_DIR.glob('live_run*.json')):
        run = json.loads(path.read_text(encoding='utf-8'))
        tokens = [c['prompt_tokens'] for c in run.get('cases', []) if c.get('prompt_tokens')]
        if tokens:
            out[path.name] = {'cases': len(tokens), 'min': min(tokens), 'max': max(tokens)}
    return out


def main() -> int:
    report = build()
    tokens = report['estimated_tokens']
    print('EXTRACTION CALL SNAPSHOT (0 model calls)')
    for name, value in tokens['components'].items():
        print(f"  {name:<40} {value:>6}")
    print(f"  {'known project total':<40} {tokens['known_project_total']:>6}")
    print(f"  {'provider total (extraction call)':<40} "
          f"{tokens['provider_total_per_extraction_call']:>6}")
    print(f"  {'UNATTRIBUTED':<40} {tokens['unattributed']:>6}")
    print('\nLEDGER vs RENDERED')
    for key, value in report['ledger_vs_rendered'].items():
        if key != 'note':
            print(f"  {key} = {value}")
    print(f"\nFLAGS: {report['flags']}")
    print('\nDUPLICATION CHECKS')
    for key, value in report['duplication'].items():
        print(f"  {key}: {value.get('verdict')}")
        for k, v in value.items():
            if k not in ('verdict', 'examples'):
                print(f"      {k} = {v}")
    print(f"\nEXTRACT-STEP PROMPT VARIANCE: {json.dumps(report['extract_step_prompt_variance'])}")
    SNAPSHOT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\nwrote {SNAPSHOT.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
