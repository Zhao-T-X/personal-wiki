from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any
from .db import loads
from .resolution import resolve_or_create_entity
from .normalization import derive_relations
from .entity_eligibility import entity_eligibility, is_plausible_subject
from .object_classification import classify_object
from .config import runtime
from .ontology import claim_registry_version, relation_spec, relation_types_allowed
from .repositories import (ClaimRepository, EntityRepository, EventRepository,
                           EvidenceRepository, IdeaRepository, QuestionRepository,
                           RelationRepository)


def _source(evidence: EvidenceRepository, chunk_id: str) -> dict:
    row = evidence.provenance(chunk_id)
    if not row:
        raise ValueError(f'Invalid provenance: chunk {chunk_id} does not exist')
    return row


def _norm_text(s: str) -> str:
    s = unicodedata.normalize('NFKC', s).casefold()
    return re.sub(r'[^0-9a-z\u4e00-\u9fff]+', '', s)


def _locate_quote(chunk_content: str, quote: str | None, chunk_start: int, chunk_end: int) -> tuple[int,int,str | None,bool]:
    """Locate a model-supplied evidence quote inside the chunk.

    Returns (start, end, quote, localized). Matching ladder: exact →
    whitespace-compacted → unicode-normalized (full/half-width, punctuation)
    → fuzzy per-line (ratio >= 0.85). Falls back to the whole-chunk span
    instead of failing the whole document; ``localized=False`` marks quotes
    that could not be verified and are counted by the caller.
    """
    quote = (quote or '').strip()
    if not quote:
        return chunk_start, chunk_end, None, False
    idx = chunk_content.find(quote)
    if idx >= 0:
        return chunk_start + idx, chunk_start + idx + len(quote), quote, True
    compact = ' '.join(chunk_content.split())
    compact_quote = ' '.join(quote.split())
    if compact_quote and compact_quote in compact:
        return chunk_start, chunk_end, quote, True
    n_content, n_quote = _norm_text(chunk_content), _norm_text(quote)
    if n_quote and n_quote in n_content:
        return chunk_start, chunk_end, quote, True
    best = 0.0
    for line in chunk_content.splitlines():
        line = line.strip()
        if not line:
            continue
        ratio = SequenceMatcher(None, _norm_text(line), n_quote).ratio()
        if ratio > best:
            best = ratio
    if n_quote and best >= 0.85:
        return chunk_start, chunk_end, quote, True
    return chunk_start, chunk_end, quote, False


def _entity_id_by_ref(entity_ids: dict[str, str], ref: str | None) -> str | None:
    if not ref: return None
    return entity_ids.get(ref.casefold())


def _supported_entity_names(extraction: dict[str, Any]) -> set[str]:
    """Names (and aliases, case-folded) that some claim actually references.

    An entity only earns a place in the knowledge pool when a claim uses it as its
    subject or object. This set is the single fact the eligibility gate reads to
    decide KEEP vs REVIEW — built once, no per-entity database query.
    """
    names: set[str] = set()
    for c in extraction.get('claims', []) or []:
        s = (c.get('subject') or '').strip()
        if s:
            names.add(s.casefold())
        o = c.get('object')
        if o and isinstance(o, str):
            names.add(o.casefold())
    return names


def persist_extraction(conn, *, document_id: str, extraction: dict[str, Any]) -> dict[str, int]:
    entities = EntityRepository(conn)
    claims = ClaimRepository(conn)
    events = EventRepository(conn)
    relations = RelationRepository(conn)
    ideas = IdeaRepository(conn)
    questions = QuestionRepository(conn)
    evidence = EvidenceRepository(conn)

    entity_ids: dict[str, str] = {}
    eligibility_report: list[dict] = []
    dropped_entities = 0
    review_entities = 0
    # The single fact the eligibility gate reads: which entity names participate in a
    # claim. Built once before the loop — no per-entity database query (no N+1).
    supported = _supported_entity_names(extraction)
    for e in extraction.get('entities', []):
        verdict, reason = entity_eligibility(
            e['name'], e.get('types'), supported=(e['name'].casefold() in supported))
        eligibility_report.append({'name': e['name'], 'types': e.get('types'),
                                   'verdict': verdict, 'reason': reason})
        if verdict == 'DROP':
            # Structural garbage (file path / relation name / modifier): never an entity.
            dropped_entities += 1
            continue
        # The eligibility verdict rides on the entity as a property, so Duplicate
        # Detection, Object Linking and the Review queue can each tell an
        # accepted ("keep") entity from an unsupported ("review") one without
        # re-deriving the decision (single source of truth for the boundary).
        props = dict(e.get('properties') or {}) if isinstance(e.get('properties'), dict) else {}
        props['eligibility'] = 'review' if verdict == 'REVIEW' else 'keep'
        eid = resolve_or_create_entity(conn, name=e['name'], entity_types=e.get('types'), entity_type=e.get('type'), aliases=e.get('aliases', []), description=e.get('description'), properties=props)
        if verdict == 'REVIEW':
            # Plausible but unproven (no claim support): quarantine as a candidate so it
            # is out of the verified pool and surfaces for human review, not auto-kept.
            # Never demote an entity that is already verified.
            review_entities += 1
            existing = entities.get(eid)
            if not existing or existing.get('status') != 'verified':
                entities.update(eid, {'status': 'candidate'})
        entity_ids[e['name'].casefold()] = eid
        for alias in e.get('aliases', []): entity_ids[alias.casefold()] = eid

    counts = {'entities': len(set(entity_ids.values())), 'claims': 0, 'relations': 0, 'events': 0, 'ideas': 0, 'questions': 0, 'auto_created_subjects': 0, 'imprecise_quotes': 0, 'dropped_unsupported_subjects': 0}

    for c in extraction.get('claims', []):
        src = _source(evidence, c['source_chunk'])
        if src['document_id'] != document_id: raise ValueError('Claim provenance points to a different document')
        subject_id = _entity_id_by_ref(entity_ids, c['subject'])
        if not subject_id:
            # The model referenced a subject it never declared. Only materialise it as a
            # Resource when it is a plausible subject — not a file path, relation name or
            # modifier. Otherwise it stays an unresolved candidate and the claim is not
            # persisted as a fact about a freshly-invented entity (no garbage Resource).
            if not is_plausible_subject(c['subject']):
                counts['dropped_unsupported_subjects'] += 1
                continue
            subject_id = resolve_or_create_entity(conn, name=c['subject'], entity_types=['Resource'], aliases=[], description=None, properties={})
            entity_ids[c['subject'].casefold()] = subject_id
            counts['auto_created_subjects'] += 1
        object_id = _entity_id_by_ref(entity_ids, c.get('object'))
        object_text = None if object_id else c.get('object')
        start, end, quote, located = _locate_quote(src['content'], c.get('evidence_quote'), src['start_offset'], src['end_offset'])
        if not located: counts['imprecise_quotes'] += 1
        # Temporal/evolution meaning travels in the claim context, not in the
        # predicate: no schema migration, and the predicate stays canonical.
        context = dict(c.get('context') or {})
        if c.get('temporal_signal'):
            context.setdefault('temporal_signal', c['temporal_signal'])
        # Semantic boundary: type the object so downstream paths (Object Linking, Review)
        # agree on what it is. Only *entity*-like objects may be linked later; a literal
        # value or a descriptive phrase keeps its text and is never a subject candidate.
        if runtime().get('object_classification_enabled', True):
            if object_id:
                context.setdefault('object_class', 'entity')
            elif object_text:
                context.setdefault('object_class', classify_object(object_text)[0])
                counts['object_classes'] = counts.get('object_classes', {})
                cls = context['object_class']
                counts['object_classes'][cls] = counts['object_classes'].get(cls, 0) + 1
        claims.insert(subject_id=subject_id, predicate=c['predicate'], object_id=object_id, object_text=object_text,
                      content=c.get('content'), context=context, claim_type=c['claim_type'],
                      polarity=c['polarity'], modality=c['modality'], confidence=c['confidence'],
                      status='candidate', created_by='llm', source_document_id=document_id,
                      source_chunk_id=c['source_chunk'], source_start_offset=start,
                      source_end_offset=end, source_quote=quote,
                      ontology_version=claim_registry_version())
        counts['claims'] += 1

    for ev in extraction.get('events', []):
        src = _source(evidence, ev['source_chunk'])
        if src['document_id'] != document_id: raise ValueError('Event provenance points to a different document')
        start, end, quote, located = _locate_quote(src['content'], ev['evidence_quote'], src['start_offset'], src['end_offset'])
        if not located: counts['imprecise_quotes'] += 1
        events.insert(event_type=ev['event_type'], description=ev['description'],
                      participants=ev.get('participants', []), time=ev.get('time', {}),
                      location=ev.get('location'), status=ev['status'], confidence=ev['confidence'],
                      source_document_id=document_id, source_chunk_id=ev['source_chunk'],
                      source_start_offset=start, source_end_offset=end, source_quote=quote)
        counts['events'] += 1

    # Relations are derived deterministically from Claims. Legacy explicit relations are ignored.
    derived = derive_relations(extraction)
    for r in derived:
        src = _source(evidence, r['source_chunk'])
        if src['document_id'] != document_id: raise ValueError('Relation provenance points to a different document')
        source_id = _entity_id_by_ref(entity_ids, r['source'])
        target_id = _entity_id_by_ref(entity_ids, r['target'])
        if not source_id or not target_id: continue
        spec = relation_spec(r['predicate'])
        if not spec: continue
        srow = entities.fetch_types_aliases(source_id)
        trow = entities.fetch_types_aliases(target_id)
        if not relation_types_allowed(loads(srow['types_json'], []), r['predicate'], loads(trow['types_json'], [])): continue
        start, end, quote, located = _locate_quote(src['content'], r['evidence_quote'], src['start_offset'], src['end_offset'])
        if not located: counts['imprecise_quotes'] += 1
        if relations.exists(source_id=source_id, predicate=r['predicate'], target_id=target_id,
                            document_id=document_id, chunk_id=r['source_chunk']): continue
        relations.insert(source_id=source_id, predicate=r['predicate'], target_id=target_id,
                         context=r.get('context', {}), confidence=r['confidence'], status='candidate',
                         created_by='normalizer', source_document_id=document_id,
                         source_chunk_id=r['source_chunk'], source_start_offset=start,
                         source_end_offset=end, source_quote=quote)
        counts['relations'] += 1

    for idea in extraction.get('ideas', []):
        src = _source(evidence, idea['source_chunk'])
        if src['document_id'] != document_id: raise ValueError('Idea provenance points to a different document')
        start, end, quote, located = _locate_quote(src['content'], idea['evidence_quote'], src['start_offset'], src['end_offset'])
        if not located: counts['imprecise_quotes'] += 1
        ideas.insert(content=idea['content'], status=idea['status'], confidence=idea['confidence'],
                     source_document_id=document_id, source_chunk_id=idea['source_chunk'],
                     source_start_offset=start, source_end_offset=end, source_quote=quote)
        counts['ideas'] += 1

    for q in extraction.get('questions', []):
        src = _source(evidence, q['source_chunk'])
        if src['document_id'] != document_id: raise ValueError('Question provenance points to a different document')
        start, end, quote, located = _locate_quote(src['content'], q['evidence_quote'], src['start_offset'], src['end_offset'])
        if not located: counts['imprecise_quotes'] += 1
        questions.insert(content=q['content'], status=q['status'], source_document_id=document_id,
                         source_chunk_id=q['source_chunk'], source_start_offset=start,
                         source_end_offset=end, source_quote=quote)
        counts['questions'] += 1
    # Unsupported rate: of everything the model proposed as an entity, how much reached
    # the pool without any claim backing it (dropped structural garbage + quarantined
    # review entities). The lower, the cleaner the extraction.
    total_proposed = dropped_entities + review_entities + counts['entities']
    counts['dropped_entities'] = dropped_entities
    counts['review_entities'] = review_entities
    counts['unsupported_entity_rate'] = round(
        (dropped_entities + review_entities) / max(1, total_proposed), 3)
    return {**counts, 'eligibility': eligibility_report,
            'entity_ids': list(set(entity_ids.values()))}
