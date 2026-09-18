"""Extraction Context Planner (Step 15) — deterministic prefetch for one-pass extraction.

Why this exists. Today an extraction step costs two provider calls: the model reads
`claim-predicates.md` and `entity-types.md` through `read_skill_reference`, then answers.
The second call replays the entire system prompt and tool block *and* carries both
reference documents in full (~1.96k tokens of the 4.48k the follow-up call costs).

The references are not the problem — the CEO → `has_ceo` fix came from showing the model
the registry. **Re-delivering the whole document at full price every time is the
problem.** So: prepare the minimal semantic context before the call, and let the model
answer once.

    CONTEXT CANDIDATE SELECTION ONLY. This module never decides meaning.

What that means concretely, because the boundary is the whole point of the round:

    allowed     "the chunk mentions a chief executive" -> offer `has_ceo` as a candidate
    allowed     "has_ceo declares domain=Organization"  -> offer the Organization/Person types
    FORBIDDEN   "the chunk mentions a chief executive" -> emit a Claim
    FORBIDDEN   "Context Runtime looks technical"      -> decide its entity type
    FORBIDDEN   any rule of the form  keyword -> final verdict

Final semantics stay where they already were: the model chooses, `KnowledgeCompiler`
validates, the Registry is the authority.

Every payload here is derived from an authority that already exists; nothing is invented
and nothing is a second copy:

    predicate candidates   ontology.match_claim_predicates()   (deterministic scorer)
    predicate detail       schemas/claim-predicate-registry.json, via ontology
    entity type index      skills/knowledge-extraction/references/entity-types.md,
                           cross-checked against ontology.ENTITY_TYPES
    object_kind            skills/knowledge-extraction/SKILL.md — already in the
                           system prompt, so it is deliberately NOT re-injected (§8)

The one rule this file must never break: a reference body appears exactly once in the
request. Prefetch is the only source of it, and in prefetch mode the agent registers no
reference tool, so a second copy cannot be fetched even by accident.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .items import TYPE_EXTRACTION_CONTEXT, ContextItem
from ..ontology import (CLAIM_PREDICATES, ENTITY_TYPES, claim_predicate_spec,
                        match_claim_predicates, normalize_predicate)

ROOT = Path(__file__).resolve().parents[2]
ENTITY_TYPES_DOC = ROOT / 'skills' / 'knowledge-extraction' / 'references' / 'entity-types.md'
SKILL_DOC = ROOT / 'skills' / 'knowledge-extraction' / 'SKILL.md'

# Mode names. `agentic` = the agent fetches references itself (two calls).
# `prefetch` = this planner supplies the context up front (one call).
MODE_AGENTIC = 'agentic'
MODE_PREFETCH = 'prefetch'

# Raised when the planner cannot assemble the context it promised. Recorded rather than
# papered over: prefetch must never silently degrade into a second round (§19).
INSUFFICIENT = 'CONTEXT_PREFETCH_INSUFFICIENT'

# How many deterministic candidates are worth showing. The matcher's own default; kept
# explicit so the context size is a decision, not a side effect.
MAX_PREDICATE_CANDIDATES = 5

# The heading this planner uses for the predicate block. Named so a test can assert the
# block appears exactly once instead of counting a phrase that might drift.
CLAIM_PREDICATES_SENTINEL = '[CLAIM PREDICATE CONTEXT]'


@dataclass(frozen=True)
class ExtractionContextPlan:
    """The context one prefetch extraction call is given, and where each part came from."""

    predicate_candidates: tuple[str, ...] = ()
    predicate_context: str = ''
    entity_type_context: str = ''
    object_kind_context: dict = field(default_factory=dict)
    general_extraction_context: str = ''
    flags: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """The block rendered into the prompt: header, then the two vocabularies."""
        return '\n\n'.join(p.strip() for p in
                           (self.general_extraction_context, self.predicate_context,
                            self.entity_type_context) if p and p.strip())

    def items(self) -> list[ContextItem]:
        """The single ContextItem this plan contributes to the prompt.

        One item, not one per part: the compiler's dedup and the trace's `sections` both
        key on the item, and a single payload is what makes "the reference appears once"
        checkable instead of asserted.
        """
        if not self.text:
            return []
        return [ContextItem(
            id='extraction-context',
            type=TYPE_EXTRACTION_CONTEXT,
            content=self.text,
            source='context.extraction_context.plan_extraction_context',
            priority=1.0, relevance=1.0, information_gain=0.9, evidence_strength=0.5,
            required=True,
            metadata={
                'predicate_candidates': list(self.predicate_candidates),
                'flags': list(self.flags),
                'parts': {'predicate': len(self.predicate_context),
                          'entity_type': len(self.entity_type_context),
                          'general': len(self.general_extraction_context)},
            },
            reason='deterministic prefetch of the closed vocabularies this chunk needs',
        )]

    def to_dict(self) -> dict:
        return {
            'predicate_candidates': list(self.predicate_candidates),
            'predicate_context': self.predicate_context,
            'entity_type_context': self.entity_type_context,
            'object_kind_context': dict(self.object_kind_context),
            'general_extraction_context': self.general_extraction_context,
            'flags': list(self.flags),
        }


# ---------------------------------------------------------------------------------
# Document parsing. The reference documents are the registry's readable form, so the
# index is read from them rather than re-typed in Python (ADR-011: ontology data lives
# in the registry, not in code). Parsing is deliberately strict: if the document's
# shape changes, the planner reports it instead of silently emitting nothing.
# ---------------------------------------------------------------------------------

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except OSError:
        return ''


def _subsections(text: str, heading: str, *, parent: str = '##',
                 child: str = '###') -> list[tuple[str, str]]:
    """``[(title, body), ...]`` for the ``<child>`` blocks under ``<parent> <heading>``.

    The two levels are separate arguments because they are separate things: the section
    is an ``##`` heading and its entries are ``###`` headings. Conflating them is how the
    first version of this parser silently returned nothing.
    """
    start = re.search(rf'^{re.escape(parent)}\s+{re.escape(heading)}\s*$', text, re.M)
    if not start:
        return []
    rest = text[start.end():]
    stop = re.search(rf'^{re.escape(parent)}\s+\S', rest, re.M)
    body_all = rest[:stop.start()] if stop else rest

    headings = list(re.finditer(rf'^{re.escape(child)}\s+(.+?)\s*$', body_all, re.M))
    out: list[tuple[str, str]] = []
    for i, match in enumerate(headings):
        end = headings[i + 1].start() if i + 1 < len(headings) else len(body_all)
        out.append((match.group(1).strip(), body_all[match.end():end].strip()))
    return out


def entity_type_index() -> dict[str, str]:
    """``canonical type -> one-line definition`` from the entity-types reference.

    Cross-checked against ``ontology.ENTITY_TYPES``: the code set is what validation
    enforces, so a divergence means the readable registry is stale. Returning the code
    authority's keys keeps the planner honest — it can never advertise a type the
    compiler would reject.
    """
    parsed = {title: body.splitlines()[0].strip()
              for title, body in
              _subsections(_read(ENTITY_TYPES_DOC), '3. Type definitions')
              if body.strip()}
    return {t: parsed[t] for t in ENTITY_TYPES if t in parsed}


def entity_type_boundaries() -> list[tuple[str, str]]:
    """``[(title, note), ...]`` from the "Type boundaries" section of the same document."""
    return [(title, body) for title, body in
            _subsections(_read(ENTITY_TYPES_DOC), '4. Type boundaries') if body.strip()]


def _first_sentence(text: str) -> str:
    line = ' '.join(text.split())
    match = re.match(r'(.+?[.!?])(\s|$)', line)
    return match.group(1) if match else line


# ---------------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------------

def _predicate_context(candidates: list[str]) -> str:
    """The closed predicate vocabulary, plus the detail of the candidates only.

    The names are the one thing the model cannot see today: `predicate` is a free-form
    pattern in the output schema, unlike entity type and `object_kind` which ship as
    enums. So the index is always sent — but the *whole* reference (purpose, rules,
    claim-only rationale) is not.
    """
    names = sorted(CLAIM_PREDICATES)
    lines = [
        CLAIM_PREDICATES_SENTINEL,
        'Registered predicates (closed vocabulary — select the most specific one; never '
        'invent a name):',
        ', '.join(names),
    ]
    if candidates:
        lines.append('')
        lines.append('Most consistent with this chunk (candidates, not a decision):')
        for predicate in candidates:
            spec = claim_predicate_spec(predicate)
            if spec is None:
                continue
            detail = [f'- `{predicate}`']
            if spec.label:
                detail.append(f'label: {spec.label};')
            if spec.domain or spec.range:
                detail.append(f'domain={"|".join(spec.domain) or "*"} -> '
                              f'range={"|".join(spec.range) or "*"};')
            if spec.temporal:
                detail.append('temporal (put "现任"/"曾经" in `temporal_signal`, not in the '
                              'predicate name);')
            if spec.aliases:
                detail.append('aliases: ' + ', '.join(spec.aliases))
            lines.append(' '.join(detail))
    return '\n'.join(lines)


def _entity_type_context(candidate_types: set[str]) -> str:
    """The 14-type index, plus the boundary notes for the types this chunk may need.

    The schema already lists the 14 names as an enum; what it cannot carry is what they
    *mean*. The index gives one line each; the comparative boundary rules (Technology vs
    Method, Model vs Technology, ...) are the expensive part, so only the ones naming a
    candidate type are sent.
    """
    index = entity_type_index()
    if not index:
        return ''
    lines = ['[ENTITY TYPE CONTEXT]',
             f'Entity types (closed vocabulary, {len(index)}) — a Mention is not an Entity:']
    lines.extend(f'- {name}: {_first_sentence(note)}' for name, note in index.items())

    boundaries = [(title, note) for title, note in entity_type_boundaries()
                  if any(t.casefold() in title.casefold() for t in candidate_types)]
    if boundaries:
        lines.append('')
        lines.append('Boundary rules for the types above:')
        for title, note in boundaries:
            lines.append(f'- {title}: {" ".join(note.split())}')
    lines.append('')
    lines.append('When type confidence is insufficient, omit the Entity — never default to '
                 'Resource.')
    return '\n'.join(lines)


def _candidate_entity_types(candidates: list[str], text: str) -> set[str]:
    """Types worth showing boundary rules for.

    Two deterministic signals, neither of them a verdict:
      * the matched predicates' declared domain/range (the registry saying which types
        this relation can connect), and
      * the canonical type names the chunk itself spells out.

    It deliberately does **not** read the chunk for meaning.
    """
    types: set[str] = set()
    for predicate in candidates:
        spec = claim_predicate_spec(predicate)
        if spec is None:
            continue
        types.update(spec.domain)
        types.update(spec.range)
    folded = (text or '').casefold()
    types.update(t for t in ENTITY_TYPES if t.casefold() in folded)
    return {t for t in types if t in ENTITY_TYPES}


def _general_extraction_context(candidates: list[str], flags: tuple[str, ...]) -> str:
    """The header, plus what to say when the deterministic half found nothing specific.

    The header is not filler. `SKILL.md` tells the agent to open the registries with
    ``read_skill_reference`` — which prefetch mode does not register, because the context
    below already *is* that fetch. Leaving the instruction unqualified would point the
    model at a tool this call does not have; saying so is cheaper than a failed tool call
    and it is the honest description of the request.
    """
    lines = [
        '[EXTRACTION CONTEXT — ALREADY LOADED]',
        'The registries this chunk needs are included below (claim predicates, entity '
        'types). No reference tool is registered for this call and none is needed: choose '
        'from what is here.',
    ]
    if INSUFFICIENT in flags:
        lines.append('Part of the registry context could not be read. Use only the '
                     'predicates and types shown here; if none fits the source, omit the '
                     'Claim rather than inventing a predicate.')
    elif not candidates:
        lines.append('No predicate in particular stood out for this chunk. Choose from the '
                     'registered list above by meaning — never invent a predicate, and '
                     'prefer the most specific one that fits.')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------------

def plan_extraction_context(text: str, *, limit: int = MAX_PREDICATE_CANDIDATES
                            ) -> ExtractionContextPlan:
    """Decide the minimal semantic context for one extraction payload.

    Deterministic, offline, no model call — the whole point is to spend the model's
    single call on extraction instead of on looking things up.
    """
    payload = text or ''
    flags: list[str] = []

    candidates = [p for p in match_claim_predicates(payload, limit=limit)
                  if normalize_predicate(p) in CLAIM_PREDICATES]
    predicate_context = _predicate_context(candidates)

    entity_context = _entity_type_context(_candidate_entity_types(candidates, payload))
    # `object_kind` is defined in SKILL.md, which the system prompt already carries.
    # Injecting it again here would be the duplicated reference §16 forbids.
    object_kind_context = {
        'source': 'skills/knowledge-extraction/SKILL.md',
        'injected_here': False,
        'reason': 'the SKILL contract is already in the system prompt; one source only',
    }

    if not CLAIM_PREDICATES or not entity_type_index():
        # The registries are unreadable: say so instead of sending a half-blind prompt.
        flags.append(INSUFFICIENT)

    return ExtractionContextPlan(
        predicate_candidates=tuple(candidates),
        predicate_context=predicate_context,
        entity_type_context=entity_context,
        object_kind_context=object_kind_context,
        general_extraction_context=_general_extraction_context(candidates, tuple(flags)),
        flags=tuple(flags),
    )
