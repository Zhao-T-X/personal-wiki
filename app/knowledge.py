from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any
from .db import loads
from .resolution import resolve_or_create_entity
from .normalization import derive_relations
from .ontology import relation_types_allowed, relation_spec
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


def persist_extraction(conn, *, document_id: str, extraction: dict[str, Any]) -> dict[str, int]:
    entities = EntityRepository(conn)
    claims = ClaimRepository(conn)
    events = EventRepository(conn)
    relations = RelationRepository(conn)
    ideas = IdeaRepository(conn)
    questions = QuestionRepository(conn)
    evidence = EvidenceRepository(conn)

    entity_ids: dict[str, str] = {}
    for e in extraction.get('entities', []):
        eid = resolve_or_create_entity(conn, name=e['name'], entity_types=e.get('types'), entity_type=e.get('type'), aliases=e.get('aliases', []), description=e.get('description'), properties=e.get('properties', {}))
        entity_ids[e['name'].casefold()] = eid
        for alias in e.get('aliases', []): entity_ids[alias.casefold()] = eid

    counts = {'entities': len(set(entity_ids.values())), 'claims': 0, 'relations': 0, 'events': 0, 'ideas': 0, 'questions': 0, 'auto_created_subjects': 0, 'imprecise_quotes': 0}

    for c in extraction.get('claims', []):
        src = _source(evidence, c['source_chunk'])
        if src['document_id'] != document_id: raise ValueError('Claim provenance points to a different document')
        subject_id = _entity_id_by_ref(entity_ids, c['subject'])
        if not subject_id:
            # The model referenced an entity it never declared; keep the claim
            # by creating a candidate entity instead of failing the document.
            subject_id = resolve_or_create_entity(conn, name=c['subject'], entity_types=['Resource'], aliases=[], description=None, properties={})
            entity_ids[c['subject'].casefold()] = subject_id
            counts['auto_created_subjects'] += 1
        object_id = _entity_id_by_ref(entity_ids, c.get('object'))
        object_text = None if object_id else c.get('object')
        start, end, quote, located = _locate_quote(src['content'], c.get('evidence_quote'), src['start_offset'], src['end_offset'])
        if not located: counts['imprecise_quotes'] += 1
        claims.insert(subject_id=subject_id, predicate=c['predicate'], object_id=object_id, object_text=object_text,
                      content=c.get('content'), context=c.get('context', {}), claim_type=c['claim_type'],
                      polarity=c['polarity'], modality=c['modality'], confidence=c['confidence'],
                      status='candidate', created_by='llm', source_document_id=document_id,
                      source_chunk_id=c['source_chunk'], source_start_offset=start,
                      source_end_offset=end, source_quote=quote)
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
    return counts
