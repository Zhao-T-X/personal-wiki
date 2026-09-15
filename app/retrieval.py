from __future__ import annotations
import re

from .config import runtime
from .readmodels.knowledge_view import (best_per_statement, predicate_keywords,
                                        rank_key, relevance)
from .repositories import ClaimRepository, DocumentRepository, EntityRepository

# FTS5's trigram tokenizer indexes overlapping 3-character windows, so a query
# shorter than that has no token to match and needs a different strategy.
_TRIGRAM_MIN = 3


def _fts_query(q: str) -> str:
    """Build an FTS5 match expression for the trigram index.

    The index stores 3-character windows, so a query only has to *contain* a
    matching window instead of equalling a whole token. Longer queries are cut
    into overlapping grams and OR-ed — that is what lets "苹果的SEO是谁" find
    "苹果的SEO是乔布斯" (they share five grams) instead of demanding a phrase that
    appears in no document.

    Returns '' when the query is too short for the index; callers fall back.
    """
    text = ' '.join(q.split()).replace('"', '')
    if len(text) < _TRIGRAM_MIN:
        return ''
    grams = list(dict.fromkeys(text[i:i + _TRIGRAM_MIN] for i in range(len(text) - _TRIGRAM_MIN + 1)))
    return ' OR '.join(f'"{g}"' for g in grams[:32])


def _like_search(q: str, cap: int) -> list[dict]:
    """Substring fallback for queries the trigram index cannot serve.

    A personal wiki holds hundreds of documents, not millions, so a LIKE scan is
    cheap — and unlike FTS it still works for a two-character Chinese query.
    """
    return DocumentRepository().search_like(_terms(q), cap)


def lexical_search(q: str, limit: int = 10) -> list[dict]:
    cap = min(limit, runtime()['max_search_results'])
    documents = DocumentRepository()
    rows = documents.search_fts(_fts_query(q), cap)
    if not rows:
        rows = documents.search_like(_terms(q), cap)
    return [r | {'method': 'lexical'} for r in rows]


def _chunk_results_from_documents(doc_results: list[dict], cap: int, terms: list[str]) -> list[dict]:
    """Expand matched documents into chunks, but only keep the chunks that
    actually contain the query terms (best-first, max 3 per document) — pulling
    every chunk of a matched document injects noise into the evidence pack."""
    documents = DocumentRepository()
    picked: list[tuple[float, dict]] = []
    seen = set()
    lowered = [t.lower() for t in terms if t.strip()]
    for doc in doc_results:
        rows = documents.chunk_rows(doc['document_id'])
        scored = []
        for r in rows:
            if r['id'] in seen:
                continue
            body = r['content'].lower()
            hits = sum(1 for t in lowered if t in body)
            scored.append((hits, dict(r)))
        scored.sort(key=lambda x: x[0], reverse=True)
        if lowered:
            matched = [s for s in scored if s[0] > 0]
            keep = (matched or scored)[:3]
        else:
            keep = scored[:3]
        for hits, item in keep:
            seen.add(item['id'])
            picked.append((hits, item | {'score': doc.get('score', 0), 'method': 'lexical'}))
    picked.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in picked[:cap]]


def _rrf(results: list[list[dict]], limit: int) -> list[dict]:
    scores = {}
    items = {}
    for result_set in results:
        for rank, item in enumerate(result_set, start=1):
            key = item['id']
            scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
            items[key] = item
    merged = []
    for key, score in sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]:
        item = dict(items[key]); item['score'] = score; item['method'] = 'hybrid'
        merged.append(item)
    return merged


def _terms(q: str) -> list[str]:
    """Split a query into terms usable for both matching and explanation.

    Chinese has no spaces, so splitting on whitespace alone leaves a whole question
    as one unusable term. Chunks that mix scripts ("苹果的SEO") are split at the
    boundary so each part can match on its own, while pure-Latin chunks keep their
    hyphens ("Fine-tuning").
    """
    normalized = q
    for mark in '，。？！、：；（）「」【】“”':
        normalized = normalized.replace(mark, ' ')
    terms: list[str] = []
    for chunk in normalized.split():
        terms.extend(re.findall(r'[\u4e00-\u9fff]+|[A-Za-z0-9_][A-Za-z0-9_.\-]*', chunk))
    return terms


def _matched_in(item: dict, terms: list[str]) -> list[str]:
    """Which fields actually contain the query terms.

    Deterministic and cheap, so the UI can say *why* a result matched instead of
    showing a raw RRF score nobody can interpret.
    """
    if not terms:
        return []
    title = (item.get('title') or '').lower()
    content = (item.get('content') or '').lower()
    fields = []
    if any(t.lower() in title for t in terms):
        fields.append('title')
    if any(t.lower() in content for t in terms):
        fields.append('content')
    # No lexical overlap anywhere means the hit came from the embedding side.
    return fields or ['semantic']


def search(q: str, limit: int = 10, semantic: bool = True) -> list[dict]:
    terms = _terms(q)
    lexical_chunks = _chunk_results_from_documents(lexical_search(q, limit), limit, terms)
    merged = lexical_chunks
    if semantic and runtime()['openai_api_key']:
        try:
            from .embeddings import semantic_search
            merged = _rrf([lexical_chunks, semantic_search(q, limit)], limit)
        except Exception:
            pass
    results = merged[:limit]
    for item in results:
        item['matched_in'] = _matched_in(item, terms)
    return results


def _evidence(chunk: dict) -> dict:
    """Where a piece of knowledge came from — enough to open the passage itself."""
    return {
        'chunk_id': chunk.get('id'),
        'document_id': chunk.get('document_id'),
        'document_title': chunk.get('title'),
        'chunk_index': chunk.get('chunk_index'),
        'start_offset': chunk.get('start_offset'),
        'end_offset': chunk.get('end_offset'),
    }


def search_knowledge(q: str, limit: int = 8, *, semantic: bool = True,
                     chunk_limit: int | None = None, claims_per_chunk: int = 3) -> dict:
    """Knowledge first, then the passages it came from.

    Recall is unchanged — chunks are still found by FTS / embedding / RRF, and that
    stays the floor. What this adds is the projection on top: the recalled chunks'
    claims, resolved into a readable statement with its state and its evidence, and
    ranked current-first.

    History is never filtered out. Search answers "where is this mentioned", which is
    a different question from Knowledge QA's "what is true now" — so a superseded
    claim is ranked lower rather than hidden. Searching "Tim Cook Apple CEO" must
    still find the company's former CEO.

    Three queries beyond recall — the chunks' claims, their entities' aliases, their
    open disputes — all batched. A page of chunks must never cost one lookup per
    chunk (§11), which is the same discipline the evidence count already follows.
    """
    terms = _terms(q)
    chunk_results = search(q, chunk_limit if chunk_limit is not None else max(limit, 10),
                           semantic=semantic)
    if not chunk_results:
        return {'knowledge': [], 'results': []}
    by_chunk = {c['id']: c for c in chunk_results if c.get('id')}

    claims_repo = ClaimRepository()
    claims = claims_repo.claims_for_chunks(list(by_chunk))
    if not claims:
        return {'knowledge': [], 'results': chunk_results}

    entity_ids = [c.get('subject_id') for c in claims] + [c.get('object_id') for c in claims]
    aliases = EntityRepository().aliases_for([i for i in entity_ids if i])
    disputed = claims_repo.disputed_claim_ids([str(c.get('id')) for c in claims])

    # Score first, then keep only the most relevant few per chunk: a chunk that
    # yielded ten claims must not push ten items into the answer.
    scored = []
    for claim in claims:
        chunk = by_chunk.get(claim.get('source_chunk_id') or '')
        if chunk is None:
            continue
        names = aliases.get(claim.get('subject_id'), []) + aliases.get(claim.get('object_id') or '', [])
        keywords = predicate_keywords(claim.get('predicate') or '')
        scored.append((relevance(claim, terms, aliases=names, keywords=keywords), claim, chunk))
    scored.sort(key=lambda x: -x[0])

    per_chunk: dict[str, int] = {}
    candidates: list[tuple[int, dict, dict]] = []
    for hit in scored:
        chunk_id = str(hit[2].get('id') or '')
        if per_chunk.get(chunk_id, 0) >= claims_per_chunk:
            continue
        per_chunk[chunk_id] = per_chunk.get(chunk_id, 0) + 1
        candidates.append(hit)

    # One result per *fact* — the same rule Knowledge QA uses, from one definition.
    views = best_per_statement(
        [claim for _, claim, _ in candidates],
        disputed_ids=disputed,
        scores={str(claim.get('id')): score for score, claim, _ in candidates},
        evidence={str(claim.get('id')): _evidence(chunk) for _, claim, chunk in candidates})
    views.sort(key=rank_key)
    return {'knowledge': [v.to_dict() for v in views[:limit]], 'results': chunk_results}


def _claim_relevance(claim: dict, terms: list[str]) -> int:
    hay = ' '.join(str(claim.get(k) or '') for k in ('content', 'source_quote', 'object_text', 'object_name', 'subject_name', 'predicate')).lower()
    return sum(1 for t in terms if t.lower() in hay)


def _current_claims(claims: list[dict]) -> list[dict]:
    """Resolve claim lifecycle for retrieval.

    The decision itself lives in ``app.domain.claim_state`` — the single
    definition of current knowledge shared by Search / Graph / QA / Review /
    Correction. This shim exists only for callers that predate the Domain module.
    """
    from .domain.claim_state import resolve
    return resolve(claims)


def evidence_pack(question: str, limit: int = 8) -> list[dict]:
    chunks = search(question, limit, semantic=True)
    if not chunks:
        return []
    terms = _terms(question)
    documents = DocumentRepository()
    claims_repo = ClaimRepository()
    enriched = []
    for x in chunks:
        row = documents.chunk(x['id'])
        if row:
            item = dict(row); item['score'] = x.get('score'); item['method'] = x.get('method')
            claim_rows = claims_repo.for_document_pack(row['document_id'], 40)
            # Only keep claims that overlap the question; unrelated claims in the
            # same document only dilute the grounding context.
            scored = sorted(claim_rows, key=lambda c: (_claim_relevance(c, terms), c.get('confidence') or 0), reverse=True)
            relevant = [c for c in scored if _claim_relevance(c, terms) > 0][:6]
            item['claims'] = _current_claims(relevant if relevant else scored[:3])
            enriched.append(item)
    return enriched


def _chunk_claims(chunk_id: str, limit: int) -> list[dict]:
    return _current_claims(ClaimRepository().for_chunk(chunk_id, limit))


def _document_claims(document_id: str, terms: list[str], limit: int) -> list[dict]:
    claims = _current_claims(ClaimRepository().for_document(document_id, 40))
    relevant = [c for c in claims if _claim_relevance(c, terms) > 0]
    return (relevant or claims)[:limit]


def evidence_hits(question: str, limit: int = 8, *, claims_per_chunk: int = 3) -> list[dict]:
    """Retrieved chunks together with the quote and claims that justify using them.

    The quote comes from the claims extracted from that same chunk, so the default
    evidence text is already minimal and traceable back to offsets - nothing has to
    be re-derived or guessed at answer time.
    """
    chunks = search(question, limit, semantic=True)
    if not chunks:
        return []
    terms = _terms(question)
    documents = DocumentRepository()
    hits: list[dict] = []
    for chunk in chunks:
        row = documents.chunk(chunk['id'])
        if not row:
            continue
        claims = _chunk_claims(row['id'], claims_per_chunk)
        if not claims:
            claims = _document_claims(row['document_id'], terms, claims_per_chunk)
        best = max(claims, key=lambda c: (c.get('confidence') or 0)) if claims else {}
        hits.append({
            'chunk_id': row['id'],
            'document_id': row['document_id'],
            'title': row['title'],
            'quote': str(best.get('source_quote') or '').strip(),
            'chunk_content': row['content'],
            'chunk_index': row['chunk_index'],
            'start_offset': row['start_offset'],
            'end_offset': row['end_offset'],
            'confidence': best.get('confidence'),
            'claims': claims,
            'score': chunk.get('score') or 0.0,
            'method': chunk.get('method') or '',
            'source_type': row['source_type'],
        })
    return hits


def entity_summaries(hits: list[dict], limit: int = 3) -> list[dict]:
    """Compact summaries of the entities the evidence talks about (summary-first)."""
    names: list[str] = []
    for hit in hits:
        for claim in hit.get('claims', []):
            for name in (claim.get('subject_name'), claim.get('object_name')):
                if name and name not in names:
                    names.append(str(name))
    if not names:
        return []
    rows = EntityRepository().names(names, 40)
    by_name = {r['name']: r for r in rows}
    out = []
    for name in names:
        row = by_name.get(name)
        if not row:
            continue
        out.append({'name': row['name'], 'type': row['type'],
                    'summary': (row['description'] or '')[:140], 'status': row['status']})
        if len(out) >= limit:
            break
    return out


def format_evidence(pack: list[dict]) -> str:
    return '\n\n'.join(
        f"[doc:{x['document_id']} chunk:{x['id']}] {x['title']}\n{x['content']}"
        + ("\nStructured claims:\n" + "\n".join(
            f"- {c.get('subject_name')} {c.get('predicate')} {c.get('object_name') or c.get('object_text') or ''} (confidence={c.get('confidence')})"
            for c in x.get('claims', [])
        ) if x.get('claims') else '')
        for x in pack
    )
