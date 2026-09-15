"""The claim-producing entries, defined once for the consistency tests.

Two test files ask the same question — *do all the claim entries agree?* — and they
must ask it about the same entries, with the same drafts. Defining the adapters
twice would let the two answers drift apart, which is the very failure the tests
exist to catch. So the entries live here and both files import them.

An "entry" is one route from a Claim Draft to a canonical claim:

    extraction   a whole Extraction Envelope (schema + repair contract)
    correction   the one-sentence correction workflow
    research     a finding produced outside a document (ADR-011 lists this as TARGET)

An adapter is allowed to do exactly two things: build the payload its own transport
expects, and read the result back. It must not interpret a predicate, default a
field or validate anything — those belong to ``KnowledgeCompiler`` (ADR-011).
"""
from __future__ import annotations

import importlib
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# The canonical claim's own shape. Every entry must produce these keys with these
# values for the same draft; an envelope carries provenance on top of them.
CANONICAL_KEYS = ('subject', 'predicate', 'object', 'claim_type', 'polarity',
                  'modality', 'temporal_signal', 'context', 'confidence')

SENTENCE = '苹果公司的新任 CEO 是约翰·特努斯。'

# One Claim Draft, in the shape a model actually produces: an invented predicate
# with the temporal meaning baked into its name.
DRAFT = {'subject': '苹果公司', 'predicate_candidate': 'new_ceo',
         'object': '约翰·特努斯', 'claim_type': 'factual', 'polarity': 'positive',
         'modality': 'asserted', 'temporal_signal': 'new', 'confidence': 0.9,
         'context': {}}

ENTITIES = ({'name': '苹果公司', 'types': ['Organization'], 'aliases': ['苹果']},
            {'name': '约翰·特努斯', 'types': ['Person'], 'aliases': []})

# The same draft with a range violation: a registered predicate, used illegally.
ILLEGAL_DRAFT = {**DRAFT, 'object': '加利福尼亚', 'temporal_signal': None}
ILLEGAL_ENTITIES = ({'name': '苹果公司', 'types': ['Organization'], 'aliases': ['苹果']},
                    {'name': '加利福尼亚', 'types': ['Location'], 'aliases': []})

# The fact the draft above is about to replace.
INCUMBENT_ENTITY = {'name': '蒂姆·库克', 'types': ['Person'], 'aliases': []}
INCUMBENT_CLAIM = {'subject': '苹果公司', 'predicate': 'has_ceo', 'object': '蒂姆·库克'}

# Modules that bind DATABASE_PATH at import time, so a test that switches databases
# has to reload them — the same dance every DB-backed test file performs.
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
    'app.domain.claim_evolution',
    'app.domain.predicate_resolver',
    'app.domain.compiler',
    'app.domain.operations',
    'app.workflows.correction_workflow',
)


def prepare_database(tmp_path):
    """Point every DB-bound module at a fresh database under ``tmp_path``."""
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


def type_index(entities):
    """Normalized name / alias -> entity record, the shape the compiler expects."""
    from app.ontology import normalize_name
    index = {}
    for e in entities:
        index[normalize_name(e['name'])] = e
        for alias in e.get('aliases', []):
            index.setdefault(normalize_name(alias), e)
    return index


def seed_entities(entities):
    from app.db import transaction
    from app.resolution import resolve_or_create_entity
    with transaction() as conn:
        for e in entities:
            resolve_or_create_entity(conn, name=e['name'], entity_types=list(e['types']),
                                     aliases=list(e.get('aliases', [])), description=None,
                                     properties={})


def seed_claim(claim):
    """Write one claim (``subject`` / ``predicate`` / ``object``) with real provenance."""
    from app.db import transaction
    from app.domain.operations import OperationRequest, run
    from app.repositories.document_repo import DocumentRepository

    subject, predicate, obj = claim['subject'], claim['predicate'], claim['object']

    text = f'{subject}的{predicate}是{obj}。'
    docs = DocumentRepository()
    doc_id = docs.create(title=f'{subject}简介', content=text, source_type='note')
    chunk = docs.replace_chunks(doc_id, [(text, 0, 0, len(text))])[0]
    with transaction() as conn:
        return run(OperationRequest(kind='CREATE', payload={
            'subject': subject, 'predicate': predicate, 'object': obj, 'content': text,
            'source_document_id': doc_id, 'source_chunk_id': chunk['id'],
            'source_quote': text}), conn).affected['claim_id']


def _canonical_or_raise(result):
    """One contract for every adapter: a canonical claim, or a ``ValueError``."""
    if result.ok and result.claim is not None:
        return result.claim.to_dict()
    raise ValueError('; '.join(result.reasons) or result.resolution.reason)


def via_extraction(draft, entities):
    """Extraction: a whole envelope, with the compiler writing the claim back.

    The envelope names the proposed predicate ``predicate`` (that is the schema's
    field); a canonical draft names it ``predicate_candidate``. Renaming that one
    key is the whole of what an envelope adapter does.
    """
    from app.extraction import normalize_extraction
    claim = {**draft, 'predicate': draft['predicate_candidate'], 'content': SENTENCE,
             'evidence_quote': SENTENCE, 'source_chunk': 'chunk-1'}
    claim.pop('predicate_candidate')
    out = normalize_extraction({
        'entities': [dict(e) for e in entities],
        'claims': [claim],
        'events': [], 'ideas': [], 'questions': [],
    })
    return {k: out['claims'][0][k] for k in CANONICAL_KEYS}


def via_correction(draft, entities):
    """Correction: the same draft through the correction workflow's compile step."""
    from app.workflows.correction_workflow import compile_intent, intent_from_draft
    return _canonical_or_raise(compile_intent(intent_from_draft(SENTENCE, draft)))


def via_research(draft, entities):
    """Research: a research finding is a Claim Draft like any other.

    Research has no claim-ingest wired yet, so this exercises the shared dict
    ingress a research ingest must use. That is the point of including it: the day
    one is added, the consistency tests force it to agree rather than resolve
    predicates on its own.
    """
    from app.domain.compiler import KnowledgeCompiler
    return _canonical_or_raise(KnowledgeCompiler().compile_extraction_claim(
        dict(draft), entities=type_index(entities)))


ENTRIES = {'extraction': via_extraction, 'correction': via_correction,
           'research': via_research}
