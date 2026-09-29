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

from ..claim_relations import RELATIONSHIPS
from ..ontology import (IDEA_STATUSES, KNOWLEDGE_STATUSES, QUESTION_STATUSES,
                        canonical_claim_predicate, claim_predicate_spec,
                        claim_registry_version)
from ..repositories import (ClaimRepository, DocumentRepository, EntityRepository,
                            IdeaRepository, OperationRepository, QuestionRepository,
                            RelationRepository)

KINDS = ('CREATE', 'DUPLICATE', 'CONTRADICT', 'SUPERSEDE', 'CORRECT', 'MERGE',
         'ARCHIVE', 'RESTORE', 'ACCEPT', 'RELATION_RESOLVE', 'MERGE_ENTITY')


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
        # Imported lazily: ``app.resolution`` imports from ``app.domain.*``, so a
        # top-level import here would form a cycle (resolution -> domain -> operations
        # -> resolution). At call time every module is fully initialised.
        from ..resolution import resolve_or_create_entity
        return resolve_or_create_entity(self.conn, name=name, entity_types=[entity_type],
                                        aliases=[], description=None, properties={})


def _assert_claims(ctx: _Context, *claim_ids: str) -> None:
    for claim_id in claim_ids:
        if not ctx.claims.comparison_target(claim_id):
            raise OperationError(f'Claim not found: {claim_id}')


# --- kind-aware lifecycle helpers (Phase 3) --------------------------------- #
# The status-changing operations (ARCHIVE / RESTORE / ACCEPT) used to be
# claim-only. Knowledge now has five shapes (claim / entity / relation / idea /
# question), and the HTTP status PATCH is the single write entry that must reach
# them all through the Operation framework. A handler accepts *either* the legacy
# ``claim_id`` (claim-only, backward compatible) *or* the polymorphic
# ``kind`` + ``item_id`` pair, so no existing caller breaks while the framework
# gains coverage.

def _target(ctx: _Context) -> tuple[str, str]:
    """Resolve (kind, item_id) from a payload, honouring the legacy claim_id."""
    claim_id = ctx.payload.get('claim_id')
    if claim_id:
        return 'claim', claim_id
    return ctx.require('kind'), ctx.require('item_id')


def _status_vocabulary(kind: str) -> set[str]:
    if kind in ('claim', 'entity', 'relation'):
        return set(KNOWLEDGE_STATUSES)
    if kind == 'idea':
        return set(IDEA_STATUSES)
    if kind == 'question':
        return set(QUESTION_STATUSES)
    raise OperationError(f'Unsupported knowledge kind: {kind}')


def _set_lifecycle(ctx: _Context, kind: str, item_id: str, status: str) -> dict:
    """Move one knowledge object's lifecycle status. The only place a status write
    happens for any kind — no repository is touched outside ``run``."""
    if kind == 'claim':
        rowcount = ctx.claims.set_status(item_id, status)
    elif kind == 'entity':
        rowcount = ctx.entities.update(item_id, {'status': status})
    elif kind == 'relation':
        rowcount = ctx.relations.set_status(item_id, status)
    elif kind == 'idea':
        rowcount = IdeaRepository(ctx.conn).set_status(item_id, status)
    elif kind == 'question':
        rowcount = QuestionRepository(ctx.conn).set_status(item_id, status)
    else:
        raise OperationError(f'Unsupported knowledge kind: {kind}')
    if not rowcount:
        raise OperationError(f'{kind} not found: {item_id}')
    return {'id': item_id, 'status': status, 'kind': kind}


def _new_claim(ctx: _Context) -> str:
    """Shared claim creation for CREATE / CORRECT. Provenance is mandatory.

    This is the last door before storage, so the ontology is re-checked *here* and
    not only in the compiler upstream: every door may be reached directly (the
    API, a workflow, a future agent), and a claim carrying an invented predicate
    would contaminate the registry for every reader afterwards. A declared alias
    is folded in — ``CEO`` is registry data, not a new predicate — while anything
    undeclared is refused with a 422 rather than silently rewritten.
    """
    p = ctx.payload
    subject_id = ctx.entity_for(subject_id=p.get('subject_id'), name=p.get('subject'))
    try:
        predicate = canonical_claim_predicate(ctx.require('predicate'))
    except ValueError as exc:
        raise OperationError(str(exc)) from exc
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
        source_quote=p.get('source_quote'),
        # Stamp the ontology that was in force, so a later registry change can be
        # explained rather than guessed at (ADR-015).
        ontology_version=claim_registry_version())


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


@register_operation('ACCEPT')
def _accept(ctx: _Context) -> dict:
    """Accept a knowledge object as settled: move its lifecycle, nothing else.

    For a *claim* this is the research-candidate promotion behind 「采纳」 — bounded by
    the same four rules (still ``candidate``; research-sourced; evidenced; registered
    predicate). For the other kinds (entity / relation / idea / question) there is no
    such proposal gate: accepting is simply setting a positive status, validated
    against that kind's own vocabulary. Either way only lifecycle moves; no content,
    object, evidence or provenance is rewritten.

    Polymorphic on ``kind`` + ``item_id`` (or the legacy ``claim_id``); the write goes
    through :func:`_set_lifecycle` so no repository is touched outside ``run``.
    """
    kind, item_id = _target(ctx)
    status = str(ctx.payload.get('status') or 'verified')
    if kind == 'claim':
        _assert_claims(ctx, item_id)
        claim = ctx.claims.get(item_id) or {}
        if claim.get('status') != 'candidate':
            raise OperationError(
                f'Only a proposed claim can be accepted (status={claim.get("status")})')
        document = ctx.documents.get(claim.get('source_document_id') or '') or {}
        if document.get('source_type') != 'research':
            raise OperationError('ACCEPT applies to research candidates only')
        if not str(claim.get('source_quote') or '').strip():
            raise OperationError('A candidate without evidence cannot be accepted')
        predicate = claim.get('predicate') or ''
        if claim_predicate_spec(predicate) is None:
            raise OperationError(f'Unregistered claim predicate: {predicate!r}')
        if status not in KNOWLEDGE_STATUSES:
            raise OperationError(f'Unsupported claim status: {status}')
        previous = ctx.claims.status_of(item_id)
        ctx.claims.set_status(item_id, status)
        return {'claim_id': item_id, 'status': status, 'previous_status': previous,
                'research_task_id': str(document.get('source_uri') or '').removeprefix('research:')}
    # Non-claim kinds: a plain lifecycle promotion, vocabulary-checked.
    if status not in _status_vocabulary(kind):
        raise OperationError(f'Unsupported {kind} status: {status}')
    result = _set_lifecycle(ctx, kind, item_id, status)
    result['previous_status'] = None
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
    """Archive any knowledge object (claim / entity / relation / idea / question).

    Keeps the legacy ``claim_id`` path so existing callers are unaffected.
    """
    kind, item_id = _target(ctx)
    if kind == 'claim':
        _assert_claims(ctx, item_id)
    previous = ctx.claims.status_of(item_id) if kind == 'claim' else None
    result = _set_lifecycle(ctx, kind, item_id, 'archived')
    if previous is not None:
        result['previous_status'] = previous
    return result


@register_operation('RESTORE')
def _restore(ctx: _Context) -> dict:
    """Restore any knowledge object to a live status (default ``candidate``).

    Keeps the legacy ``claim_id`` path so existing callers are unaffected.
    """
    kind, item_id = _target(ctx)
    target = ctx.payload.get('status') or 'candidate'
    if target not in _status_vocabulary(kind):
        raise OperationError(f'Invalid restore status: {target}')
    if kind == 'claim':
        _assert_claims(ctx, item_id)
    return _set_lifecycle(ctx, kind, item_id, target)


@register_operation('RELATION_RESOLVE')
def _resolve_relation(ctx: _Context) -> dict:
    """Resolve a suggested claim-to-claim change — the Operation behind the
    ``PATCH /api/claim-relations/{id}`` write entry.

    Moves only lifecycle/relationship, never content. Recording the previous status
    keeps the decision exactly undoable: backing away from a confirmed supersession
    restores the older claim.
    """
    relation_id = ctx.require('relation_id')
    new_status = ctx.require('status')
    if new_status not in ('accepted', 'rejected', 'candidate'):
        raise OperationError('status must be accepted, rejected or candidate')
    relationship = ctx.payload.get('relationship')
    if relationship is not None and relationship not in RELATIONSHIPS:
        raise OperationError(f'Unknown relationship: {relationship}')
    rel = ctx.claims.relation(relation_id)
    if not rel:
        raise OperationError('Claim relation not found')
    ctx.claims.update_relation(relation_id, status=new_status, relationship=relationship)
    if relationship == 'supersedes' and new_status == 'accepted':
        older = ctx.claims.status_of(rel['target_claim_id'])
        ctx.claims.set_relation_previous_status(relation_id, older)
        ctx.claims.set_status(rel['target_claim_id'], 'superseded')
    elif rel.get('target_previous_status'):
        # Any retreat from a confirmed supersession restores the older claim.
        ctx.claims.restore_status(rel['target_claim_id'], rel['target_previous_status'],
                                  only_if='superseded')
    return {'id': relation_id, 'status': new_status, 'relationship': relationship}


@register_operation('MERGE_ENTITY')
def _merge_entity(ctx: _Context) -> dict:
    """Confirm a merge of two entities into one subject — the Operation behind the
    ``POST /api/integrity/merge`` write entry.

    Delegates to the integrity module, which repoints claims/relations and never
    picks a winner between conflicting facts. Runs inside the caller's transaction
    (``ctx.conn``) so the repoint and the recheck are atomic with the audit record.
    """
    keep_id = ctx.require('keep_id')
    drop_id = ctx.require('drop_id')
    # Imported lazily: ``integrity`` touches repositories and is large; pulling it in
    # at module load would re-introduce the domain/operations import surface for no
    # reason, and keeps this handler self-contained.
    from ..integrity import merge_entities as _merge_entities
    return _merge_entities(keep_id=keep_id, drop_id=drop_id, conn=ctx.conn)


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
