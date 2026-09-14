"""One-sentence knowledge correction: plan is read-only, apply goes through CORRECT."""
import asyncio
import importlib
import json
import os
import sys

_RELOAD = (
    'app.repositories.base',
    'app.repositories.document_repo',
    'app.repositories.entity_repo',
    'app.repositories.claim_repo',
    'app.repositories.relation_repo',
    'app.repositories.evidence_repo',
    'app.repositories.event_repo',
    'app.repositories.idea_repo',
    'app.repositories.question_repo',
    'app.repositories.research_repo',
    'app.repositories.run_repo',
    'app.repositories.catalog_repo',
    'app.repositories.operation_repo',
    'app.resolution',
    'app.knowledge',
    'app.claim_relations',
    'app.service',
    'app.domain.claim_state',
    'app.domain.operations',
    'app.workflows.correction_workflow',
)


def _reload(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config)
    importlib.reload(db)
    for name in _RELOAD:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


def _seed_existing_claim(tmp_path, *, subject='OpenAI', predicate='defined_as', obj='Sam'):
    _reload(tmp_path)
    from app.db import transaction
    from app.repositories.document_repo import DocumentRepository
    from app.domain.operations import OperationRequest, run

    docs = DocumentRepository()
    doc_id = docs.create(title='D', content='OpenAI is led by Sam.')
    chunk = docs.replace_chunks(doc_id, [('OpenAI is led by Sam.', 0, 0, 21)])[0]
    with transaction() as conn:
        claim_id = run(OperationRequest(kind='CREATE', payload={
            'subject': subject, 'predicate': predicate, 'object': obj,
            'content': 'OpenAI is led by Sam.', 'source_document_id': doc_id,
            'source_chunk_id': chunk['id'], 'source_quote': 'OpenAI is led by Sam.'}),
            conn).affected['claim_id']
    return claim_id


def test_plan_is_read_only_and_flags_a_conflict(tmp_path):
    claim_id = _seed_existing_claim(tmp_path)
    from app.repositories.claim_repo import ClaimRepository
    from app.repositories.operation_repo import OperationRepository
    from app.workflows.correction_workflow import CorrectionIntent, build_plan

    plan = build_plan('OpenAI 的领导人是 Alice。',
                      CorrectionIntent(text='OpenAI 的领导人是 Alice。', subject='OpenAI',
                                       predicate='defined_as', object='Alice'))

    # `defined_as` is single-valued, so a different object is a conflict to review.
    assert plan.relationship == 'contradicts'
    assert plan.related_claim_id == claim_id
    assert plan.candidates[0]['claim_id'] == claim_id
    # Nothing was written by planning.
    assert len(ClaimRepository().list(10)) == 1
    assert OperationRepository().count() == 1   # only the seed CREATE


def test_apply_correction_creates_traceable_claim(tmp_path):
    _seed_existing_claim(tmp_path)
    from app.repositories.claim_repo import ClaimRepository
    from app.repositories.document_repo import DocumentRepository
    from app.repositories.operation_repo import OperationRepository
    from app.workflows.correction_workflow import CorrectionIntent, apply_correction, build_plan

    intent = CorrectionIntent(text='OpenAI 的领导人是 Alice。', subject='OpenAI',
                              predicate='defined_as', object='Alice')
    plan = build_plan(intent.text, intent)
    result = apply_correction(plan, relationship='contradicts', related_claim_id=plan.related_claim_id)

    claim = ClaimRepository().get(result['claim_id'])
    assert claim['object_text'] == 'Alice'
    assert claim['status'] == 'candidate'

    # The correction became its own document, so the claim has real provenance.
    document = DocumentRepository().get(result['document_id'])
    assert document['source_type'] == 'correction'
    assert claim['source_document_id'] == result['document_id']
    assert claim['source_quote'] == 'OpenAI 的领导人是 Alice。'

    relations = ClaimRepository().relations_for_claim(result['claim_id'])
    assert relations[0]['relationship'] == 'contradicts'
    assert OperationRepository().get(result['operation_id'])['kind'] == 'CORRECT'


def test_apply_correction_can_supersede(tmp_path):
    old_id = _seed_existing_claim(tmp_path)
    from app.repositories.claim_repo import ClaimRepository
    from app.workflows.correction_workflow import CorrectionIntent, apply_correction, build_plan

    intent = CorrectionIntent(text='OpenAI 的领导人是 Alice。', subject='OpenAI',
                              predicate='defined_as', object='Alice')
    plan = build_plan(intent.text, intent)
    result = apply_correction(plan, relationship='supersedes', related_claim_id=old_id,
                              apply_supersede=True)

    claims = ClaimRepository()
    assert claims.status_of(old_id) == 'superseded'      # lifecycle moved
    assert claims.get(old_id)['object_text'] == 'Sam'    # content untouched
    assert result['superseded_claim_id'] == old_id


def test_parse_intent_degrades_without_a_model(tmp_path):
    _reload(tmp_path)
    from app.workflows.correction_workflow import parse_intent
    # No API key is configured in tests, so the model step must decline, not guess.
    assert asyncio.run(parse_intent('Anything.')) is None


def _seed_claim(tmp_path, *, subject: str, predicate: str, obj: str, content: str | None = None,
                subject_types: tuple[str, ...] = ('Organization',)):
    _reload(tmp_path)
    from app.db import transaction
    from app.repositories.document_repo import DocumentRepository
    from app.domain.operations import OperationRequest, run
    from app.resolution import resolve_or_create_entity

    content = content or f'{subject} {predicate} {obj}.'
    docs = DocumentRepository()
    doc_id = docs.create(title='D', content=content)
    chunk = docs.replace_chunks(doc_id, [(content, 0, 0, len(content))])[0]
    with transaction() as conn:
        # Type the subject explicitly: the ontology's domain/range gate needs to be
        # able to *check* the pairing, and an untyped entity could not be checked.
        resolve_or_create_entity(conn, name=subject, entity_types=list(subject_types),
                                 aliases=[], description=None, properties={})
        claim_id = run(OperationRequest(kind='CREATE', payload={
            'subject': subject, 'predicate': predicate, 'object': obj,
            'content': content, 'source_document_id': doc_id,
            'source_chunk_id': chunk['id'], 'source_quote': content}),
            conn).affected['claim_id']
    return claim_id


class _FakeCompletion:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeCompletion(content)


class _FakeResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


def test_verify_new_claim_detects_cross_predicate_conflict(tmp_path, monkeypatch):
    """A new statement whose predicate differs (new_ceo vs ceo) can still be flagged
    as contradicting existing knowledge after semantic verification."""
    old_id = _seed_claim(tmp_path, subject='苹果公司', predicate='ceo', obj='蒂姆·库克',
                         content='苹果公司的首席执行官是蒂姆·库克。')
    from app.workflows.correction_workflow import CorrectionIntent, build_plan, verify_new_claim

    intent = CorrectionIntent(text='苹果公司的新任 CEO 是约翰·特努斯。', subject='苹果公司',
                              predicate='new_ceo', object='约翰·特努斯')
    plan = build_plan(intent.text, intent)
    # Deterministic planner alone cannot relate different predicates.
    assert plan.relationship == 'new'

    def _fake_client():
        class FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(*args, **kwargs):
                        return _FakeResponse(json.dumps({
                            'verdict': 'contradicted',
                            'confidence': 0.92,
                            'rationale': 'CEO 是单值角色，蒂姆·库克与约翰·特努斯不能同时担任。',
                            'conflicting_claim_id': old_id,
                        }))
        return FakeClient()

    # _client and runtime are imported locally inside verify_new_claim, so patch
    # the source modules rather than the workflow module.
    monkeypatch.setattr('app.llm._client', _fake_client)
    monkeypatch.setattr('app.config.runtime', lambda: {
        'openai_model': 'gpt-4o-mini', 'openai_api_key': 'x', 'openai_base_url': 'http://localhost'})

    plan = asyncio.run(verify_new_claim(plan))
    assert plan.verification['verdict'] == 'contradicted'
    assert plan.verification['confidence'] == 0.92
    assert plan.relationship == 'contradicts'
    assert plan.related_claim_id == old_id


def test_verify_new_claim_degrades_when_model_unavailable(tmp_path):
    """Without a working model the verifier must stay honest instead of guessing."""
    _seed_claim(tmp_path, subject='苹果公司', predicate='ceo', obj='蒂姆·库克',
                content='苹果公司的首席执行官是蒂姆·库克。')
    from app.workflows.correction_workflow import CorrectionIntent, build_plan, verify_new_claim

    intent = CorrectionIntent(text='苹果公司的新任 CEO 是约翰·特努斯。', subject='苹果公司',
                              predicate='new_ceo', object='约翰·特努斯')
    plan = build_plan(intent.text, intent)
    plan = asyncio.run(verify_new_claim(plan))
    assert plan.verification['verdict'] == 'uncertain'
    assert plan.relationship == 'new'
