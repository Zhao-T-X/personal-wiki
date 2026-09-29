"""Knowledge Application — read & write knowledge-shaped state.

Owns the Knowledge facade: entities, claims, relations, ideas, questions and events, in
both directions. The HTTP adapter calls these functions; it does not open a repository
for knowledge reads or writes itself. Reads are composed from existing repository reads
only (nothing inferred); writes that change knowledge state go through ``operation`` or
through the same repositories *behind this boundary*.

The ``_predicate_label`` helper lives here rather than in the route file so the
vocabulary stays in one place (the registry is its only home — ADR-011).
"""
from __future__ import annotations

from ..models import (DocumentCreate, EntityCreate, EntityUpdate, EventCreate, IdeaCreate,
                    QuestionCreate)
from ..ontology import canonical_entity_type, claim_predicate_spec, normalize_name
from ..repositories import (ClaimRepository, DocumentRepository, EntityRepository,
                            EventRepository, IdeaRepository, QuestionRepository,
                            RelationRepository)
from ..domain.claim_history import order_chain
from . import (ApplicationError, BadRequestError, ConflictError, NotFoundError, ValidationError)


def _is_unique_violation(exc: Exception) -> bool:
    """The repository layer raises ``sqlite3.IntegrityError`` on a unique violation.

    We translate it here without importing the driver into the Application layer
    (the Application layer owns orchestration, not the persistence driver).
    """
    return exc.__class__.__name__ == 'IntegrityError'


def _predicate_label(predicate: str | None) -> str | None:
    """The registry's label for a predicate, or ``None`` when it declares none.

    Shipping the labels from the registry — instead of repeating a ``predicate_label``
    field on every claim-shaped response — keeps the vocabulary in a single place; a
    frontend map of its own would be a second ontology, and a per-endpoint copy would
    drift from the registry one response at a time.
    """
    spec = claim_predicate_spec(predicate or '')
    return spec.label if spec else None


# --- reads ----------------------------------------------------------------

def get_claim(claim_id: str) -> dict:
    item = ClaimRepository().get(claim_id)
    if not item:
        raise NotFoundError('Claim not found')
    return item


def claim_relations_for(claim_id: str) -> dict:
    """How this claim relates to other claims (both directions).

    Claims are never overwritten, so this is the only place knowledge evolution is
    expressed — including whether a newer claim supersedes this one.
    """
    return {'claim_id': claim_id, 'relations': ClaimRepository().relations_for_claim(claim_id)}


def claim_history(claim_id: str) -> dict:
    """One fact's evolution, oldest first — what it used to say, and when that changed.

    Composed entirely from stored facts: the accepted supersede relations give the order
    *and* the moment each statement stopped holding, and the claim rows give the values
    plus their evidence. Nothing is inferred or reconstructed — which is only possible
    because superseding moves a lifecycle status instead of deleting the row (ADR-005).
    """
    claims = ClaimRepository()
    claim = claims.get(claim_id)
    if not claim:
        raise NotFoundError('Claim not found')
    edges = claims.supersede_edges(claim['subject_id'], claim['predicate'])
    chain = order_chain(edges, claim_id)
    superseded_at = {e['target_claim_id']: e['created_at'] for e in edges}
    superseded_by = {e['target_claim_id']: e['source_claim_id'] for e in edges}
    corroboration = claims.corroboration_counts(chain.ordered_ids)

    nodes = []
    for cid in chain.ordered_ids:
        row = claims.get(cid) or {}
        nodes.append({
            'id': cid,
            'subject': row.get('subject_name') or '',
            'predicate': row.get('predicate') or '',
            'predicate_label': _predicate_label(row.get('predicate')),
            'object': row.get('object_name') or row.get('object_text') or '',
            'status': row.get('status'),
            'effective_from': row.get('created_at'),
            'effective_to': superseded_at.get(cid),
            'superseded_by': superseded_by.get(cid),
            'source_quote': row.get('source_quote'),
            'source_document_id': row.get('source_document_id'),
            'source_chunk_id': row.get('source_chunk_id'),
            'sources': (1 if row.get('source_quote') else 0) + corroboration.get(cid, 0),
            'corroborating': corroboration.get(cid, 0),
            'is_current': cid == chain.current_id,
        })
    return {'claim_id': claim_id, 'current_id': chain.current_id, 'cycles': chain.cycles,
            'superseded_count': chain.superseded_count, 'chain': nodes}


def entity_object(entity_id: str) -> dict:
    entities = EntityRepository()
    e = entities.get_full(entity_id)
    if not e:
        raise NotFoundError('Entity not found')
    claims = ClaimRepository().for_entity(entity_id)
    relations = RelationRepository().for_entity(entity_id)
    docs = list({c['source_document_id'] for c in claims})
    events = EventRepository().for_documents(docs, 50) if docs else []
    ideas = IdeaRepository().for_documents(docs, 50) if docs else []
    questions = QuestionRepository().for_documents(docs, 50) if docs else []
    evidence = [c for c in claims if c['source_quote']]
    type_decision = []
    for c in claims:
        if c['subject_id'] == entity_id and c['predicate'] == 'defined_as' and c['object_text']:
            type_decision.append({'type': c['object_text'][:60],
                                  'reason': f"来源明确定义：{(c['source_quote'] or '')[:90]}",
                                  'ok': c['polarity'] == 'positive'})
    counts = {'claims': len(claims), 'relations': len(relations), 'events': len(events),
              'ideas': len(ideas), 'questions': len(questions), 'evidence': len(evidence),
              'documents': len(docs)}
    return {'entity': e, 'counts': counts, 'claims': claims, 'relations': relations,
            'evidence': evidence, 'events': events, 'ideas': ideas, 'questions': questions,
            'type_decision': type_decision}


def document_knowledge(doc_id: str) -> dict:
    """What this document contributed — the answer to "so what did it read out of it?"

    Composed from existing reads only: nothing is inferred, and the names are taken from
    the claims themselves, so this view can never disagree with the knowledge base — it
    *is* the knowledge base, filtered to one document.
    """
    if not DocumentRepository().exists(doc_id):
        raise NotFoundError('Document not found')
    claims = ClaimRepository().for_document(doc_id, limit=200)
    names: list[str] = []
    seen: set[str] = set()
    for c in claims:
        for n in (c.get('subject_name'), c.get('object_name')):
            if n and n not in seen:
                seen.add(n)
                names.append(n)
    return {
        'document_id': doc_id,
        'names': names,
        'claims': [{
            'id': c.get('id'), 'subject': c.get('subject_name') or '',
            'predicate': c.get('predicate'),
            'predicate_label': _predicate_label(c.get('predicate')),
            'object': c.get('object_name') or c.get('object_text') or '',
            'status': c.get('status'), 'confidence': c.get('confidence'),
            'quote': c.get('source_quote'),
        } for c in claims],
        'counts': {
            'claims': len(claims),
            'names': len(names),
            'pending': sum(1 for c in claims if c.get('status') == 'candidate'),
        },
    }


def knowledge_health() -> dict:
    entities = EntityRepository().health_counts()
    claims = ClaimRepository().health_counts()
    relations = RelationRepository().count()
    open_q = QuestionRepository().open_count()
    return {'total_claims': claims['total'], 'verified_claims': claims['verified'],
            'verified_claim_ratio': round(claims['verified'] / max(1, claims['total']) * 100, 1),
            'total_entities': entities['total'], 'verified_entities': entities['verified'],
            'verified_entity_ratio': round(entities['verified'] / max(1, entities['total']) * 100, 1),
            'object_text_claims': claims['object_text'], 'relations': relations,
            'open_questions': open_q, 'stale_candidates': entities['stale']}


def conflicts(limit: int = 50) -> list[dict]:
    rows = ClaimRepository().conflicts_rows()
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r['subject_name'], r['predicate']), []).append(dict(r))
    out = []
    for (subj, pred), cs in groups.items():
        if len(cs) >= 2 and len({c['polarity'] for c in cs}) > 1:
            out.append({'subject_name': subj, 'predicate': pred, 'claims': cs})
            if len(out) >= max(1, min(limit, 200)):
                break
    return out


# --- writes ----------------------------------------------------------------

def create_entity(payload: EntityCreate) -> dict:
    import uuid
    try:
        et = canonical_entity_type(payload.type)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    eid = str(uuid.uuid4())
    name = payload.name.strip()
    aliases = sorted({name, *[a.strip() for a in payload.aliases if a.strip()]})
    try:
        EntityRepository().insert(eid, type=et, types=[et], name=name, aliases=aliases,
                                  description=payload.description, properties=payload.properties,
                                  normalize=normalize_name)
    except Exception as exc:
        if _is_unique_violation(exc):
            raise ConflictError('An entity with this name already exists') from exc
        raise
    return {'id': eid, 'name': name, 'status': 'candidate'}


def update_entity(entity_id: str, payload: EntityUpdate) -> dict:
    from ..db import dumps, loads
    changes: dict = {}
    if payload.description is not None:
        changes['description'] = payload.description
    if payload.name is not None:
        changes['name'] = payload.name.strip()
        changes['aliases_json'] = dumps(sorted({payload.name.strip(), *(payload.aliases or [])}))
    elif payload.aliases is not None:
        changes['aliases_json'] = dumps(payload.aliases)
    if payload.type is not None:
        try:
            et = canonical_entity_type(payload.type)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        changes['type'] = et
        changes['types_json'] = dumps([et])
    if payload.properties:
        changes['properties_json'] = dumps(payload.properties)
    entities = EntityRepository()
    if not changes:
        if not entities.exists(entity_id):
            raise NotFoundError('Entity not found')
        return {'id': entity_id, 'updated': False}
    aliases = loads(changes['aliases_json'], []) if 'aliases_json' in changes else None
    try:
        rowcount = entities.update(entity_id, changes, aliases=aliases, normalize=normalize_name)
    except Exception as exc:
        if _is_unique_violation(exc):
            raise ConflictError('Another entity already uses this name') from exc
        raise
    if not rowcount:
        raise NotFoundError('Entity not found')
    return {'id': entity_id, 'updated': True}


def create_idea(payload: IdeaCreate) -> dict:
    iid = IdeaRepository().insert(content=payload.content, status=payload.status,
                                 source_document_id=payload.source_document_id)
    return {'id': iid, 'status': payload.status}


def create_question(payload: QuestionCreate) -> dict:
    qid = QuestionRepository().insert(content=payload.content, status=payload.status,
                                      source_document_id=payload.source_document_id)
    return {'id': qid, 'status': payload.status}


def create_event(payload: EventCreate) -> dict:
    eid = EventRepository().insert(event_type=payload.event_type, description=payload.description,
                                   participants=payload.participants, time=payload.time,
                                   location=payload.location, status=payload.status,
                                   source_document_id=payload.source_document_id)
    return {'id': eid, 'status': payload.status}


def update_document(doc_id: str, data: DocumentCreate) -> dict:
    """Replace a document's title / content / source.

    The write lives here so the route handler performs no data access directly.
    """
    try:
        rowcount = DocumentRepository().update(
            doc_id, title=data.title, content=data.content,
            source_type=data.source_type, source_uri=data.source_uri, metadata=data.metadata)
    except Exception as exc:
        if _is_unique_violation(exc):
            raise ConflictError('Another document already has the same content') from exc
        raise
    if not rowcount:
        raise NotFoundError('Document not found')
    return {'id': doc_id, 'updated': True}
