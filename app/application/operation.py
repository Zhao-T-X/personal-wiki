"""Operation Application — the write authority for knowledge.

The API must never mutate knowledge state through the repositories directly. A write
reaches the database only by submitting an Operation to ``domain.operations.run``.
Every knowledge-changing entry point in this module funnels into that single door:

* **Operation-executed** — submitted straight to ``run`` (CREATE / CORRECT / SUPERSEDE
  / MERGE / DUPLICATE / CONTRADICT, plus the Phase 3 RELATION_RESOLVE and
  MERGE_ENTITY). ``apply_operation`` is the front door.
* **Lifecycle writes expressed as Operations** — the status PATCH, claim-relation
  resolution and entity merge. They build an ``OperationRequest`` (ARCHIVE / RESTORE /
  RELATION_RESOLVE / MERGE_ENTITY) and call ``run`` themselves; the only repository
  write for knowledge happens inside ``run``. There is no second write authority and
  no repository touched behind this layer.

No FastAPI, no ``sqlite3`` here — only domain, repositories and sibling application
modules. Errors are raised as ``ApplicationError`` subclasses for the adapter to map.
"""
from __future__ import annotations

from ..claim_relations import RELATIONSHIPS
from ..db import transaction
from ..domain.operations import OperationError, OperationRequest, run
from ..review import issues_for_claims
from . import BadRequestError, NotFoundError, ValidationError


def apply_operation(payload: dict) -> dict:
    """Execute a knowledge operation through ``domain.operations.run``.

    Returns the operation result plus the post-operation integrity recheck, exactly as
    the old ``/api/knowledge/operations`` handler did — only the repository writes now
    live in the domain, never in the route.
    """
    body = payload or {}
    kind = body.get('kind')
    if not kind:
        raise ValidationError('kind is required')
    try:
        with transaction() as conn:
            result = run(
                OperationRequest(
                    kind=kind,
                    payload=body.get('payload') or {},
                    actor=body.get('actor', 'user'),
                    reason=body.get('reason', ''),
                ),
                conn,
            )
    except OperationError as exc:
        raise  # already a domain ValidationError-equivalent (ValueError subclass) -> 422
    touched = [str(v) for k, v in (result.affected or {}).items()
               if v and 'claim' in str(k)]
    return {
        'operation_id': result.operation_id,
        'kind': result.kind,
        'affected': result.affected,
        'issues': issues_for_claims(touched),
    }


def update_knowledge_status(kind: str, item_id: str, status: str) -> dict:
    """Change a knowledge object's lifecycle status — always via an Operation.

    ``archived`` becomes an ``ARCHIVE`` operation; every other status becomes a
    ``RESTORE`` (which carries the target status). The framework already covers all
    five knowledge shapes (claim / entity / relation / idea / question), so the route
    handler performs no data access and there is no second write authority.
    """
    from ..ontology import (IDEA_STATUSES, KNOWLEDGE_STATUSES, QUESTION_STATUSES)

    allowed = (KNOWLEDGE_STATUSES if kind in {'entity', 'claim', 'relation'}
               else IDEA_STATUSES if kind == 'idea'
               else QUESTION_STATUSES if kind == 'question'
               else set())
    if status not in allowed:
        raise ValidationError('Invalid status')
    if kind not in {'entity', 'claim', 'relation', 'idea', 'question'}:
        raise BadRequestError('Unsupported knowledge kind')

    op_kind = 'ARCHIVE' if status == 'archived' else 'RESTORE'
    payload = {'kind': kind, 'item_id': item_id}
    if op_kind == 'RESTORE':
        payload['status'] = status
    try:
        with transaction() as conn:
            run(OperationRequest(kind=op_kind, payload=payload, actor='user', reason=''),
                conn)
    except OperationError as exc:
        # The only domain error a lifecycle operation raises here is "not found".
        raise NotFoundError(str(exc)) from exc
    return {'id': item_id, 'status': status}


def resolve_claim_relation(relation_id: str, body: dict) -> dict:
    """Accept / reject / re-relate a suggested claim-to-claim change — via an Operation.

    Moving a claim's *lifecycle status* — never its text, object, evidence or
    provenance — and recording the previous status so the decision can be undone exactly.
    The write now lives in ``domain.operations`` (RELATION_RESOLVE); this layer only
    validates and submits.
    """
    new_status = body.get('status')
    if new_status not in ('accepted', 'rejected', 'candidate'):
        raise ValidationError('status must be accepted, rejected or candidate')
    relationship = body.get('relationship')
    if relationship is not None and relationship not in RELATIONSHIPS:
        raise ValidationError(f'Unknown relationship: {relationship}')
    try:
        with transaction() as conn:
            run(OperationRequest(
                    kind='RELATION_RESOLVE',
                    payload={'relation_id': relation_id, 'status': new_status,
                             'relationship': relationship},
                    actor='user', reason=''),
                conn)
    except OperationError as exc:
        raise NotFoundError(str(exc)) from exc
    return {'id': relation_id, 'status': new_status, 'relationship': relationship}


def merge_entities(keep_id: str, drop_id: str) -> dict:
    """Confirm a merge of two entities into one subject — via an Operation.

    Delegates to ``domain.operations`` (MERGE_ENTITY), which runs the integrity merge
    inside the caller's transaction. The returned dict is the merge impact/recheck,
    identical in shape to before; the only change is that the write is now audited as
    an Operation rather than a direct repository repoint.
    """
    with transaction() as conn:
        result = run(OperationRequest(
                        kind='MERGE_ENTITY',
                        payload={'keep_id': keep_id, 'drop_id': drop_id},
                        actor='user', reason=''),
                    conn)
    return result.affected
