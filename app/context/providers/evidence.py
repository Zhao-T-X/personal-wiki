"""Evidence provider: minimum sufficient evidence, with an escalation ladder.

```
L1  quote only            <- default
L2  quote + neighbour     <- low confidence / conditional / too little evidence
L3  surrounding paragraph <- no stored quote / conflicting evidence
L4  whole chunk           <- explicitly requested
L5  extra chunks          <- ambiguity that only more sources can settle
```

Escalation is deterministic: it reads signals we already have (stored quote,
claim confidence, modality, conflicts between hits) and never costs an extra LLM
call. Dedup happens before rendering, because repeated evidence is the cheapest
token waste to remove.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from ..items import TYPE_EVIDENCE, ContextItem
from ..planner import ContextPlan
from ...runtime.task import TaskContext

LEVEL_QUOTE = 1
LEVEL_QUOTE_CONTEXT = 2
LEVEL_PARAGRAPH = 3
LEVEL_CHUNK = 4
LEVEL_MULTI_CHUNK = 5
LEVEL_NAMES = {
    LEVEL_QUOTE: 'quote',
    LEVEL_QUOTE_CONTEXT: 'quote+context',
    LEVEL_PARAGRAPH: 'paragraph',
    LEVEL_CHUNK: 'chunk',
    LEVEL_MULTI_CHUNK: 'multi-chunk',
}

# Escalation thresholds and limits.
DEFAULT_MAX_LEVEL = LEVEL_CHUNK
LOW_CONFIDENCE = 0.6
CONDITIONAL_MODALITIES = {'conditional', 'possible', 'probable'}
LEVEL_CHAR_CAPS = {LEVEL_QUOTE: 400, LEVEL_QUOTE_CONTEXT: 420, LEVEL_PARAGRAPH: 800}
DEFAULT_PER_DOCUMENT_CAP = 2
SIMILAR_QUOTE_RATIO = 0.85
# Fuzzy quote matching only applies to quotes long enough to be a sentence: for
# short strings a one-character difference can look like a 0.85 match.
MIN_FUZZY_CHARS = 40
MAX_OTHER_CLAIMS = 2

_SENTENCE_SPLIT = re.compile(r'(?<=[。！？!?；;])|(?<=\.\s)|\n+')


@dataclass
class EvidenceHit:
    """One retrieved chunk plus the quote that justifies using it."""

    chunk_id: str
    document_id: str
    title: str = ''
    quote: str = ''
    chunk_content: str = ''
    chunk_index: int | None = None
    start_offset: int = 0
    end_offset: int = 0
    confidence: float | None = None
    claims: list[dict] = field(default_factory=list)
    score: float = 0.0
    method: str = ''
    source_type: str = ''
    level: int = LEVEL_QUOTE
    reason: str = 'quote is enough'
    rendered: str = ''

    @classmethod
    def from_dict(cls, data: dict) -> 'EvidenceHit':
        return cls(
            chunk_id=str(data.get('chunk_id') or data.get('id') or ''),
            document_id=str(data.get('document_id') or ''),
            title=str(data.get('title') or ''),
            quote=str(data.get('quote') or ''),
            chunk_content=str(data.get('chunk_content') or data.get('content') or ''),
            chunk_index=data.get('chunk_index'),
            start_offset=int(data.get('start_offset') or 0),
            end_offset=int(data.get('end_offset') or 0),
            confidence=data.get('confidence'),
            claims=list(data.get('claims') or []),
            score=float(data.get('score') or 0.0),
            method=str(data.get('method') or ''),
            source_type=str(data.get('source_type') or ''),
        )

    @property
    def best_confidence(self) -> float | None:
        values = [c.get('confidence') for c in self.claims if c.get('confidence') is not None]
        if self.confidence is not None:
            values.append(self.confidence)
        return max(values) if values else None

    @property
    def conditional(self) -> bool:
        for claim in self.claims:
            if str(claim.get('modality') or '').lower() in CONDITIONAL_MODALITIES:
                return True
            context = claim.get('context') or {}
            if isinstance(context, dict) and context.get('condition'):
                return True
        return False

    def to_response(self) -> dict:
        """Keep the shape `/api/ask` has always returned to the UI."""
        return {
            'id': self.chunk_id,
            'document_id': self.document_id,
            'title': self.title,
            'content': self.rendered or self.quote or self.chunk_content,
            'quote': self.quote,
            'method': self.method,
            'score': self.score,
            'chunk_index': self.chunk_index,
            'start_offset': self.start_offset,
            'end_offset': self.end_offset,
            'escalation_level': self.level,
            'escalation_reason': self.reason,
            'claims': self.claims,
        }


# ------------------------------------------------------------------ rendering

def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s and s.strip()]


def sentence_window(text: str, anchor: str, *, radius: int = 1) -> str:
    """The sentence containing `anchor` plus `radius` neighbours on each side."""
    parts = _sentences(text)
    if not parts:
        return ''
    needle = (anchor or '').strip()[:24]
    index = 0
    if needle:
        for i, sentence in enumerate(parts):
            if needle in sentence:
                index = i
                break
    start = max(0, index - radius)
    return ''.join(parts[start:index + radius + 1])


def paragraph_window(text: str, anchor: str, *, max_chars: int = LEVEL_CHAR_CAPS[LEVEL_PARAGRAPH]) -> str:
    """The blank-line block containing `anchor`, capped in size."""
    needle = (anchor or '').strip()[:24]
    index = text.find(needle) if needle else -1
    if index < 0:
        return text[:max_chars]
    start = text.rfind('\n\n', 0, index)
    start = 0 if start < 0 else start + 2
    end = text.find('\n\n', index)
    end = len(text) if end < 0 else end
    block = text[start:end].strip()
    if len(block) <= max_chars:
        return block
    head = max(0, index - start - max_chars // 2)
    return text[start + head:start + head + max_chars].strip()


def render(hit: EvidenceHit, level: int | None = None) -> str:
    """Render one hit at a given escalation level."""
    level = level or hit.level
    citation = f"[doc:{hit.document_id} chunk:{hit.chunk_id}] {hit.title}".rstrip()

    if level <= LEVEL_QUOTE and hit.quote:
        body = f'"{hit.quote}"'
    elif level <= LEVEL_QUOTE_CONTEXT:
        body = sentence_window(hit.chunk_content or hit.quote, hit.quote) or hit.quote
        if hit.quote and hit.quote not in body:
            body = f'"{hit.quote}" {body}'.strip()
    elif level == LEVEL_PARAGRAPH:
        body = paragraph_window(hit.chunk_content, hit.quote) or hit.quote
    else:
        body = hit.chunk_content or hit.quote

    cap = LEVEL_CHAR_CAPS.get(level)
    if cap and len(body) > cap:
        body = body[:cap].rstrip()

    lines = [citation, body] if body else [citation]
    others = [c for c in hit.claims if (c.get('source_quote') or '').strip() != hit.quote.strip()]
    for claim in others[:MAX_OTHER_CLAIMS]:
        obj = claim.get('object_name') or claim.get('object_text') or ''
        lines.append(f"- {claim.get('subject_name') or '?'} {claim.get('predicate') or '?'} {obj}".rstrip())
    return '\n'.join(lines)


# ----------------------------------------------------------------- escalation

def conflicting_hits(hits: list[EvidenceHit]) -> set[str]:
    """Chunk ids involved in a contradiction.

    A contradiction is the same **subject and predicate** with opposite polarity.
    Keying on the predicate alone would let unrelated documents (which reuse the
    same vocabulary) escalate every hit, which costs tokens without adding any
    grounding - the measurement that produced this rule showed 5 hits jumping to
    L3 paragraphs for no reason.
    """
    polarities: dict[tuple[str, str], set[str]] = {}
    owners: dict[tuple[str, str], list[str]] = {}
    for hit in hits:
        for claim in hit.claims:
            predicate = str(claim.get('predicate') or '')
            polarity = str(claim.get('polarity') or '')
            if not predicate or not polarity:
                continue
            key = (str(claim.get('subject_name') or ''), predicate)
            polarities.setdefault(key, set()).add(polarity)
            owners.setdefault(key, []).append(hit.chunk_id)

    conflicting: set[str] = set()
    for key, found in polarities.items():
        if len(found) > 1:
            conflicting.update(owners[key])
    return conflicting


def choose_level(hit: EvidenceHit, *, conflicts: bool = False, insufficient: bool = False,
                 max_level: int = DEFAULT_MAX_LEVEL) -> tuple[int, str]:
    """Pick the cheapest level that can still ground the answer."""
    if not hit.quote.strip():
        return min(LEVEL_PARAGRAPH, max_level), 'no stored quote - using the surrounding paragraph'

    # Triggers only ever raise the level; the reason names the most specific one
    # that fired (later checks are the more serious signals).
    level, reason = LEVEL_QUOTE, 'quote is enough'
    if insufficient:
        level, reason = LEVEL_QUOTE_CONTEXT, 'too few evidence hits to ground the answer'
    if hit.conditional:
        level, reason = max(level, LEVEL_QUOTE_CONTEXT), 'conditional claim - the condition sits next to the quote'
    confidence = hit.best_confidence
    if confidence is not None and confidence < LOW_CONFIDENCE:
        level, reason = max(level, LEVEL_QUOTE_CONTEXT), f'low confidence ({confidence})'
    if conflicts:
        level, reason = max(level, LEVEL_PARAGRAPH), 'conflicting evidence found for this question'
    return min(level, max_level), reason


def apply_escalation(hits: list[EvidenceHit], *, requested: int = LEVEL_QUOTE,
                     max_level: int = DEFAULT_MAX_LEVEL) -> list[EvidenceHit]:
    """Set every hit's level and rendered text. Escalates only when justified.

    Only the hits involved in a contradiction are widened; the rest keep the
    cheapest level their own signals allow.
    """
    conflicting = conflicting_hits(hits)
    with_quotes = [h for h in hits if h.quote.strip()]
    insufficient = len(with_quotes) < 2 and bool(hits)
    for hit in hits:
        level, reason = choose_level(hit, conflicts=hit.chunk_id in conflicting,
                                     insufficient=insufficient, max_level=max_level)
        if requested > level:
            level, reason = min(requested, max_level), 'requested by the caller'
        hit.level, hit.reason = level, reason
        hit.rendered = render(hit)
    return hits


# ---------------------------------------------------------------------- dedup

def _normalise(text: str) -> str:
    return ' '.join((text or '').split()).casefold()


def dedup_hits(hits: list[EvidenceHit], *, per_document_cap: int = DEFAULT_PER_DOCUMENT_CAP,
               similar_ratio: float = SIMILAR_QUOTE_RATIO) -> tuple[list[EvidenceHit], list[dict]]:
    """Drop repeated evidence before it costs tokens.

    Three passes: identical chunk, identical quote, then near-identical quote
    (keeps the higher-scoring hit); finally cap how much any single document may
    contribute so one verbose source cannot crowd out the rest.
    Returns the kept hits and a ledger of what was dropped.
    """
    kept: list[EvidenceHit] = []
    dropped: list[dict] = []
    seen_chunks: set[str] = set()
    seen_quotes: list[tuple[str, EvidenceHit]] = []

    def find_duplicate(key: str) -> EvidenceHit | None:
        if not key:
            return None
        fuzzy_allowed = len(key) >= MIN_FUZZY_CHARS
        for existing_key, owner in seen_quotes:
            if existing_key == key:
                return owner
            if fuzzy_allowed and len(existing_key) >= MIN_FUZZY_CHARS:
                if SequenceMatcher(None, existing_key, key).ratio() >= similar_ratio:
                    return owner
        return None

    for hit in hits:
        if hit.chunk_id in seen_chunks:
            dropped.append({'id': hit.chunk_id, 'reason': 'duplicate_chunk', 'source': hit.document_id})
            continue
        key = _normalise(hit.quote)
        owner = find_duplicate(key)
        if owner is not None:
            if hit.score > owner.score:
                # The new hit is the better representative; swap it in.
                kept = [hit if h is owner else h for h in kept]
                seen_quotes = [(k, hit if o is owner else o) for k, o in seen_quotes]
                dropped.append({'id': owner.chunk_id, 'reason': 'duplicate_quote',
                                'source': owner.document_id, 'kept': hit.chunk_id})
                seen_chunks.add(hit.chunk_id)
            else:
                dropped.append({'id': hit.chunk_id, 'reason': 'duplicate_quote',
                                'source': hit.document_id, 'kept': owner.chunk_id})
            continue
        seen_chunks.add(hit.chunk_id)
        seen_quotes.append((key or hit.chunk_id, hit))
        kept.append(hit)

    capped: list[EvidenceHit] = []
    per_doc: dict[str, int] = {}
    for hit in kept:
        count = per_doc.get(hit.document_id, 0)
        if count >= per_document_cap:
            dropped.append({'id': hit.chunk_id, 'reason': 'document_cap',
                            'source': hit.document_id})
            continue
        per_doc[hit.document_id] = count + 1
        capped.append(hit)
    return capped, dropped


# ------------------------------------------------------------------- provider

def evidence_items(hits: list[EvidenceHit]) -> list[ContextItem]:
    """One context item per hit, priced by its rendered size."""
    items: list[ContextItem] = []
    for hit in hits:
        items.append(ContextItem(
            id=f'evidence:{hit.chunk_id}',
            type=TYPE_EVIDENCE,
            content=hit.rendered or render(hit),
            source=f'chunk:{hit.chunk_id}',
            priority=0.9,
            relevance=0.9,
            information_gain=0.8,
            evidence_strength=hit.best_confidence if hit.best_confidence is not None else 0.7,
            required=False,
            metadata={
                'document_id': hit.document_id,
                'chunk_id': hit.chunk_id,
                'escalation_level': hit.level,
                'escalation_reason': hit.reason,
                'quote_tokens': len(hit.quote) // 4 + 1,
            },
            reason=f'L{hit.level} {LEVEL_NAMES.get(hit.level, "quote")}: {hit.reason}',
        ))
    return items


class EvidenceProvider:
    """Serves pre-collected hits; retrieval stays outside the context layer."""

    name = 'evidence'

    def __init__(self, hits: list[EvidenceHit] | None = None):
        self.hits = list(hits or [])

    def provide(self, task: TaskContext, plan: ContextPlan) -> list[ContextItem]:
        return evidence_items(self.hits)
