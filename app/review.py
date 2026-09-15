"""Review Inbox: how much is actually waiting on the user, and of what kind.

``/api/review`` answers "what does the database still hold unreviewed" — raw queues,
one list per table. This answers a different, product-level question: "how much work is
waiting for me right now". That number has to be one that cannot disagree with itself,
and two failure modes shape the code:

* **a total that omits a queue the page renders.** The badge says 2, the page lists 3,
  and from then on the user trusts neither — the counts were the cheap part, the
  trust was the expensive part.
* **a total that includes rows nobody can act on any more.** A conflict already
  resolved, a pair whose sides were rejected. An inbox that never empties is one
  people stop reading, and then the real findings go unread with it.

So every group is filtered to "still disposable, not yet decided", and the total is
the sum of the groups — the invariant is asserted in the tests rather than implied.

Low-risk maintenance (object-link suggestions) is reported *beside* the total instead
of inside it: sixteen suggestions are not sixteen decisions. Counting them would turn
a system that helps into one that hands out a daily work list (P2.5 §6).

The same module also builds the post-operation issue list, so merge, research and
correction describe what they disturbed in one language (``IntegrityIssue``) rather
than each inventing a sentence.

Business module: no SQL here, only repositories (tests/test_architecture.py).
"""
from __future__ import annotations

from .integrity import potential_conflicts
from .readmodels.integrity_issue import claim_conflict_issue
from .repositories import ClaimRepository, EntityRepository, RelationRepository

# The window each surface renders. These are *not* arbitrary: they are the defaults of
# the endpoints the Review page calls, because a badge must never promise more than the
# page it opens will show. Learned by running it — with independent limits the inbox
# reported 51 duplicate pairs while the panel listed 20, and the other 31 were
# unactionable exactly where the user had been sent to act on them.
#
# The fix is to widen these windows rather than narrow the count: the backlog is real,
# and a number that hides it is the thing this projection exists to stop.
_DUPLICATE_WINDOW = 200     # /api/integrity/scan `duplicates`
_UNLINKED_WINDOW = 200      # /api/integrity/scan `unlinked`
_QUEUE_WINDOW = 200         # /api/review `limit`
_RELATION_WINDOW = 200      # /api/claim-relations `limit`

# The groups that count as *work*, in the order the Review page shows them. Kept here
# so the total and the labels cannot drift apart.
WORK_GROUPS = ('entity_duplicates', 'claim_conflicts', 'entities', 'claims', 'relations')


def inbox() -> dict:
    """Everything waiting for a decision right now, grouped by what it is.

    ``duplicate_entities`` comes from the integrity scan rather than a fresh query, so
    the badge and the integrity panel cannot show different numbers for the same
    question — including the two reasons a pair is *not* shown: already ruled out
    ("not the same") and carrying no knowledge at all.
    """
    from .integrity import scan as integrity_scan

    findings = integrity_scan(duplicate_limit=_DUPLICATE_WINDOW,
                              unlinked_limit=_UNLINKED_WINDOW)
    groups = {
        'entity_duplicates': int(findings['counts']['duplicate_entities']),
        'claim_conflicts': len(ClaimRepository().pending_decisions(_RELATION_WINDOW)),
        'entities': len(EntityRepository().candidates(_QUEUE_WINDOW)),
        'claims': len(ClaimRepository().candidates(_QUEUE_WINDOW)),
        'relations': len(RelationRepository().candidates(_QUEUE_WINDOW)),
    }
    return {
        'total': sum(groups[k] for k in WORK_GROUPS),
        'groups': groups,
        # Offered, never counted: a literal object is not a defect, and ignoring one
        # forever costs nothing.
        'maintenance': {'object_link_suggestions': int(findings['counts']['unlinked_claims'])},
    }


def issues_for_claims(claim_ids: list[str]) -> list[dict]:
    """What a knowledge change disturbed, reported in the shared shape.

    Called *after* an operation that produced or moved knowledge, so the answer arrives
    with the thing that caused it instead of waiting for the user to go looking for it
    later. The detector is :func:`integrity.potential_conflicts` — the same verdict the
    import path records — so a merge, a research acceptance and an import cannot
    disagree about whether two statements conflict.

    Only current claims count, which is what makes this self-clearing: once the user
    retires one side of a disagreement, the issue stops being reported without anything
    having to remember that it was shown.
    """
    ids = [i for i in dict.fromkeys(claim_ids) if i]
    if not ids:
        return []
    # The touched claims *and their group*: a conflict is between this claim and
    # whatever else is filed under the same subject and predicate, which is usually not
    # something the operation touched.
    claims = ClaimRepository().current_counterparts(ids)
    return [claim_conflict_issue(c).to_dict() for c in potential_conflicts(claims)]
