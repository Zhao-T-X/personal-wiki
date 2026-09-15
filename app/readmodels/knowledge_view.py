"""How one piece of knowledge is shown — the shared shape for every surface.

Search, Knowledge QA, Research and the KnowledgeCard all need the same thing: a
claim rendered as something a person reads — a sentence, a state, and the evidence
behind it. That shape is a product decision, not a domain one, so it lives here.
This module decides nothing: every value is copied from something that already
decided it (the registry for the predicate's name, the stored lifecycle for the
state, the claim's own chunk for the evidence).

Two rules, both about not inventing anything:

* **A label is never synthesised.** A predicate the registry does not name comes
  back as ``None``, so no caller *can* print the identifier as prose (ADR-011: the
  registry is the only vocabulary). Falling back to the raw identifier here would
  put ``has_ceo`` in a sentence the first time a label is missing.
* **A state is never guessed.** ``current`` / ``candidate`` / ``disputed`` /
  ``historical`` come from the stored lifecycle plus an unadjudicated relation —
  and the same vocabulary decides the ordering.

Pure module: no database, no framework, no LLM (docs/adr/ADR-011).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..ontology import claim_predicate_spec

# The four states a reader has to tell apart.
#
# This wording is the *product's*, not ontology data: the registry owns the words
# for predicates and entity types, and has nothing to say about "is this still
# true". Keeping the phrasing in one place is what stops Search and Knowledge QA
# from each inventing a label for `superseded`.
CURRENT = 'current'
CANDIDATE = 'candidate'
DISPUTED = 'disputed'
HISTORICAL = 'historical'

STATE_LABELS: dict[str, str] = {
    CURRENT: '当前',
    CANDIDATE: '待确认',
    DISPUTED: '有争议',
    HISTORICAL: '历史',
}

# Where a piece of knowledge came from. Not a status — a claim proposed by a research
# task and one extracted from a document are both "not accepted yet" — but they are not
# the same thing to a reader either, and the *word* has to follow that, not the page it
# happens to be rendered on.
#
# This is why the label lives here: 「研究候选」 means "not in the knowledge base yet,
# waiting for you", whereas 未验证 would read as "already knowledge, just unchecked".
# Every surface — Research, Search, Ask, One Box — consumes this one word.
KNOWLEDGE = 'knowledge'
RESEARCH_CANDIDATE = 'research_candidate'

ORIGIN_LABELS: dict[str, str] = {
    RESEARCH_CANDIDATE: '研究候选',
}

# Current knowledge first: a replaced statement stays *findable* — that is the whole
# point of never deleting it — but it is not what the wiki holds now, so it ranks
# below everything that is.
STATE_RANK: dict[str, int] = {CURRENT: 0, CANDIDATE: 1, DISPUTED: 2, HISTORICAL: 3}


def fold(value: Any) -> str:
    """Case- and spacing-insensitive form, safe for Chinese.

    Deliberately not ``ontology.normalize_predicate``: that one strips anything
    outside ``[a-z0-9_]``, which erases Chinese entirely — "首席执行官" would fold
    to the empty string and stop matching anything.
    """
    return ' '.join(str(value or '').casefold().split())


def predicate_keywords(predicate: str) -> tuple[str, ...]:
    """Every way the registry says this predicate in words.

    The keyword set *is* registry data — canonical name, label, declared aliases —
    which is why there is no vocabulary table in this file. It is what makes "CEO",
    "首席执行官" and "chief executive officer" the same search: all three are
    declared names of ``has_ceo``, not guesses about what the user meant.
    """
    spec = claim_predicate_spec(predicate)
    if spec is None:
        return (fold(predicate),)
    words = [spec.predicate, spec.label or '', *spec.aliases]
    return tuple(dict.fromkeys(f for f in (fold(w) for w in words) if f))


def state_of(claim: dict, *, disputed: bool = False) -> str:
    """Which of the four states a claim is in, most specific first.

    Precedence is deliberate. Superseded outranks everything — a replaced statement
    is history whatever else is true of it. An unresolved contradiction outranks
    ``candidate``, because "we are not sure this is settled" tells a reader more
    than "nobody has reviewed this yet".
    """
    status = str(claim.get('status') or '')
    if status == 'superseded':
        return HISTORICAL
    if disputed:
        return DISPUTED
    if status == 'candidate':
        return CANDIDATE
    return CURRENT


def relevance(claim: dict, terms: list[str], *, aliases: Iterable[str] = (),
              keywords: Iterable[str] = ()) -> int:
    """How many query terms this claim accounts for, across every name it has.

    Matching goes through the ontology instead of the claim's own sentence: the
    predicate is matched by its label and declared aliases, the entities by their
    name *and* their aliases. A claim the query reaches twice — "Apple" for the
    subject, "CEO" for the predicate — therefore outranks one it reaches once,
    which is what puts the fact the user asked about above the rest of its chunk.
    """
    surfaces = [claim.get('subject_name'), claim.get('object_name'), claim.get('object_text'),
                claim.get('content'), claim.get('source_quote'), *aliases, *keywords]
    hay = ' '.join(f for f in (fold(s) for s in surfaces) if f)
    if not hay:
        return 0
    return sum(1 for term in terms if fold(term) and fold(term) in hay)


def statement_key(claim: dict) -> tuple:
    """The identity of the *fact*, so one statement yields one knowledge result.

    A claim has exactly one source chunk, but the same fact can be extracted from
    two of them. Grouping by ``(subject, predicate, object)`` collapses those into a
    single result instead of showing the user the same answer twice.
    """
    object_key = claim.get('object_id') or fold(claim.get('object_text'))
    return (claim.get('subject_id'), claim.get('predicate'), object_key)


def rank_key(view: 'KnowledgeView') -> tuple:
    """Current first, then how well the query matched.

    Ties keep the order the candidates arrived in — which is confidence order, from
    the query — so this never needs a second sort key.
    """
    return (STATE_RANK.get(view.state, len(STATE_RANK)), -view.score)


def _preview(claim: dict) -> str | None:
    """The claim's own words: a locatable quote when there is one, else its sentence.

    Both are stored text, neither is a paraphrase. The quote is preferred because it
    is the part that traces back to offsets in the source.
    """
    for key in ('source_quote', 'content'):
        text = ' '.join(str(claim.get(key) or '').split())
        if text:
            return text[:240]
    return None


@dataclass(frozen=True)
class KnowledgeView:
    """One piece of knowledge, arranged for reading."""

    claim_id: str
    subject_label: str
    predicate: str
    predicate_label: str | None
    object_label: str
    state: str
    status: str
    origin: str = KNOWLEDGE
    created_at: str | None = None
    evidence_preview: str | None = None
    evidence: dict | None = None
    sources: int = 1
    score: int = 0

    @property
    def status_label(self) -> str:
        """The word a reader sees — chosen from origin *and* state, never by the caller.

        A caller that could pick this per page would eventually say two different things
        about the same claim, which is exactly what a single read model exists to stop.
        """
        if self.state == CANDIDATE and self.origin in ORIGIN_LABELS:
            return ORIGIN_LABELS[self.origin]
        return STATE_LABELS.get(self.state, self.state)

    def to_dict(self) -> dict:
        return {
            'claim_id': self.claim_id,
            'subject_label': self.subject_label,
            'predicate': self.predicate,
            # None when the registry declares no label — and there is nothing here
            # for a caller to fall back to, which is the point.
            'predicate_label': self.predicate_label,
            'object_label': self.object_label,
            'state': self.state,
            'status': self.status,
            'status_label': self.status_label,
            'origin': self.origin,
            'evidence_preview': self.evidence_preview,
            'evidence': self.evidence,
            'sources': self.sources,
            'score': self.score,
            'created_at': self.created_at,
        }


def best_per_statement(claims: list[dict], *, disputed_ids: frozenset[str] | set[str] = frozenset(),
                       scores: dict[str, int] | None = None,
                       evidence: dict[str, dict] | None = None,
                       origin: str = KNOWLEDGE) -> list[KnowledgeView]:
    """One view per asserted *fact*, keeping the best-supported record of it.

    Two sources asserting the same thing is one piece of support, not two — showing it
    twice reads like two different answers. And when a fact has both a current and a
    superseded record of itself (a duplicate from another source), the current one is
    what a reader should be shown.

    Shared by Search and Knowledge QA so "one result per fact" is one rule instead of
    two that drift. Ordering is left to the caller: Search ranks current-first because
    it is browsing, QA takes the first few because it is justifying one sentence.
    """
    by_id = scores or {}
    evidence = evidence or {}
    buckets: dict[tuple, list[dict]] = {}
    for claim in claims:
        buckets.setdefault(statement_key(claim), []).append(claim)

    views: list[KnowledgeView] = []
    for members in buckets.values():
        def strength(claim: dict) -> tuple:
            disputed = str(claim.get('id')) in disputed_ids
            return (STATE_RANK.get(state_of(claim, disputed=disputed), len(STATE_RANK)),
                    -by_id.get(str(claim.get('id')), 0))

        best = min(members, key=strength)
        views.append(view_for(best, disputed=str(best.get('id')) in disputed_ids,
                              evidence=evidence.get(str(best.get('id'))),
                              sources=len(members),
                              score=by_id.get(str(best.get('id')), 0), origin=origin))
    return views


def view_for(claim: dict, *, aliases: Iterable[str] = (), keywords: Iterable[str] = (),
             disputed: bool = False, evidence: dict | None = None,
             sources: int = 1, score: int = 0, origin: str = KNOWLEDGE) -> KnowledgeView:
    """Project one stored claim into the shape a surface renders."""
    spec = claim_predicate_spec(claim.get('predicate') or '')
    return KnowledgeView(
        origin=origin,
        claim_id=str(claim.get('id') or ''),
        subject_label=str(claim.get('subject_name') or ''),
        predicate=str(claim.get('predicate') or ''),
        predicate_label=(spec.label or None) if spec else None,
        object_label=str(claim.get('object_name') or claim.get('object_text') or ''),
        state=state_of(claim, disputed=disputed),
        status=str(claim.get('status') or ''),
        created_at=claim.get('created_at'),
        evidence_preview=_preview(claim),
        evidence=evidence,
        sources=max(1, sources),
        score=score,
    )
