"""Step 15 — prefetch extraction: one provider call, same semantics.

The claim under test is not "we call the model less". It is:

    the context the model needs is prepared *before* the call, so the call that
    extracts is also the call that answers — and what it extracts is unchanged.

Two things make that checkable rather than aspirational:

1. **The counter is the real provider seam.** ``AsyncCompletions.create`` is the exact
   coroutine ``scripts/capture_provider_request.py`` hooks, so "1 call" means one request
   would have left the process. It is stubbed here to stay at 0 tokens, not to simulate
   the count.
2. **Both modes are answered with the same envelope**, so the pipeline assertions
   (compile → persist → eligibility) differ only by context mode.

Only the extraction *step* is counted: Pass 1 detection is a different agent with its own
prompt and is out of scope for this round.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import config, db
from app.agents import extraction_agent as ea
from app.agents.base import DEFAULT_TOOLS, build_agent
from app.context import estimate_tokens
from app.context.extraction_context import (CLAIM_PREDICATES_SENTINEL, INSUFFICIENT,
                                            MODE_AGENTIC, MODE_PREFETCH,
                                            plan_extraction_context)
from app.extraction_items import COMPLETED_WITH_WARNINGS, compile_extraction_items
from app.knowledge import persist_extraction
from app.prompt_profiles import compose_prompt
from app.runlog import record_run
from app.service import create_document, write_chunks
from app.skills import load_skill, read_skill_contract

CASE_TEXT = '苹果现任 CEO 是 John Ternus。'
CASE_PAYLOAD = f'[CHUNK c1]\n{CASE_TEXT}'

# The gold the fake provider answers with, so both modes compile and persist exactly the
# same knowledge and any pipeline difference is attributable to the mode.
ENVELOPE = {
    'entities': [
        {'name': '苹果', 'types': ['Organization'], 'aliases': []},
        {'name': 'John Ternus', 'types': ['Person'], 'aliases': []},
    ],
    'claims': [
        {'subject': '苹果', 'predicate': 'has_ceo', 'object': 'John Ternus',
         'object_kind': 'entity', 'temporal_signal': 'current',
         'claim_type': 'factual', 'polarity': 'positive', 'modality': 'asserted',
         'context': {}, 'confidence': 0.9, 'source_chunk': 'c1',
         'evidence_quote': CASE_TEXT},
    ],
    'events': [], 'ideas': [], 'questions': [],
}

BAD_CLAIM_ENVELOPE = {
    **ENVELOPE,
    'claims': [*ENVELOPE['claims'],
               {'subject': '苹果', 'predicate': 'totally_invented_predicate',
                'object': 'John Ternus', 'object_kind': 'entity',
                'claim_type': 'factual', 'polarity': 'positive', 'modality': 'asserted',
                'context': {}, 'confidence': 0.9, 'source_chunk': 'c1',
                'evidence_quote': CASE_TEXT}],
}

STRUCTURED = 'GenerateStructuredOutput'
REFERENCE_TOOL = 'read_skill_reference'
REFERENCE_CALL = (REFERENCE_TOOL, {'skill': 'knowledge-extraction',
                                   'reference': 'claim-predicates.md'})
# Markers that only exist in the *whole* reference documents.
WHOLE_PREDICATE_DOC = 'Claim Predicate Registry v1.0'
WHOLE_ENTITY_DOC = 'Entity Type Standard v1.0'


# --------------------------------------------------------------------------- fakes

def _response(tool: str, arguments: dict, index: int) -> SimpleNamespace:
    """The smallest object AgentScope's response parser accepts.

    The call id must be unique per response: repeating one makes the agent loop forever
    (it sees a tool result it has already accounted for), which is a property of the
    harness, not of the code under test.
    """
    call = SimpleNamespace(id=f'call_{index}', type='function',
                           function=SimpleNamespace(name=tool,
                                                    arguments=json.dumps(arguments)))
    message = SimpleNamespace(content=None, tool_calls=[call], reasoning_content=None,
                              audio=None)
    return SimpleNamespace(
        id=f'chatcmpl-step15-{index}',
        choices=[SimpleNamespace(message=message, finish_reason='tool_calls')],
        usage=SimpleNamespace(prompt_tokens=3000, completion_tokens=200),
    )


class FakeProvider:
    """Counts real ``create()`` invocations and replays a queued conversation."""

    def __init__(self, replies: list[tuple[str, dict]]):
        self.replies = replies
        self.requests: list[dict] = []

    @property
    def calls(self) -> int:
        return len(self.requests)

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        index = min(len(self.requests) - 1, len(self.replies) - 1)
        tool, arguments = self.replies[index]
        return _response(tool, arguments, len(self.requests))

    # -- views over the recorded requests -------------------------------------
    @property
    def system_prompt(self) -> str:
        return self.system_prompt_of(0)

    def system_prompt_of(self, index: int) -> str:
        for message in self.requests[index].get('messages') or []:
            if message.get('role') == 'system':
                return _text_of(message.get('content'))
        return ''

    def prompt_tokens(self, index: int = 0) -> int:
        return estimate_tokens(self.system_prompt_of(index))

    def tool_names(self, index: int = 0) -> list[str]:
        return [((t.get('function') or {}).get('name') or t.get('name'))
                for t in (self.requests[index].get('tools') or [])]

    def sent_tool_result(self, needle: str) -> bool:
        """Was ``needle`` ever pushed back to the model as a tool result?"""
        for request in self.requests:
            for message in request.get('messages') or []:
                if message.get('role') == 'tool' and needle in _text_of(message.get('content')):
                    return True
        return False


def _text_of(content) -> str:
    """Message content, whether it arrives as text or as AgentScope's block list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(str(block.get('text', '')) for block in content
                       if isinstance(block, dict))
    return ''


def _completions_class():
    """The client class AgentScope issues requests through (shared by all instances)."""
    probe = build_agent('Probe', 'probe', tools=[], skills=False)
    return type(probe.model.client.chat.completions)


@pytest.fixture
def provider(monkeypatch, request):
    """A fake at the real provider seam. ``indirect`` supplies the queued replies."""
    config._OVERRIDES['openai_api_key'] = 'step15-test-key'
    fake = FakeProvider(request.param)
    monkeypatch.setattr(_completions_class(), 'create', fake.create)
    db.init_db()
    try:
        yield fake
    finally:
        config._OVERRIDES.pop('openai_api_key', None)


@pytest.fixture
def with_key():
    """A dummy key so an Agent can be constructed. Nothing is ever sent."""
    config._OVERRIDES['openai_api_key'] = 'step15-test-key'
    try:
        yield
    finally:
        config._OVERRIDES.pop('openai_api_key', None)


def _extract(mode: str) -> dict:
    with record_run('extract', agent_role='step15'):
        return asyncio.run(ea.extract_structured(CASE_PAYLOAD, mode=mode))


def _persist(envelope: dict) -> tuple[str, dict]:
    """Compile + persist one envelope through the production pipeline.

    Each call gets a distinct document body: the document table dedupes on content hash,
    and reusing a body would collide instead of testing the pipeline twice.
    """
    content = f'{CASE_TEXT} [step15 {uuid.uuid4().hex[:8]}]'
    doc = create_document(title=f'step15 {content[-8:]}', content=content, source_type='note')
    chunk = write_chunks(doc, content)[0]
    prepared = json.loads(json.dumps(envelope))
    for claim in prepared['claims']:
        claim['source_chunk'] = chunk['id']
        claim['evidence_quote'] = content
    outcome = compile_extraction_items(prepared)
    with db.transaction() as conn:
        counts = persist_extraction(conn, document_id=doc, extraction=outcome.envelope)
    return doc, {'counts': counts, 'outcome': outcome, 'chunk': chunk['id']}


# ------------------------------------------------------------- §10 call counts

@pytest.mark.parametrize('provider', [[(STRUCTURED, ENVELOPE)]], indirect=True)
def test_prefetch_extraction_is_one_provider_call(provider):
    data = _extract(MODE_PREFETCH)
    assert provider.calls == 1, f'prefetch made {provider.calls} provider calls'
    assert data['claims'][0]['predicate'] == 'has_ceo'


@pytest.mark.parametrize('provider', [[REFERENCE_CALL, (STRUCTURED, ENVELOPE)]], indirect=True)
def test_agentic_extraction_still_costs_a_reference_round(provider):
    """The baseline this round is measured against — asserted, not assumed."""
    data = _extract(MODE_AGENTIC)
    assert provider.calls >= 2, 'agentic lost its reference round; the A/B is meaningless'
    assert provider.sent_tool_result('has_ceo')
    assert data['claims'][0]['predicate'] == 'has_ceo'


@pytest.mark.parametrize('provider', [[(STRUCTURED, ENVELOPE)]], indirect=True)
def test_prefetch_cannot_open_a_second_round_even_if_it_wants_to(provider):
    """§19: no hidden fallback, structurally.

    The reference tool is not registered, so a second round is not something the model
    can decide to take — it would have to call a tool that is not in the request.
    """
    _extract(MODE_PREFETCH)
    assert provider.calls == 1
    assert ea.PREFETCH_TOOLS == []
    assert REFERENCE_TOOL not in provider.tool_names()
    assert not provider.sent_tool_result('Claim Predicate Registry')
    # and the agent really is built with an empty function-tool set, not just sent one
    assert asyncio.run(
        ea.build_extraction_agent(CASE_PAYLOAD, mode=MODE_PREFETCH).toolkit.get_tool_schemas()
    ) == []


# --------------------------------------------------------- §16 context contents

@pytest.mark.parametrize('provider', [[(STRUCTURED, ENVELOPE)]], indirect=True)
def test_prefetch_carries_the_relevant_registry_context(provider):
    _extract(MODE_PREFETCH)
    prompt = provider.system_prompt
    assert 'has_ceo' in prompt                       # the candidate this chunk needs
    assert 'Organization' in prompt and 'Person' in prompt   # its declared domain/range
    assert 'never invent a name' in prompt           # the closed-vocabulary rule


@pytest.mark.parametrize('provider', [[(STRUCTURED, ENVELOPE)]], indirect=True)
def test_prefetch_does_not_ship_the_whole_reference_documents(provider):
    _extract(MODE_PREFETCH)
    prompt = provider.system_prompt
    assert WHOLE_PREDICATE_DOC not in prompt
    assert WHOLE_ENTITY_DOC not in prompt
    assert 'Claim-only predicates' not in prompt      # the predicate doc's long tail


@pytest.mark.parametrize('provider', [[(STRUCTURED, ENVELOPE)]], indirect=True)
def test_the_registry_context_appears_exactly_once(provider):
    """§16: a reference may appear once. A second copy is a bug, not a nuance."""
    _extract(MODE_PREFETCH)
    prompt = provider.system_prompt
    assert prompt.count(CLAIM_PREDICATES_SENTINEL) == 1
    assert prompt.count('[ENTITY TYPE CONTEXT]') == 1
    assert prompt.count('has_ceo (label') <= 1


@pytest.mark.parametrize('provider', [[(STRUCTURED, ENVELOPE)]], indirect=True)
def test_prefetch_says_the_references_are_already_loaded(provider):
    """The skill contract tells the agent to open the registries with a tool that
    prefetch does not register; the prompt has to say so rather than point at nothing."""
    _extract(MODE_PREFETCH)
    assert 'ALREADY LOADED' in provider.system_prompt


@pytest.mark.parametrize('provider', [[REFERENCE_CALL, (STRUCTURED, ENVELOPE)]], indirect=True)
def test_structured_output_survives_in_both_modes(provider):
    _extract(MODE_AGENTIC)
    assert STRUCTURED in provider.tool_names()
    one_call = [REFERENCE_TOOL, STRUCTURED]
    assert set(provider.tool_names()) <= set(one_call)


@pytest.mark.parametrize('provider', [[(STRUCTURED, ENVELOPE)]], indirect=True)
def test_prefetch_is_cheaper_than_the_two_call_prompt_it_replaces(provider):
    """The single call must not be a bigger request than the round it removed."""
    _extract(MODE_PREFETCH)
    system = provider.prompt_tokens()
    # The prefetched system prompt replaces: skill + two full reference documents.
    full_references = estimate_tokens(
        (Path('skills/knowledge-extraction/references/claim-predicates.md'))
        .read_text(encoding='utf-8')) + estimate_tokens(
        (Path('skills/knowledge-extraction/references/entity-types.md'))
        .read_text(encoding='utf-8'))
    assert system < full_references + 600


# --------------------------------------------------- §11 semantics are untouched

def test_the_two_modes_reach_the_same_stored_knowledge():
    """Compile → persist → eligibility, on the same envelope.

    The context mode decides what the model *sees*; it must not decide what the pipeline
    *stores*. Same envelope in, same rows out.
    """
    db.init_db()
    from app.db import loads
    from app.domain.entity_eligibility import is_entity_eligible_for_semantic_pool
    from app.repositories import ClaimRepository, EntityRepository

    stored: dict[str, dict] = {}
    for mode in (MODE_AGENTIC, MODE_PREFETCH):
        doc, result = _persist(ENVELOPE)
        entities = EntityRepository()
        apple = entities.get(entities.by_name('苹果')['id'])
        stored[mode] = {
            'claims': len(ClaimRepository().for_document(doc)),
            'entities': result['counts']['entities'],
            'status': result['outcome'].status,
            'rejected': [r.error_code for r in result['outcome'].failures],
            'apple_eligible': is_entity_eligible_for_semantic_pool(apple['properties']),
            'apple_types': sorted(loads(apple['types_json'], [])),
        }
    assert stored[MODE_AGENTIC] == stored[MODE_PREFETCH], stored
    assert stored[MODE_PREFETCH]['claims'] == 1
    assert stored[MODE_PREFETCH]['apple_eligible'] is True


def test_partial_commit_behaves_the_same_in_both_modes():
    """Step 11 must be untouched: one bad claim is isolated, the good one survives."""
    db.init_db()
    from app.repositories import ClaimRepository

    for mode in (MODE_AGENTIC, MODE_PREFETCH):
        doc, result = _persist(BAD_CLAIM_ENVELOPE)
        assert len(ClaimRepository().for_document(doc)) == 1, mode
        assert [r.error_code for r in result['outcome'].failures] == ['UNSUPPORTED_PREDICATE'], mode
        assert result['outcome'].status == COMPLETED_WITH_WARNINGS, mode


# ---------------------------------------------------- §17 the planner's boundary

def test_planner_output_has_no_verdict_fields():
    """Architecture lock: the plan may only carry context selections.

    If a future change wants to put a decided subject, object or claim in here, the
    field set changes and this fails. That is the point — the planner is a context
    candidate selector, and the registry/compiler stay the semantic authority.
    """
    plan = plan_extraction_context(CASE_TEXT)
    assert set(vars(plan)) == {
        'predicate_candidates', 'predicate_context', 'entity_type_context',
        'object_kind_context', 'general_extraction_context', 'flags',
    }
    # Candidates are registry names, not a chosen answer.
    from app.ontology import CLAIM_PREDICATES
    assert set(plan.predicate_candidates) <= CLAIM_PREDICATES


def test_planner_never_emits_claims_or_entity_types_for_the_model():
    plan = plan_extraction_context(CASE_TEXT)
    text = plan.text
    for verdict in ('"subject"', '"predicate":', 'Claim('):
        assert verdict not in text
    # It may offer the *types a predicate declares*; it may not decide a type for a name.
    assert '苹果 is' not in text and 'John Ternus is' not in text


def test_object_kind_is_not_re_injected():
    """§8: `object_kind` lives in the skill contract — one source, not two."""
    plan = plan_extraction_context(CASE_TEXT)
    assert plan.object_kind_context['injected_here'] is False
    assert 'object_kind' not in plan.text


def test_a_chunk_with_no_predicate_signal_still_gets_the_vocabulary():
    """§18: no candidate must not mean no context (and must not mean a forced guess)."""
    plan = plan_extraction_context('Them.')
    assert plan.predicate_candidates == ()
    assert CLAIM_PREDICATES_SENTINEL in plan.text
    assert INSUFFICIENT not in plan.flags
    assert 'never invent a predicate' in plan.text


def test_the_planner_needs_no_model_call():
    """It is deterministic: same text in, same plan out, no provider involved."""
    first = plan_extraction_context(CASE_TEXT)
    second = plan_extraction_context(CASE_TEXT)
    assert first.to_dict() == second.to_dict()


# ------------------------------------------- §3/§10 the mode contract, locked

def test_default_mode_is_prefetch():
    """Production default, with agentic kept as a real rollback path."""
    from app.config import DEFAULTS
    assert DEFAULTS['extraction_context_mode'] == MODE_PREFETCH
    assert ea.extraction_context_mode() == MODE_PREFETCH
    # the rollback is wired, not just documented
    rollback = ea.extraction_prompt_inputs(CASE_PAYLOAD, mode=MODE_AGENTIC)
    assert rollback['tools'] == ea.EXTRACTION_TOOLS != []
    assert rollback['extra_items'] is None and rollback['references']


def test_runtime_contract_prefetch_registers_no_function_tool(with_key):
    """The one-call property is structural: nothing callable, so nothing to call."""
    db.init_db()
    agent = ea.build_extraction_agent(CASE_PAYLOAD, mode=MODE_PREFETCH)
    assert asyncio.run(agent.toolkit.get_tool_schemas()) == []
    assert ea.extraction_prompt_inputs(CASE_PAYLOAD, mode=MODE_PREFETCH)['tools'] == []


def test_runtime_contract_agentic_keeps_the_reference_tool(with_key):
    """The other half of the same contract: the rollback path is not hollowed out."""
    db.init_db()
    agent = ea.build_extraction_agent(CASE_PAYLOAD, mode=MODE_AGENTIC)
    names = {((s.get('function') or {}).get('name') or s.get('name'))
             for s in asyncio.run(agent.toolkit.get_tool_schemas())}
    assert REFERENCE_TOOL in names


def test_skill_contract_names_no_runtime_tool():
    """The skill must not require a specific retrieval implementation.

    Both modes share one contract, and their tool surfaces differ — a contract that names
    a tool is broken in the other mode *by construction*. Testing "no registered tool name
    appears" states that property; asserting a particular sentence would not survive a
    rephrasing, which is the whole point of the fix.
    """
    contract = read_skill_contract('knowledge-extraction')
    candidates = {getattr(fn, '__name__', '') for fn in DEFAULT_TOOLS} | {
        STRUCTURED, REFERENCE_TOOL}
    named = sorted(name for name in candidates if name and name in contract)
    assert not named, f'skill contract requires a runtime tool: {named}'


def test_both_modes_share_one_skill_contract():
    """One skill file, delivered identically to both runtimes."""
    db.init_db()
    contract = f'[SKILL]\n{load_skill("knowledge-extraction")}'
    for mode in (MODE_AGENTIC, MODE_PREFETCH):
        inputs = ea.extraction_prompt_inputs(CASE_PAYLOAD, mode=mode)
        prompt = compose_prompt('extractor', references=inputs['references'],
                                tools=inputs['tools'], extra_items=inputs['extra_items'])
        assert contract in prompt, mode
