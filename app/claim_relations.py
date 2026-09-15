"""Claim-to-claim relationships: duplicate / coexists / supersedes / contradicts.

Never overwrite a claim. When new knowledge arrives it becomes a *new* row and
the relationship to what we already knew is modelled explicitly, so history,
evidence and provenance all survive. Current knowledge is then derived from the
claims plus these relationships, not stored by destroying the old row.

Deterministic first (matching the project's rule that logic lives in code and the
LLM only judges meaning): every verdict below comes from stored structure —
entity resolution, predicate, polarity, resolved object — so the same input
always yields the same answer and nothing is mutated behind the user's back.

What this deliberately does *not* decide: whether a newer claim replaces an older
one. That needs temporal or textual evidence, so `supersedes` is never inferred
here — a single-valued predicate whose object changed is surfaced as
`contradicts` + `review`, and the human chooses. False certainty is worse than an
honest question.
"""
from __future__ import annotations

from .ontology import functional_claim_predicates
from .repositories import ClaimRepository

RELATIONSHIPS = ('duplicate', 'coexists', 'supersedes', 'contradicts', 'unclear')

# Predicates whose subject holds one value at a time. Two different objects then
# describe a knowledge *change* rather than two facts standing side by side.
#
# Derived from the registry, never hardcoded (ADR-011; task §31): whether a
# predicate is single-valued is ontology data. Changing this set means editing
# schemas/claim-predicate-registry.json, not Python. Predicates absent from it
# fall back to `coexists`, which destroys nothing and never asks the user to
# resolve a conflict that isn't one.
FUNCTIONAL_PREDICATES = functional_claim_predicates()

# Ranking for "most interesting relationship first" in a review queue.
_PRIORITY = {'duplicate': 0, 'supersedes': 1, 'contradicts': 2, 'coexists': 3, 'unclear': 4}

def _object_key(row: dict) -> str:
    """Normalised object identity: the resolved entity when there is one, else text.

    Going through the entity id (rather than the surface string) means
    "Apple Inc." and "Apple" compare equal once entity resolution has folded
    them together — which is the whole point of resolving first.
    """
    if row.get('object_id'):
        return f'entity:{row["object_id"]}'
    text = (row.get('object_text') or '').strip().casefold()
    return f'text:{text}' if text else ''


def _verdict(relationship: str, existing: dict, confidence: float,
             reason: str, action: str) -> dict:
    return {
        'relationship': relationship,
        'related_claim_id': existing.get('id'),
        'confidence': round(confidence, 3),
        'reason': reason,
        'suggested_action': action,
    }


def compare_claim(new: dict, existing: dict) -> dict:
    """Judge how `new` relates to one `existing` claim.

    Symmetric in the sense that the verdict does not depend on which claim was
    stored first; the returned `related_claim_id` is always ``existing['id']``.

    Returns ``{relationship, related_claim_id, confidence, reason, suggested_action}``.
    """
    same_subject = bool(new.get('subject_id')) and new.get('subject_id') == existing.get('subject_id')
    same_predicate = bool(new.get('predicate')) and new.get('predicate') == existing.get('predicate')
    if not (same_subject and same_predicate):
        return _verdict('unclear', existing, 0.0,
                        'Different subject or predicate — no structural basis to relate them.',
                        'ignore')

    new_object, old_object = _object_key(new), _object_key(existing)
    new_polarity = new.get('polarity') or 'positive'
    old_polarity = existing.get('polarity') or 'positive'

    if new_object and new_object == old_object:
        if new_polarity == old_polarity:
            return _verdict('duplicate', existing, 0.97,
                            'Same subject, predicate and object — the same statement, '
                            'asserted again by another source.',
                            'link_evidence')
        # Same statement asserted both true and false: no ordering reconciles it.
        return _verdict('contradicts', existing, 0.9,
                        f'The same statement is asserted as both {old_polarity} and '
                        f'{new_polarity}; the sources disagree.',
                        'review')

    if new_object and old_object:
        if new['predicate'] in FUNCTIONAL_PREDICATES:
            return _verdict('contradicts', existing, 0.55,
                            f'`{new["predicate"]}` holds one value at a time, but the object '
                            'changed. Compare the sources before treating either as current.',
                            'review')
        return _verdict('coexists', existing, 0.7,
                        'Same subject and predicate with a different object — both can hold '
                        'at the same time.',
                        'keep_both')

    # One side has no resolvable object (e.g. object is free text that never
    # resolved to an entity): too little structure to be sure of anything.
    return _verdict('unclear', existing, 0.2,
                    'Object missing or unresolved on one side — cannot be compared reliably.',
                    'review')


def compare_with_related(new_claim: dict, existing_claims: list[dict]) -> list[dict]:
    """Compare one claim against all candidates, most significant relationship first."""
    verdicts = [compare_claim(new_claim, other) for other in existing_claims]
    verdicts.sort(key=lambda v: (_PRIORITY.get(v['relationship'], 9), -v['confidence']))
    return verdicts


def find_related_claims(conn, claim_id: str, *, limit: int = 10) -> list[dict]:
    """Existing claims that share a subject and predicate with the given claim.

    Level 1/2 of the candidate strategy: entity resolution already folded
    "OpenAI Inc." into a single subject_id, so matching on ids is exact, indexed
    and cheap. Semantic fallback is intentionally absent — it costs an embedding
    call per claim, and structural recall has to prove insufficient first.
    """
    claims = ClaimRepository(conn)
    row = claims.comparison_target(claim_id)
    if not row:
        return []
    return claims.related(subject_id=row['subject_id'], predicate=row['predicate'],
                          exclude_id=claim_id, limit=max(1, min(limit, 50)))


def relation_status(suggested_action: str) -> str:
    """Recording a link (duplicate) or a coexistence is safe to automate — neither
    changes what the wiki asserts. Anything that could (a contradiction) waits for a
    human, so it is written as a candidate."""
    return 'accepted' if suggested_action in ('link_evidence', 'keep_both') else 'candidate'


def record_relations(claims, claim: dict, verdicts: list[dict]) -> list[dict]:
    """Write the verdicts for one claim that are not on record yet.

    Returns the ones actually written, so a caller can *report* what changed instead
    of guessing. Shared by import-time detection and the post-merge integrity
    recheck: a contradiction a merge surfaced has to be recorded exactly the way one
    found during import is, or the Conflict Center would show two kinds of conflict.
    """
    written: list[dict] = []
    for verdict in verdicts:
        if verdict['relationship'] == 'unclear':
            continue
        # One row per pair: whichever direction was detected first wins, so a
        # duplicate is not reported twice from both sides.
        if claims.relation_exists(verdict['related_claim_id'], claim['id']):
            continue
        relation_id = claims.insert_relation(
            source_claim_id=claim['id'], target_claim_id=verdict['related_claim_id'],
            relationship=verdict['relationship'], confidence=verdict['confidence'],
            reason=verdict['reason'], suggested_action=verdict['suggested_action'],
            status=relation_status(verdict['suggested_action']), created_by='system')
        if relation_id:
            written.append({**verdict, 'relation_id': relation_id, 'claim_id': claim['id']})
    return written


def detect_claim_relations(conn, *, document_id: str) -> int:
    """Record relationships between this document's claims and earlier ones.

    Runs inside the caller's transaction, so a claim can never be committed
    without the relation that explains it. Per-claim best effort: failing to
    understand one relationship must not lose the claim (design §26), so a
    problem on one row is swallowed and later rows still get their chance.

    Returns the number of relationships written.
    """
    claims = ClaimRepository(conn)
    written = 0
    for claim in claims.for_document_chronological(document_id):
        try:
            written += len(record_relations(
                claims, claim, compare_with_related(claim, find_related_claims(conn, claim['id']))))
        except Exception:  # noqa: BLE001 - never lose the claim over a relationship
            continue
    return written


_RELATION_SYSTEM = (
    'You compare two claims from a personal knowledge base and decide how the newer '
    'one relates to the earlier one. Reply with JSON only: '
    '{"relationship": "...", "confidence": 0.0-1.0, "reason": "..."} where relationship is one of: '
    'duplicate (the same statement, differently worded), '
    'coexists (both can be true at the same time), '
    'supersedes (the newer replaces the older — only when the evidence text actually says so), '
    'contradicts (they cannot both be true and nothing shows which is current), '
    'unclear (not enough information). '
    'Never invent facts that are not present in the supplied statements or evidence.'
)


async def analyze_relation(new_claim: dict, old_claim: dict) -> dict | None:
    """Ask the model to judge two claims — a suggestion, never a mutation.

    The deterministic rules above already settle duplicates, polarity conflicts and
    single-valued predicates. What they cannot read is meaning: whether two
    differently-worded statements describe the same thing, or whether a source
    states that the older value was replaced. That judgement is requested on
    demand, never during import, so adding a document costs no extra tokens.

    Returns the verdict, or None when the model is unavailable — the caller then
    keeps the deterministic result instead of failing.
    """
    import asyncio
    import json

    from .config import runtime
    from .llm import _client

    def _brief(c: dict) -> str:
        return (f"subject: {c.get('subject_name')}\n"
                f"predicate: {c.get('predicate')}\n"
                f"object: {c.get('object_name') or c.get('object_text')}\n"
                f"statement: {c.get('content') or ''}\n"
                f"evidence: {c.get('source_quote') or '(none)'}")

    prompt = (f"Earlier claim:\n{_brief(old_claim)}\n\n"
              f"Newer claim:\n{_brief(new_claim)}\n\n"
              'How does the newer claim relate to the earlier one?')

    def _call() -> dict | None:
        try:
            client = _client()
            response = client.chat.completions.create(
                model=runtime()['openai_model'], temperature=0.0,
                response_format={'type': 'json_object'},
                messages=[{'role': 'system', 'content': _RELATION_SYSTEM},
                          {'role': 'user', 'content': prompt}],
            )
            data = json.loads(response.choices[0].message.content or '{}')
        except Exception:
            return None
        relationship = str(data.get('relationship') or '').strip().lower()
        if relationship not in RELATIONSHIPS:
            return None
        try:
            confidence = float(data.get('confidence') or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            'relationship': relationship,
            'confidence': round(max(0.0, min(1.0, confidence)), 3),
            'reason': str(data.get('reason') or '').strip()[:500],
            'suggested_action': ('review' if relationship in ('contradicts', 'supersedes', 'unclear')
                                 else 'keep_both'),
        }

    return await asyncio.to_thread(_call)
