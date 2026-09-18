"""Step 17 — Skill minimality: replay A/B between the `current` and `compact` contracts.

What this file can prove, and what it cannot:

- **Replay** feeds the *recorded* model outputs (the Step 15.1 live run) through the real
  compile → persist pipeline. Because the model is not called, the two arms cannot differ by
  model variance. What replay proves is that the pipeline is *skill-agnostic after the model*:
  the same recorded envelope becomes the same knowledge under either contract, so a future
  semantic difference cannot be blamed on the compiler.
- **Replay cannot prove the model is still right** under the shorter contract — the output is
  fixed. That is the job of the live canary (`scripts/step17_skill_ab.py`). This file asserts
  the *preconditions* the canary relies on: the compact contract exists, is strictly smaller,
  still states every semantic clause the contract depends on, and leaks no registry dump or
  runtime tool name into the prompt.

0 tokens: nothing here touches a provider.
"""
from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest

from app import config, db, service
from app.agents import extraction_agent as ea
from app.context import estimate_tokens
from app.context.cache import context_cache_key
from app.context.extraction_context import plan_extraction_context
from app.db import loads
from app.domain.entity_eligibility import get_entity_eligibility
from app.extraction_items import compile_extraction_items
from app.knowledge import persist_extraction
from app.prompt_profiles import build_context
from app.repositories import ClaimRepository, EntityRepository
from app.skills import (configured_variant, load_skill, read_skill_contract, skill_source,
                        skill_version)

FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'extraction_cases'
CASES_PATH = FIXTURE_DIR / 'micro_cases.json'
LAST_RUN_PATH = FIXTURE_DIR / 'live_last_run.json'

SKILL = 'knowledge-extraction'
CURRENT = 'current'
COMPACT = 'compact'
SKILL_MARKER = '[SKILL]'

# §4: every clause the extraction contract depends on. Absence is a defect, not a style choice.
REQUIRED_CLAUSES = [
    'entity', 'concept', 'literal', 'unknown',        # object kinds, defined
    'stable-identity',                                # Entity definition
    'predicate', 'closed vocabulary',                 # registry-only closure
    'temporal_signal',                                # temporal placement
    'object_kind',                                    # the verdict field
    'evidence', 'verbatim',                           # evidence contract
    'precision', 'omit',                              # precision / abstention
]

# Names that must NOT appear: runtime machinery, or the registry's own catalogue.
FORBIDDEN_IN_COMPACT = ['read_skill_reference', 'GenerateStructuredOutput', 'list_skills',
                        'Claim Predicate Registry', 'Entity Type Standard']

ENTITY_TYPES = ['Person', 'Organization', 'Product', 'Software', 'Technology', 'Method',
                'Concept', 'Theory', 'Dataset', 'Model', 'Standard', 'Protocol', 'Resource',
                'Location']
# Registry-specific predicate spellings: multi-word/underscore forms and `has_ceo`. The
# single English verbs (`supports`, `uses`, ...) are excluded on purpose — they are ordinary
# prose words and a substring hit on them would be a false positive, not a catalogue leak.
CATALOGUE_PREDICATES = ['defined_as', 'classified_as', 'consists_of', 'leads_to', 'depends_on',
                        'based_on', 'derived_from', 'trained_on', 'evaluated_on', 'tested_on',
                        'compares_with', 'better_than', 'worse_than', 'designed_for', 'used_for',
                        'applied_to', 'has_ceo']

PAYLOAD = '[CHUNK c1]\n苹果现任 CEO 是 John Ternus。'


def _cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding='utf-8'))['live_cases']


def _recorded() -> dict[str, dict]:
    """Real model output per case, captured by the Step 15.1 run (held constant across arms)."""
    run = json.loads(LAST_RUN_PATH.read_text(encoding='utf-8'))
    return {c['id']: c['raw_envelope'] for c in run['cases']}


def _business_view(doc, outcome) -> dict:
    entities = EntityRepository()
    entity_view = {}
    for entity in outcome.envelope['entities']:
        row = entities.by_name(entity['name'])
        if not row:
            entity_view[entity['name']] = None
            continue
        full = entities.get(row['id'])
        entity_view[entity['name']] = {
            'types': sorted(loads(full['types_json'], [])),
            'eligibility': get_entity_eligibility(full['properties']).value,
            'status': full['status'],
        }
    claims = sorted(
        (c.get('subject'), c.get('predicate'), c.get('object'), c.get('polarity'),
         c.get('temporal_signal'), c.get('object_kind'), (c.get('evidence_quote') or '')[:60])
        for c in outcome.envelope['claims'])
    return {'status': outcome.status, 'claims': claims, 'entities': entity_view,
            'stored_claims': len(ClaimRepository().for_document(doc)),
            'rejected': sorted(r.error_code for r in outcome.failures)}


def _drive_recorded(envelope: dict) -> dict:
    marker = uuid.uuid4().hex[:8]
    content = f'step17 replay {marker}'
    doc = service.create_document(title=f'step17 {marker}', content=content, source_type='note')
    outcome = compile_extraction_items(copy.deepcopy(envelope))
    with db.transaction() as conn:
        persist_extraction(conn, document_id=doc, extraction=outcome.envelope)
    return _business_view(doc, outcome)


def _compose(variant: str):
    """The production prefetch composition for one payload, under one variant.

    Uses the same input builder ``build_extraction_agent`` uses
    (``extraction_prompt_inputs``), so the measured prompt is the one a call would send.
    """
    inputs = ea.extraction_prompt_inputs(PAYLOAD)
    compiled = build_context('extractor', include_reference=True,
                             references=inputs['references'], tools=inputs['tools'],
                             extra_items=inputs['extra_items'], persist=False, use_cache=False)
    return compiled


@pytest.fixture(autouse=True)
def _ready():
    db.init_db()
    yield


@pytest.fixture
def variant(monkeypatch):
    def _set(name: str):
        monkeypatch.setitem(config._OVERRIDES, 'extraction_skill_variant', name)
    return _set


# --------------------------------------------------------------------- §9 variants

def test_default_variant_is_current():
    assert config.DEFAULTS['extraction_skill_variant'] == 'current'
    assert configured_variant(SKILL) is None
    assert skill_source(SKILL) == 'skills/knowledge-extraction/SKILL.md'


def test_compact_variant_is_strictly_smaller():
    current = read_skill_contract(SKILL)
    compact = read_skill_contract(SKILL, variant=COMPACT)
    assert len(compact) < len(current)
    assert estimate_tokens(compact) < estimate_tokens(current)
    # A floor, so "smaller" is never reached by emptying the contract.
    assert estimate_tokens(compact) >= 200, estimate_tokens(compact)


def test_compact_keeps_every_contract_clause():
    compact = read_skill_contract(SKILL, variant=COMPACT).lower()
    missing = [clause for clause in REQUIRED_CLAUSES if clause.lower() not in compact]
    assert not missing, f'compact dropped contract clauses: {missing}'


def test_compact_drops_registry_dump_and_runtime_names():
    compact = read_skill_contract(SKILL, variant=COMPACT)
    for banned in FORBIDDEN_IN_COMPACT:
        assert banned not in compact, f'compact leaks {banned!r}'
    # It may name `Concept` (it defines the object kind) but must not enumerate the 14 types.
    types_named = [t for t in ENTITY_TYPES if t in compact]
    assert types_named == ['Concept'], types_named
    # It must not carry the predicate catalogue; the Reference Context owns it.
    predicates_named = [p for p in CATALOGUE_PREDICATES if p in compact]
    assert predicates_named == [], predicates_named


def test_current_skill_is_the_one_that_carries_the_catalogue():
    """The contrast that makes the previous test meaningful, not vacuous."""
    current = read_skill_contract(SKILL)
    assert 'has_ceo' in current, 'current lost its predicate example'
    assert len([p for p in CATALOGUE_PREDICATES if p in current]) >= 1


def test_switching_variant_changes_source_and_version(variant):
    variant(COMPACT)
    assert configured_variant(SKILL) == COMPACT
    assert skill_source(SKILL) == 'skills/knowledge-extraction/SKILL_COMPACT.md'
    assert skill_version(SKILL) != skill_version(SKILL, variant=CURRENT)
    assert load_skill(SKILL) == read_skill_contract(SKILL, variant=COMPACT)


def test_unknown_variant_falls_back_to_current(variant):
    variant('typo-not-a-variant')
    assert configured_variant(SKILL) is None
    assert skill_source(SKILL) == 'skills/knowledge-extraction/SKILL.md'


def test_cache_key_is_variant_bound(variant):
    """Switching the contract must not replay the other contract's cached prompt."""
    kwargs = dict(role='extractor', task_type='extract', references=[], tools=[],
                  custom='', history=None, history_summary=None, packet=None, skill=SKILL,
                  include_reference=True, extra=None)
    variant(CURRENT)
    current_key = context_cache_key(**kwargs)
    variant(COMPACT)
    compact_key = context_cache_key(**kwargs)
    assert current_key != compact_key


def test_composed_prompt_shrinks_by_the_skill_delta(variant):
    variant(CURRENT)
    current = _compose(CURRENT)
    variant(COMPACT)
    compact = _compose(COMPACT)

    delta = (estimate_tokens(read_skill_contract(SKILL, variant=CURRENT))
             - estimate_tokens(read_skill_contract(SKILL, variant=COMPACT)))
    assert delta > 0
    assert current.rendered_tokens - compact.rendered_tokens == delta
    assert SKILL_MARKER in compact.render()
    assert read_skill_contract(SKILL, variant=COMPACT) in compact.render()
    assert read_skill_contract(SKILL, variant=CURRENT) not in compact.render()


# --------------------------------------------------------------------- §10 replay A/B

def test_the_eleven_recorded_cases_compile_identically_under_both_variants(variant):
    """CURRENT == COMPACT at the knowledge level, for all 11 cases.

    The recorded envelope is held constant; the only thing that changes is which contract
    the prompt would carry. Identical outcomes prove no skill text leaks into validation,
    so a canary difference can only come from the model, never the pipeline.
    """
    recorded = _recorded()
    cases = _cases()
    assert len(cases) == 11, f'expected 11 contract cases, found {len(cases)}'

    differences: dict[str, dict] = {}
    for case in cases:
        envelope = recorded[case['id']]
        variant(CURRENT)
        current = _drive_recorded(envelope)
        variant(COMPACT)
        compact = _drive_recorded(envelope)
        if current != compact:
            differences[case['id']] = {'current': current, 'compact': compact}
    assert not differences, json.dumps(differences, ensure_ascii=False, indent=2)[:2000]


def test_replay_arm_carries_the_compact_contract(variant):
    """The A/B is not two identical prompts: each arm really composes its own contract."""
    variant(COMPACT)
    compact = _compose(COMPACT).render()
    variant(CURRENT)
    current = _compose(CURRENT).render()
    assert compact != current
    assert read_skill_contract(SKILL, variant=COMPACT) in compact
    assert read_skill_contract(SKILL) in current
