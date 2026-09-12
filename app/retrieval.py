from __future__ import annotations
import json
import re

from .db import connect
from .config import runtime

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


def _like_search(conn, q: str, cap: int) -> list[dict]:
    """Substring fallback for queries the trigram index cannot serve.

    A personal wiki holds hundreds of documents, not millions, so a LIKE scan is
    cheap — and unlike FTS it still works for a two-character Chinese query.
    """
    terms = _terms(q)
    if not terms:
        return []
    clause = ' OR '.join(['d.title LIKE ? OR d.content LIKE ?'] * len(terms))
    params: list = []
    for term in terms:
        params += [f'%{term}%', f'%{term}%']
    return [dict(r) for r in conn.execute(
        f'''SELECT d.id AS document_id, d.title, d.source_type, d.content, 0.0 AS score
            FROM documents d WHERE {clause} LIMIT ?''', (*params, cap)).fetchall()]


def lexical_search(q: str, limit: int = 10) -> list[dict]:
    conn = connect()
    cap = min(limit, runtime()['max_search_results'])
    rows = []
    match = _fts_query(q)
    if match:
        try:
            rows = conn.execute('''
              SELECT d.id AS document_id, d.title, d.source_type, d.content,
                     bm25(documents_fts) AS score
              FROM documents_fts f
              JOIN documents d ON d.rowid=f.rowid
              WHERE documents_fts MATCH ?
              ORDER BY score
              LIMIT ?
            ''', (match, cap)).fetchall()
        except Exception:
            rows = []
    if not rows:
        rows = _like_search(conn, q, cap)
    conn.close()
    return [dict(r) | {'method':'lexical'} for r in rows]


def _chunk_results_from_documents(doc_results: list[dict], cap: int, terms: list[str]) -> list[dict]:
    """Expand matched documents into chunks, but only keep the chunks that
    actually contain the query terms (best-first, max 3 per document) — pulling
    every chunk of a matched document injects noise into the evidence pack."""
    conn = connect()
    picked: list[tuple[float, dict]] = []
    seen = set()
    lowered = [t.lower() for t in terms if t.strip()]
    for doc in doc_results:
        rows = conn.execute('''SELECT c.id,c.document_id,c.content,c.chunk_index,c.start_offset,c.end_offset,
                                      d.title,d.source_type
                               FROM chunks c JOIN documents d ON d.id=c.document_id
                               WHERE c.document_id=? ORDER BY c.chunk_index''', (doc['document_id'],)).fetchall()
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
    conn.close()
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


def _claim_relevance(claim: dict, terms: list[str]) -> int:
    hay = ' '.join(str(claim.get(k) or '') for k in ('content', 'source_quote', 'object_text', 'object_name', 'subject_name', 'predicate')).lower()
    return sum(1 for t in terms if t.lower() in hay)


def _current_claims(claims: list[dict]) -> list[dict]:
    """Resolve claim lifecycle for retrieval (design §21: prefer current knowledge).

    A superseded claim is history, not an error:
    - when something current covers the same subject+predicate it is dropped, so
      "who is the CEO" can never be answered from a statement that was replaced;
    - when nothing current covers it, it is kept and flagged `lifecycle`, so
      "who *was* the CEO" still has evidence to answer from.
    """
    if not claims:
        return claims
    covered = {(c.get('subject_name'), c.get('predicate'))
               for c in claims if c.get('status') != 'superseded'}
    out = []
    for claim in claims:
        if claim.get('status') == 'superseded':
            if (claim.get('subject_name'), claim.get('predicate')) in covered:
                continue
            out.append({**claim, 'lifecycle': 'superseded'})
        else:
            out.append(claim)
    return out


def evidence_pack(question: str, limit: int = 8) -> list[dict]:
    chunks = search(question, limit, semantic=True)
    if not chunks:
        return []
    terms = _terms(question)
    conn = connect()
    enriched = []
    for x in chunks:
        row = conn.execute('''SELECT c.id,c.document_id,c.content,c.chunk_index,c.start_offset,c.end_offset,d.title,d.source_type
                              FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id=?''', (x['id'],)).fetchone()
        if row:
            item = dict(row); item['score'] = x.get('score'); item['method'] = x.get('method')
            claim_rows = [dict(c) for c in conn.execute('''SELECT c.content,c.source_quote,c.predicate,c.object_text,c.confidence,c.status,s.name subject_name,o.name object_name
                                          FROM claims c JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id
                                          WHERE c.source_document_id=? AND c.status != 'rejected' ORDER BY c.created_at DESC LIMIT 40''',(row['document_id'],)).fetchall()]
            # Only keep claims that overlap the question; unrelated claims in the
            # same document only dilute the grounding context.
            scored = sorted(claim_rows, key=lambda c: (_claim_relevance(c, terms), c.get('confidence') or 0), reverse=True)
            relevant = [c for c in scored if _claim_relevance(c, terms) > 0][:6]
            item['claims'] = _current_claims(relevant if relevant else scored[:3])
            enriched.append(item)
    conn.close()
    return enriched


_CLAIM_COLUMNS = '''c.content,c.source_quote,c.predicate,c.polarity,c.modality,c.confidence,c.status,
                    c.context_json,s.name subject_name,o.name object_name,c.object_text'''


def _to_claim(row) -> dict:
    claim = dict(row)
    try:
        claim['context'] = json.loads(claim.pop('context_json', '{}') or '{}')
    except Exception:
        claim['context'] = {}
    return claim


def _chunk_claims(conn, chunk_id: str, limit: int) -> list[dict]:
    rows = conn.execute(f'''SELECT {_CLAIM_COLUMNS}
                            FROM claims c JOIN entities s ON s.id=c.subject_id
                            LEFT JOIN entities o ON o.id=c.object_id
                            WHERE c.source_chunk_id=? AND c.status!='rejected'
                            ORDER BY c.confidence DESC LIMIT ?''', (chunk_id, limit)).fetchall()
    return _current_claims([_to_claim(r) for r in rows])


def _document_claims(conn, document_id: str, terms: list[str], limit: int) -> list[dict]:
    rows = conn.execute(f'''SELECT {_CLAIM_COLUMNS}
                            FROM claims c JOIN entities s ON s.id=c.subject_id
                            LEFT JOIN entities o ON o.id=c.object_id
                            WHERE c.source_document_id=? AND c.status!='rejected'
                            ORDER BY c.confidence DESC LIMIT 40''', (document_id,)).fetchall()
    claims = _current_claims([_to_claim(r) for r in rows])
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
    conn = connect()
    hits: list[dict] = []
    try:
        for chunk in chunks:
            row = conn.execute('''SELECT c.id,c.document_id,c.content,c.chunk_index,
                                         c.start_offset,c.end_offset,d.title,d.source_type
                                  FROM chunks c JOIN documents d ON d.id=c.document_id
                                  WHERE c.id=?''', (chunk['id'],)).fetchone()
            if not row:
                continue
            claims = _chunk_claims(conn, row['id'], claims_per_chunk)
            if not claims:
                claims = _document_claims(conn, row['document_id'], terms, claims_per_chunk)
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
    finally:
        conn.close()
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
    conn = connect()
    try:
        placeholders = ','.join('?' for _ in names[:40])
        rows = conn.execute(f'''SELECT name,type,description,status FROM entities
                                WHERE name IN ({placeholders})''', names[:40]).fetchall()
    finally:
        conn.close()
    by_name = {r['name']: dict(r) for r in rows}
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
