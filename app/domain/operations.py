"""Knowledge Operations — the only sanctioned way to change knowledge.

A caller never edits a Claim. It submits an Operation; the operation validates,
changes *lifecycle and relationships* (never content), and is written to the
audit trail. This is what makes "correct a fact" and "merge duplicates" and
"restore an archived claim" all the same mechanism.

    Agent / API -> Operation -> Domain rule -> Repository

The executor runs inside a caller-owned transaction (``app/db.transaction``), so
a failed operation can never leave a half-applied change behind. This module
imports repositories (the persistence interface) but never ``sqlite3``, FastAPI
or AgentScope.

See docs/architecture/KNOWLEDGE-OPERATIONS.md and docs/adr/ADR-009.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..ontology import KNOWLEDGE_STATUSES
from ..repositories import (ClaimRepository, DocumentRepository, EntityRepository,
                            OperationRepository, RelationRepository)
from ..resolution import resolve_or_create_entity

KINDS = ('CREATE', 'DUPLICATE', 'CONTRADICT', 'SUPERSEDE', 'CORRECT', 'MERGE', 'ARCHIVE', 'RESTORE')


class OperationError(ValueError):
    """An operation's input violates a domain rule (maps to HTTP 422)."""


@dataclass
class OperationRequest:
    kind: str
    payload: dict = field(default_factory=dict)
    actor: str = 'user'
    reason: str = ''


@dataclass
class OperationResult:
    operation_id: str
    kind: str
    affected: dict


_HANDLERS: dict = {}


def register_operation(kind: str):
    """Register a handler. New operations extend the system by registering,
    never by adding another ``elif`` to a central dispatch."""
    def decorator(fn):
        _HANDLERS[kind] = fn
        return fn
    return decorator


def registered_operations() -> dict:
    return dict(_HANDLERS)


class _Context:
    """What a handler may touch — all repositories bound to one connection."""

    def __init__(self, conn, request: OperationRequest):
        self.conn = conn
        self.request = request
        self.payload = request.payload or {}
        self.actor = request.actor or 'user'
        self.reason = request.reason or ''
        self.claims = ClaimRepository(conn)
        self.entities = EntityRepository(conn)
        self.relations = RelationRepository(conn)
        self.documents = DocumentRepository(conn)
        self.operations = OperationRepository(conn)

    def require(self, key: str):
        value = self.payload.get(key)
        if value in (None, ''):
            raise OperationError(f'{self.request.kind} requires "{key}"')
        return value

    def entity_for(self, *, subject_id: str | None = None, name: str | None = None,
                   entity_type: str = 'Resource') -> str:
        if subject_id:
            if not self.entities.exists(subject_id):
                raise OperationError(f'Entity not found: {subject_id}')
            return subject_id
        if not name:
            raise OperationError('Claim operations need subject_id or a subject name')
        return resolve_or_create_entity(self.conn, name=name, entity_types=[entity_type],
                                        aliases=[], description=None, properties={})


def _assert_claims(ctx: _Context, *claim_ids: str) -> None:
    for claim_id in claim_ids:
        if not ctx.claims.comparison_target(claim_id):
            raise OperationError(f'Claim not found: {claim_id}')


def _new_claim(ctx: _Context) -> str:
    """Shared claim creation for CREATE / CORRECT. Provenance is mandatory."""
    p = ctx.payload
    subject_id = ctx.entity_for(subject_id=p.get('subject_id'), name=p.get('subject'))
    predicate = ctx.require('predicate')
    object_id = p.get('object_id')
    object_text = p.get('object_text')
    if not object_id and not object_text:
        object_text = p.get('object') or None
    return ctx.claims.insert(
        subject_id=subject_id, predicate=predicate, object_id=object_id, object_text=object_text,
        content=p.get('content'), context=p.get('context', {}),
        claim_type=p.get('claim_type', 'factual'), polarity=p.get('polarity', 'positive'),
        modality=p.get('modality', 'asserted'), confidence=p.get('confidence'),
        status='candidate', created_by=ctx.actor,
        source_document_id=ctx.require('source_document_id'),
        source_chunk_id=ctx.require('source_chunk_id'),
        source_start_offset=p.get('source_start_offset', 0),
        source_end_offset=p.get('source_end_offset', 0),
        source_quote=p.get('source_quote'))


def _link(ctx: _Context, *, new_id: str, old_id: str, relationship: str,
          status: str, suggested_action: str) -> str | None:
    _assert_claims(ctx, new_id, old_id)
    return ctx.claims.insert_relation(
        source_claim_id=new_id, target_claim_id=old_id, relationship=relationship,
        confidence=ctx.payload.get('confidence'),
        reason=ctx.reason or f'{relationship} via {ctx.request.kind}',
        suggested_action=suggested_action, status=status, created_by=ctx.actor)


@register_operation('CREATE')
def _create(ctx: _Context) -> dict:
    return {'claim_id': _new_claim(ctx)}


@register_operation('DUPLICATE')
def _duplicate(ctx: _Context) -> dict:
    new_id, old_id = ctx.require('new_claim_id'), ctx.require('old_claim_id')
    relation_id = _link(ctx, new_id=new_id, old_id=old_id, relationship='duplicate',
                        status='accepted', suggested_action='link_evidence')
    return {'relation_id': relation_id, 'new_claim_id': new_id, 'old_claim_id': old_id}


@register_operation('CONTRADICT')
def _contradict(ctx: _Context) -> dict:
    new_id, old_id = ctx.require('new_claim_id'), ctx.require('old_claim_id')
    relation_id = _link(ctx, new_id=new_id, old_id=old_id, relationship='contradicts',
                        status='candidate', suggested_action='review')
    return {'relation_id': relation_id, 'new_claim_id': new_id, 'old_claim_id': old_id}


@register_operation('SUPERSEDE')
def _supersede(ctx: _Context) -> dict:
    """The only operation that makes an older claim stop being current. It moves
    the older claim's *lifecycle status*; its text, object and evidence are never
    rewritten."""
    new_id, old_id = ctx.require('new_claim_id'), ctx.require('old_claim_id')
    previous = ctx.claims.status_of(old_id)
    relation_id = _link(ctx, new_id=new_id, old_id=old_id, relationship='supersedes',
                        status='accepted', suggested_action='review')
    if relation_id:
        ctx.claims.set_relation_previous_status(relation_id, previous)
    ctx.claims.set_status(old_id, 'superseded')
    return {'relation_id': relation_id, 'superseded_claim_id': old_id, 'previous_status': previous}


@register_operation('CORRECT')
def _correct(ctx: _Context) -> dict:
    """Create the corrected claim and link it to the claim it corrects. Also the
    operation behind "one-sentence correction"."""
    p = ctx.payload
    new_id = _new_claim(ctx)
    result: dict = {'claim_id': new_id}
    related_id = p.get('related_claim_id')
    relationship = p.get('relationship')
    if related_id and relationship and relationship != 'new':
        _assert_claims(ctx, related_id)
        status = 'accepted' if relationship in ('duplicate', 'coexists') else 'candidate'
        relation_id = ctx.claims.insert_relation(
            source_claim_id=new_id, target_claim_id=related_id, relationship=relationship,
            confidence=p.get('confidence'), reason=ctx.reason or 'user correction',
            suggested_action='link_evidence' if status == 'accepted' else 'review',
            status=status, created_by=ctx.actor)
        result.update({'related_claim_id': related_id, 'relationship': relationship,
                       'relation_id': relation_id})
        if relationship == 'supersedes' and p.get('apply_supersede'):
            previous = ctx.claims.status_of(related_id)
            if relation_id:
                ctx.claims.set_relation_previous_status(relation_id, previous)
            ctx.claims.set_status(related_id, 'superseded')
            result.update({'superseded_claim_id': related_id, 'previous_status': previous})
    return result


@register_operation('MERGE')
def _merge(ctx: _Context) -> dict:
    keep_id, drop_id = ctx.require('keep_claim_id'), ctx.require('merge_claim_id')
    if keep_id == drop_id:
        raise OperationError('MERGE needs two different claims')
    _assert_claims(ctx, keep_id, drop_id)
    previous = ctx.claims.status_of(drop_id)
    relation_id = ctx.claims.insert_relation(
        source_claim_id=keep_id, target_claim_id=drop_id, relationship='duplicate',
        confidence=ctx.payload.get('confidence'), reason=ctx.reason or 'merged duplicate',
        suggested_action='link_evidence', status='accepted', created_by=ctx.actor)
    ctx.claims.set_status(drop_id, 'archived')
    return {'relation_id': relation_id, 'kept_claim_id': keep_id,
            'archived_claim_id': drop_id, 'previous_status': previous}


@register_operation('ARCHIVE')
def _archive(ctx: _Context) -> dict:
    claim_id = ctx.require('claim_id')
    _assert_claims(ctx, claim_id)
    previous = ctx.claims.status_of(claim_id)
    ctx.claims.set_status(claim_id, 'archived')
    return {'claim_id': claim_id, 'previous_status': previous}


@register_operation('RESTORE')
def _restore(ctx: _Context) -> dict:
    claim_id = ctx.require('claim_id')
    _assert_claims(ctx, claim_id)
    target = ctx.payload.get('status') or 'candidate'
    if target not in KNOWLEDGE_STATUSES:
        raise OperationError(f'Invalid restore status: {target}')
    ctx.claims.set_status(claim_id, target)
    return {'claim_id': claim_id, 'status': target}


def run(request: OperationRequest, conn) -> OperationResult:
    """Execute one operation in the caller's transaction and audit it."""
    handler = _HANDLERS.get(request.kind)
    if handler is None:
        raise OperationError(f'Unknown operation: {request.kind}')
    ctx = _Context(conn, request)
    affected = handler(ctx)
    operation_id = str(uuid.uuid4())
    ctx.operations.record(op_id=operation_id, kind=request.kind, actor=ctx.actor,
                          status='applied', reason=ctx.reason,
                          payload=request.payload, result=affected)
    return OperationResult(operation_id=operation_id, kind=request.kind, affected=affected)
