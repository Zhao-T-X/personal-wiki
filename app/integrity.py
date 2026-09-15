"""Knowledge Integrity: make a hidden data problem visible, explain it, and let a
human fix it with an operation that already exists.

Not a cleanup engine, and deliberately not automatic. Three rules shape everything
here:

* **Detect, explain, suggest, confirm.** A duplicate entity is *offered* with its
  impact computed before anything is written. Nothing in this module decides that two
  entities are the same thing, or which of two conflicting claims is right.
* **A fix may surface a new problem, and saying so is part of the fix.** Merging two
  entities does not make knowledge consistent: it moves claims under one subject, and
  an entity split that was hiding a disagreement becomes an explicit factual conflict.
  That conflict is reported *and* recorded as a candidate relation for the Conflict
  Center, exactly as import would have recorded it. Hiding it would be the worst
  outcome — the user would have "fixed" something and unknowingly broken it.
* **Reuse, never re-decide.** ``compare_claim`` is the single verdict on how two
  claims relate; ``record_relations`` is the single rule for writing one. A second
  opinion here would let the integrity report and the import path disagree.

Responsibilities that must stay apart:

    Entity Merge         -> one subject for two rows
    Conflict Detection   -> finds that two facts cannot both hold
    SUPERSEDE/CONTRADICT -> decides which one holds (a human confirms)
    ClaimStateResolver   -> reads the resulting current state

A merge that also picked a winner would be doing the Conflict Center's job with none
of its history.

Business module: no SQL here, only repositories (tests/test_architecture.py).
"""
from __future__ import annotations

import uuid
from collections import defaultdict

from .claim_relations import FUNCTIONAL_PREDICATES, compare_claim, record_relations
from .db import loads, transaction
from .domain.claim_state import is_current
from .ontology import claim_predicate_spec, normalize_name
from .repositories import (ClaimRepository, CurationRepository, EntityRepository,
                           OperationRepository, RelationRepository)
from .repositories.curation_repo import NOT_SAME, canonical_pair
from .resolution import name_similarity, scan_duplicate_entities

# How sure the system is that a free-text object refers to an existing entity.
HIGH, MEDIUM = 'high', 'medium'
_CONFIDENCE_RANK = {HIGH: 0, MEDIUM: 1}
# Below this, a name is not similar enough to be worth offering as a suggestion.
_SUGGEST_THRESHOLD = 0.65


def _predicate_label(predicate: str) -> str | None:
    spec = claim_predicate_spec(predicate or '')
    return (spec.label or None) if spec else None


def _object_label(claim: dict) -> str:
    return str(claim.get('object_name') or claim.get('object_text') or '')


def _conflict_pairs(claims: list[dict]):
    """Every pair of *current* claims that cannot both hold, with its verdict.

    The verdict comes from ``claim_relations.compare_claim`` — the one definition of
    how two claims relate — so an integrity report shows exactly the conflicts the
    import path would have recorded instead of a second, drifting opinion.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for claim in claims:
        if is_current(claim):
            groups[(claim.get('subject_id'), claim.get('predicate'))].append(claim)
    for (_, predicate), group in groups.items():
        for i, first in enumerate(group):
            for second in group[i + 1:]:
                verdict = compare_claim(first, second)
                if verdict['relationship'] == 'contradicts':
                    yield first, second, verdict


def _conflict_view(first: dict, second: dict, verdict: dict) -> dict:
    """A conflict as the product talks about it — subject, values, and that's it."""
    predicate = first.get('predicate') or ''
    return {
        'subject_id': first.get('subject_id'),
        'subject_name': first.get('subject_name') or second.get('subject_name') or '',
        'predicate': predicate,
        'predicate_label': _predicate_label(predicate),
        'objects': [_object_label(first), _object_label(second)],
        'claim_ids': [first.get('id'), second.get('id')],
        'functional': predicate in FUNCTIONAL_PREDICATES,
        'confidence': verdict['confidence'],
        'reason': verdict['reason'],
    }


def potential_conflicts(claims: list[dict]) -> list[dict]:
    """Conflicts that exist among these claims right now."""
    return [_conflict_view(a, b, v) for a, b, v in _conflict_pairs(claims)]


def _conflict_key(conflict: dict) -> tuple:
    return (conflict['predicate'], tuple(sorted(conflict['objects'])), conflict['subject_id'])


def _after_merge(claims: list[dict], keep_id: str, drop_id: str) -> list[dict]:
    """The same claims as they would look with the merge applied — nothing written.

    A preview has to be a real dry run: recomputing conflicts on rewritten *copies*
    is what lets the preview state a number ("this will create 1 conflict") instead of
    warning vaguely, which is the only version of the warning a user can act on.
    """
    out = []
    for claim in claims:
        rewritten = {**claim}
        if rewritten.get('subject_id') == drop_id:
            rewritten['subject_id'] = keep_id
        if rewritten.get('object_id') == drop_id:
            rewritten['object_id'] = keep_id
        out.append(rewritten)
    return out


def _range_accepts(predicate: str, entity_types: set[str]) -> bool:
    """Whether the registry lets this predicate point at that *kind* of thing.

    ``range`` is ontology data, so this is a registry lookup rather than a rule of its
    own. An empty range means unconstrained, and an untyped entity counts as
    compatible — "unknown" is not evidence against.
    """
    spec = claim_predicate_spec(predicate or '')
    allowed = set(spec.range) if spec else set()
    return not allowed or not entity_types or bool(allowed & entity_types)


def entity_pool() -> list[dict]:
    """Every entity with its declared types, in one query.

    The comparison target for both the scan and the pipeline link pass: names for
    matching, types for the range check.
    """
    return [{'id': r['id'], 'name': r['name'], 'status': r.get('status'),
             'types': set(loads(r.get('types_json') or '[]', []) or ([r['type']] if r.get('type') else []))}
            for r in EntityRepository().similarity_pool(None, 500)]


def link_proposal(claim: dict, resolved: dict[str, list[str]], pool: list[dict]) -> dict | None:
    """Where a free-text object could point, and how sure the system is.

    The tier *is* the message:

    * ``high`` — the text is exactly one entity's name or declared alias, and that
      entity's kind is allowed by the predicate's declared range. Same comparison the
      write path performs, so this is a lookup result, not a guess. This is the only
      tier anything may act on by itself.
    * ``medium`` — several entities claim the text, or a similar name exists. Offered
      with its candidates and **no target**: nobody has chosen, so there is nothing
      safe to apply.
    * ``None`` — nothing found, or the only match is the wrong kind of thing. Not
      reported: an unresolvable literal is not a problem to fix, and a list padded
      with dead ends is a list people learn to ignore.
    """
    text = str(claim.get('object_text') or '').strip()
    if not text:
        return None
    predicate = claim.get('predicate') or ''
    base = {
        'claim_id': claim.get('id'),
        'subject_label': claim.get('subject_name') or '',
        'predicate': predicate,
        'predicate_label': _predicate_label(predicate),
        'object_text': text,
        'statement': claim.get('content') or '',
    }
    by_id = {c['id']: c for c in pool}
    matched = [by_id[i] for i in (resolved.get(text.lower()) or resolved.get(normalize_name(text)) or [])
               if i in by_id]

    # The registry has the last word: a Person cannot fill a slot declared for an
    # Organization, so that pairing is not a candidate at all.
    matched = [c for c in matched if _range_accepts(predicate, c['types'])]
    if len(matched) == 1:
        target = matched[0]
        return {**base, 'confidence': HIGH, 'matched_by': 'exact',
                'target': {'id': target['id'], 'name': target['name']},
                'candidates': [], 'similarity': 1.0}
    if len(matched) > 1:
        return {**base, 'confidence': MEDIUM, 'matched_by': 'ambiguous', 'target': None,
                'candidates': [{'id': c['id'], 'name': c['name'], 'similarity': 1.0} for c in matched],
                'similarity': 1.0}

    best = None
    for cand in pool:
        if cand.get('status') == 'archived' or not _range_accepts(predicate, cand['types']):
            continue
        score = name_similarity(text, cand['name'])
        if score >= _SUGGEST_THRESHOLD and (best is None or score > best['similarity']):
            best = {'id': cand['id'], 'name': cand['name'], 'similarity': round(score, 3)}
    if not best:
        return None
    return {**base, 'confidence': MEDIUM, 'matched_by': 'similar', 'target': None,
            'candidates': [best], 'similarity': best['similarity']}


def _audit_links(conn, links: list[dict], *, reason: str) -> None:
    """Record automatic links where every other knowledge write is recorded.

    One row per link, so a later question — "why is this claim linked to Tim Cook?" —
    is answered from storage instead of by re-deriving the rules: this extraction
    matched the entity's declared alias exactly, and here is the trace id.
    """
    ops = OperationRepository(conn)
    for link in links:
        ops.record(op_id=str(uuid.uuid4()), kind='LINK_OBJECT', actor='system',
                   status='applied', reason=reason,
                   payload={'claim_id': link['claim_id'], 'entity_id': link['entity_id'],
                            'object_text': link['object_text'], 'predicate': link['predicate'],
                            'matched_by': link['matched_by'], 'confidence': 1.0,
                            'trace_id': link['trace_id']},
                   result={'linked': True})


def autolink_object_literals(conn, *, document_id: str) -> dict:
    """L1: link a document's free-text objects to entities that already exist.

    Runs *inside the pipeline* — right after the document's claims and relations are
    written — and never at start-up. An automatic write belongs where its cause is:
    the run that produced the claim explains the change, and a scan of the whole
    database at boot has neither a cause nor a bounded blast radius.

    Strictly the unambiguous case qualifies, all of it:

    * the text is exactly one entity's name or declared alias (two candidates is a
      question, not a link);
    * the predicate's declared range accepts that entity's kind;
    * nothing is created and nothing is merged — only an id is attached;
    * the claim's own words are untouched, and a claim already linked is skipped, so
      running twice changes nothing.

    Everything else is left to the suggestion path (L2). A guess written automatically
    is the one mistake this module must not make.
    """
    claims = ClaimRepository(conn).unlinked_object_claims_for_document(document_id)
    if not claims:
        return {'linked': 0, 'considered': 0, 'trace_id': None}

    texts = [str(c.get('object_text') or '').strip() for c in claims]
    resolved = EntityRepository(conn).resolve_many([t for t in texts if t])
    pool = entity_pool()
    trace_id = f'link:{document_id}'
    links, linked = [], []
    for claim in claims:
        proposal = link_proposal(claim, resolved, pool)
        if not proposal or proposal['confidence'] != HIGH or not proposal.get('target'):
            continue
        links.append((proposal['claim_id'], proposal['target']['id']))
        linked.append({'claim_id': proposal['claim_id'],
                       'entity_id': proposal['target']['id'],
                       'entity_name': proposal['target']['name'],
                       'object_text': proposal['object_text'],
                       'predicate': proposal['predicate'],
                       'matched_by': proposal['matched_by'],
                       'trace_id': trace_id})

    written = ClaimRepository(conn).link_objects(links)
    if written:
        _audit_links(conn, linked, reason='抽取时对象字面量精确匹配到既有实体')
    return {'linked': written, 'considered': len(claims), 'links': linked,
            'trace_id': trace_id if written else None}


def scan(*, duplicate_limit: int = 20, unlinked_limit: int = 50) -> dict:
    """Everything the integrity check can currently see.

    Two findings, both L2 — explain and confirm. Neither is a defect on its own: a
    literal object is legitimate, and two similar names are usually two things. The
    scan reports candidates and their confidence, never errors.

    Name similarity alone is cheap and noisy — a document path contains another
    document path, a table contains its FTS index — so each pair also carries how much
    knowledge hangs off each side, and is ordered by that. Pairs with no knowledge on
    either side are left out (their count is still reported, so nothing is hidden
    silently): merging two entities nobody has said anything about changes nothing,
    and a list padded with those is a list nobody reads.

    Queries are fixed regardless of wiki size: the entity pool, the duplicate pairs,
    the claim counts, the unlinked claims, and their batch name resolution.
    """
    # Decided pairs are dropped *before* the limit applies: a pair the user has
    # already ruled out must never push an unknown one out of the queue. That is the
    # whole point of remembering the decision.
    candidates = scan_duplicate_entities(None, pair_limit=max(duplicate_limit * 4, 40))
    decided = CurationRepository().decided_pairs(NOT_SAME)
    fresh = [p for p in candidates
             if canonical_pair(p['entity_a']['id'], p['entity_b']['id']) not in decided]

    pool = entity_pool()
    claims = ClaimRepository().unlinked_object_claims(unlinked_limit)
    resolved = EntityRepository().resolve_many([c['object_text'] for c in claims])
    proposals = [p for p in (link_proposal(c, resolved, pool) for c in claims) if p]

    counts = ClaimRepository().claim_counts_by_entity(
        [e['id'] for p in fresh for e in (p['entity_a'], p['entity_b'])])
    kept, empty = [], 0
    for pair in fresh:
        a, b = counts.get(pair['entity_a']['id'], 0), counts.get(pair['entity_b']['id'], 0)
        if not (a or b):
            empty += 1
            continue
        kept.append({**pair, 'entity_a': {**pair['entity_a'], 'claims': a},
                     'entity_b': {**pair['entity_b'], 'claims': b}})
    # Most knowledge at stake first, similarity as the tie-break.
    kept.sort(key=lambda p: (-(p['entity_a']['claims'] + p['entity_b']['claims']),
                             -p['similarity']))
    return {
        'duplicate_entities': kept[:duplicate_limit],
        'unlinked_claims': proposals,
        'counts': {'duplicate_entities': len(kept[:duplicate_limit]),
                   'unlinked_claims': len(proposals),
                   'hidden_no_knowledge': empty,
                   'hidden_decided': len(candidates) - len(fresh)},
    }


# --- curation memory ----------------------------------------------------------

def record_not_same(*, entity_id_a: str, entity_id_b: str, reason: str | None = None,
                    trace_id: str | None = None) -> dict:
    """Remember that two entities are *not* the same thing.

    A curation decision, not knowledge: it says nothing about the world, only that
    this pair should stop being proposed. Recorded so the system stops asking, and
    revocable because the judgement can turn out to be wrong.
    """
    return CurationRepository().decide(entity_id_a=entity_id_a, entity_id_b=entity_id_b,
                                       decision=NOT_SAME, reason=reason, trace_id=trace_id)


def curation_decisions(limit: int = 100) -> list[dict]:
    """What has been decided, newest first — so it can be seen and undone."""
    return CurationRepository().list(limit)


def revoke_curation(decision_id: str) -> dict:
    """Undo a decision. The pair goes back to being an ordinary candidate."""
    removed = CurationRepository().revoke(decision_id)
    if not removed:
        raise ValueError('Curation decision not found')
    return {'revoked': removed, 'decision_id': decision_id}


def merge_impact(*, keep_id: str, drop_id: str) -> dict:
    """What a merge would touch, before it touches anything — the dry run.

    The number that matters is ``new_conflicts``. Merging reconciles nothing: two
    entities that each held one value of a single-valued relation become one subject
    holding two. Promising that up front is the difference between a repair the user
    trusts and one that quietly breaks a fact.
    """
    entities = EntityRepository()
    keep, drop = entities.get(keep_id), entities.get(drop_id)
    if not keep or not drop:
        raise ValueError('Entity not found')
    if keep_id == drop_id:
        raise ValueError('An entity cannot be merged with itself')

    claims = ClaimRepository().claims_touching([keep_id, drop_id])
    relations = RelationRepository().incident([keep_id, drop_id], 200)
    existing = potential_conflicts(claims)
    existing_keys = {_conflict_key(c) for c in existing}
    new = [c for c in potential_conflicts(_after_merge(claims, keep_id, drop_id))
           if _conflict_key(c) not in existing_keys]

    keep_types, drop_types = set(keep.get('types') or []), set(drop.get('types') or [])
    incompatible = bool(keep_types and drop_types and not keep_types & drop_types)
    return {
        'keep': {'id': keep_id, 'name': keep['name']},
        'drop': {'id': drop_id, 'name': drop['name']},
        'affected_claims': len(claims),
        'affected_relations': len(relations),
        'affected_evidence': sum(1 for c in claims if c.get('source_quote')),
        'claims_moved': sum(1 for c in claims
                            if c.get('subject_id') == drop_id or c.get('object_id') == drop_id),
        'existing_conflicts': existing,
        'new_conflicts': new,
        # Reported, never enforced: two similar names of different types are often
        # two things, and blocking on a type mismatch would stop the human from
        # fixing a mis-typed entity by merging it into the right one.
        'type_mismatch': incompatible,
    }


def _tombstone(name: str, keep_name: str) -> str:
    """The name left behind by a merged entity. Kept so the row stays auditable, and
    renamed so it cannot shadow exact-name resolution for the survivor."""
    return f'{name}（已并入 {keep_name}）'


def merge_entities(*, keep_id: str, drop_id: str) -> dict:
    """Perform a confirmed merge, then report what it disturbed.

    A merge is only allowed to change *which subject* a statement is filed under. It
    does not touch a claim's content, status, predicate or evidence, and it never picks
    a winner — so the worst it can do is make an existing disagreement visible. That is
    why the recheck is part of the response rather than a follow-up job the user has to
    remember to run.
    """
    impact = merge_impact(keep_id=keep_id, drop_id=drop_id)
    entities = EntityRepository()
    keep, drop = entities.get(keep_id), entities.get(drop_id)
    aliases = sorted({*(keep.get('aliases') or []), *(drop.get('aliases') or []),
                      str(keep['name']), str(drop['name'])})

    with transaction() as conn:
        moved_claims = ClaimRepository(conn).repoint_entity(drop_id, keep_id)
        moved_relations = RelationRepository(conn).repoint_entity(drop_id, keep_id)
        # The dropped name becomes an alias of the survivor. Without this the next
        # extraction that mentions it would recreate the very duplicate being fixed.
        EntityRepository(conn).update(keep_id, {}, aliases=aliases, normalize=normalize_name)
        EntityRepository(conn).update(drop_id, {'name': _tombstone(str(drop['name']), str(keep['name'])),
                                                'status': 'archived'})
        EntityRepository(conn).replace_aliases(drop_id, [], normalize_name)

    recheck = recheck_after_merge([keep_id])
    return {
        'merged': {'keep_id': keep_id, 'drop_id': drop_id, 'keep_name': keep['name'],
                   'drop_name': drop['name'], 'moved_claims': moved_claims,
                   'moved_relations': moved_relations, 'aliases_added': len(aliases)},
        'impact': impact,
        # The findings *after* the merge, not the prediction: the preview can be wrong
        # about which claims were affected, and the user must see what actually happened.
        'recheck': recheck,
    }


def recheck_after_merge(entity_ids: list[str]) -> dict:
    """Re-examine the claims a merge moved and record what it disturbed.

    This is the core of the operation rather than an afterthought: the merge resolves
    nothing on purpose, so the point here is to say out loud which factual conflicts
    now exist and hand them to the Conflict Center — as a *candidate* relation, which
    changes no fact and is exactly how an import would have recorded the same finding.
    """
    claims_repo = ClaimRepository()
    touched = claims_repo.claims_touching(entity_ids)
    conflicts, recorded = [], 0
    for first, second, verdict in _conflict_pairs(touched):
        conflicts.append(_conflict_view(first, second, verdict))
        recorded += len(record_relations(claims_repo, first, [verdict]))
    return {'conflicts': conflicts, 'recorded_relations': recorded,
            'claims_examined': len(touched)}


def apply_object_links(*, min_confidence: str = HIGH, limit: int = 200) -> dict:
    """Link free-text objects to entities that already exist.

    Only ever *links*. It never creates an entity: an integrity fix that invented
    entities would feed the extraction path's next round of duplicates, which is the
    problem this module exists to reduce. An unmatched literal is left alone — the
    third tier is doing nothing.
    """
    ceiling = _CONFIDENCE_RANK.get(min_confidence, 0)
    proposals = [p for p in scan(unlinked_limit=limit)['unlinked_claims']
                 if _CONFIDENCE_RANK.get(p['confidence'], 9) <= ceiling]
    # A medium proposal has candidates but no chosen target: the human has not picked
    # one, so there is nothing safe to apply.
    chosen = [p for p in proposals if p.get('target')]
    links = [(p['claim_id'], p['target']['id']) for p in chosen]
    applied = ClaimRepository().link_objects(links)
    if applied:
        # The pipeline links automatically; this tool exists for data that predates it.
        # Both leave the same audit trail, so "why is this claim linked?" has one answer
        # regardless of which path did it.
        with transaction() as conn:
            _audit_links(conn, [{'claim_id': p['claim_id'],
                                 'entity_id': p['target']['id'],
                                 'entity_name': p['target']['name'],
                                 'object_text': p['object_text'],
                                 'predicate': p['predicate'],
                                 'matched_by': f"exact_{p['confidence']}",
                                 'trace_id': 'repair:object-links'} for p in chosen],
                         reason='历史数据补齐对象关联')
    return {'applied': applied, 'considered': len(proposals),
            'skipped_unconfirmed': len(proposals) - len(chosen),
            'claim_ids': [p['claim_id'] for p in chosen]}
