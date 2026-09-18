"""Step 16 — does the Detection pass earn its provider call?

Replay A/B, 0 tokens. The same recorded extraction output (the real model output captured
in the Step 15.1 run) is fed through the real pipeline twice — once with Pass 1 detection
ON, once with it OFF — and the two results are compared at the level the business cares
about, not as JSON blobs.

Three arms, because "Detection" is not one thing:

    OFF          no detection call; extraction sees the plain payload
    ON-perfect   detection names exactly the kinds the output really contains — the best
                 detector that could exist for these cases
    ON-lossy     detection misses a kind the output really contains — the filtering risk
                 detection also carries, which the cost argument never mentions

The recorded output is deliberately held constant across arms. Any difference between arms
is therefore attributable to the detection plumbing (skip / scope hint / ``_restrict``), not
to model variance — which is what a live A/B could not isolate.
"""
from __future__ import annotations

import asyncio
import copy
import json
import uuid
from pathlib import Path

import pytest

from app import config, db, llm, service
from app.db import loads
from app.domain.entity_eligibility import get_entity_eligibility
from app.extraction_items import compile_extraction_items
from app.knowledge import persist_extraction
from app.repositories import ClaimRepository, EntityRepository

FIXTURE_DIR = Path(__file__).parent / 'fixtures' / 'extraction_cases'
CASES_PATH = FIXTURE_DIR / 'micro_cases.json'
LAST_RUN = FIXTURE_DIR / 'live_last_run.json'

OFF = 'off'
ON = 'on'


def _cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding='utf-8'))['live_cases']


def _recorded() -> dict[str, dict]:
    """Real model output per case, captured by the Step 15.1 live run."""
    run = json.loads(LAST_RUN.read_text(encoding='utf-8'))
    return {c['id']: c['raw_envelope'] for c in run['cases']}


def _non_empty_kinds(envelope: dict) -> dict:
    return {kind: bool(envelope.get(kind)) for kind in llm.KIND_KEYS}


class _FakeDetector:
    """Stands in for Pass 1 and counts how often it was asked."""

    def __init__(self, result: dict | None):
        self.result = result
        self.calls = 0

    async def __call__(self, payload: str) -> dict:
        self.calls += 1
        return dict(self.result or {})


class _FakeExtractor:
    """Replays one recorded output, and remembers what it was asked to extract."""

    def __init__(self, envelope: dict):
        self.envelope = envelope
        self.payloads: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.payloads)

    async def __call__(self, payload: str) -> dict:
        self.payloads.append(payload)
        return copy.deepcopy(self.envelope)


def _drive(monkeypatch, case: dict, mode: str, detection: dict | None, envelope: dict) -> dict:
    """One case through the real ``llm.extract`` control flow, with stubbed providers."""
    marker = uuid.uuid4().hex[:8]
    content = f'{case["text"]} [{marker}]'
    doc = service.create_document(title=f'detect-ab {marker}', content=content,
                                  source_type='note')
    chunk = service.write_chunks(doc, content)[0]

    detector = _FakeDetector(detection)
    extractor = _FakeExtractor(envelope)
    monkeypatch.setattr(llm, 'detect_structured', detector)
    monkeypatch.setattr(llm, 'extract_structured', extractor)
    monkeypatch.setattr(config, '_OVERRIDES',
                        {**config._OVERRIDES, 'extraction_detection_mode': mode})

    raw = asyncio.run(llm.extract([{'id': chunk['id'], 'content': content}]))
    outcome = compile_extraction_items(raw)
    with db.transaction() as conn:
        counts = persist_extraction(conn, document_id=doc, extraction=outcome.envelope)
    return {'doc': doc, 'outcome': outcome, 'counts': counts, 'raw': raw,
            'detector': detector, 'extractor': extractor}


def _business_view(result: dict) -> dict:
    """What the pipeline decided, expressed as knowledge rather than as JSON shape.

    Claim key is the canonical identity (subject, predicate, object, polarity, temporal);
    object_kind and evidence travel with it; entities carry their types AND the eligibility
    verdict persistence gave them.
    """
    outcome, counts = result['outcome'], result['counts']
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
    return {
        'document_status': outcome.status,
        'claims': claims,
        'entities': entity_view,
        'stored_claims': len(ClaimRepository().for_document(result['doc'])),
        'imprecise_quotes': counts.get('imprecise_quotes', 0),
        'rejected': sorted(r.error_code for r in outcome.failures),
    }


@pytest.fixture(autouse=True)
def _ready():
    db.init_db()
    yield


# ------------------------------------------------------------------- §20 the A/B

def test_off_makes_no_detection_provider_call(monkeypatch):
    """§12: OFF must bypass the provider, not call it and ignore the answer."""
    case = _cases()[0]
    envelope = _recorded()[case['id']]
    result = _drive(monkeypatch, case, OFF, None, envelope)
    assert result['detector'].calls == 0
    assert result['extractor'].calls == 1


def test_on_makes_exactly_one_detection_call(monkeypatch):
    case = _cases()[0]
    envelope = _recorded()[case['id']]
    result = _drive(monkeypatch, case, ON, _non_empty_kinds(envelope), envelope)
    assert result['detector'].calls == 1
    assert result['extractor'].calls == 1


def test_on_and_off_reach_the_same_knowledge(monkeypatch):
    """The core question, all 11 cases: does a *perfect* detection change anything?"""
    recorded = _recorded()
    differences = {}
    for case in _cases():
        envelope = recorded[case['id']]
        off = _drive(monkeypatch, case, OFF, None, envelope)
        on = _drive(monkeypatch, case, ON, _non_empty_kinds(envelope), envelope)
        off_view, on_view = _business_view(off), _business_view(on)
        if off_view != on_view:
            differences[case['id']] = {'off': off_view, 'on': on_view}
    assert not differences, json.dumps(differences, ensure_ascii=False, indent=2)[:2000]


def test_the_skip_path_only_fires_where_the_output_was_already_empty(monkeypatch):
    """The one place detection can save money: a batch it judges entirely empty.

    With a *perfect* detector, that is exactly the set of cases whose real extraction came
    back empty — the Step 15.1 run produced nothing for `path_source_artifact` (a location
    sentence with nothing to assert) and something for every other case, including the
    abstention one. Detection can only predict an outcome extraction already reaches.
    """
    recorded = _recorded()
    empty = sorted(cid for cid, env in recorded.items()
                   if not any(env.get(kind) for kind in llm.KIND_KEYS))
    skipped, extracted = [], []
    for case in _cases():
        envelope = recorded[case['id']]
        result = _drive(monkeypatch, case, ON, _non_empty_kinds(envelope), envelope)
        (skipped if result['extractor'].calls == 0 else extracted).append(case['id'])
    assert sorted(skipped) == empty, {'skipped': skipped, 'already_empty': empty}
    # And skipping must not change what was stored: it saves a call, not knowledge.
    for case in _cases():
        if case['id'] not in empty:
            continue
        envelope = recorded[case['id']]
        off = _business_view(_drive(monkeypatch, case, OFF, None, envelope))
        on = _business_view(_drive(monkeypatch, case, ON, _non_empty_kinds(envelope), envelope))
        assert off == on, case['id']


def test_a_wrong_detection_loses_extracted_knowledge(monkeypatch):
    """§8: the filter cuts both ways. ``_restrict`` erases what detection did not name."""
    recorded = _recorded()
    losses = {}
    for case in _cases():
        envelope = recorded[case['id']]
        present = [kind for kind in llm.KIND_KEYS if envelope.get(kind)]
        if len(present) < 2:
            continue                      # nothing to lose
        blinded = {kind: (value and kind != 'entities')
                   for kind, value in _non_empty_kinds(envelope).items()}
        off = _drive(monkeypatch, case, OFF, None, envelope)
        on = _drive(monkeypatch, case, ON, blinded, envelope)
        off_entities = {e['name'] for e in off['outcome'].envelope['entities']}
        on_entities = {e['name'] for e in on['outcome'].envelope['entities']}
        if off_entities != on_entities:
            losses[case['id']] = sorted(off_entities - on_entities)
    assert losses, 'expected _restrict to erase entities detection failed to name'
    print(f'  entities erased by an imperfect detection in {len(losses)}/11 cases: '
          f'{json.dumps(losses, ensure_ascii=False)[:400]}')


def test_the_scope_hint_reaches_the_prompt_and_the_planner(monkeypatch):
    """§6: the only other thing detection changes is the text extraction is given.

    The scope hint is appended to the payload, and the prefetch planner plans from that
    payload — so detection *does* reach the planner, indirectly, as instruction text. That
    is the PREFETCH_CANDIDATE_NOISE found in Step 15.1, here with its cause.
    """
    from app.context.extraction_context import plan_extraction_context
    case = next(c for c in _cases() if c['id'] == 'predicate_registry_ceo')
    envelope = _recorded()[case['id']]
    result = _drive(monkeypatch, case, ON, _non_empty_kinds(envelope), envelope)
    payload = result['extractor'].payloads[0]
    assert '[PASS 2 SCOPE]' in payload
    off_plan = plan_extraction_context(case['text'])
    on_plan = plan_extraction_context(payload)
    assert set(on_plan.predicate_candidates) - set(off_plan.predicate_candidates) == {'extracts'}
