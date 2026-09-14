from __future__ import annotations

import hashlib
import os
import struct
from urllib.parse import urlparse

# fastembed pulls its models from HuggingFace, which is unreachable from some
# networks; default to the community mirror unless explicitly overridden.
os.environ.setdefault('HF_ENDPOINT', 'https://hf-mirror.com')

import numpy as np
from .config import runtime
from .db import connect, transaction

# Providers that serve chat completions but expose no /embeddings route, whatever
# the configured model name suggests. Pointing the app at one of these used to
# send every embed request to a nonexistent endpoint, which is why vector search
# silently stayed empty; such a config must fall back to the local model instead.
_NO_EMBEDDING_HOSTS = ('deepseek.com',)

_local_model = None


def _client():
    cfg = runtime()
    if not cfg['openai_api_key']:
        raise RuntimeError('API key is not configured')
    from openai import OpenAI
    return OpenAI(api_key=cfg['openai_api_key'], base_url=cfg['openai_base_url'])


def _local_name() -> str:
    return os.getenv('LOCAL_EMBEDDING_MODEL', 'BAAI/bge-small-zh-v1.5')


def _local_path() -> str:
    return os.getenv('LOCAL_EMBEDDING_PATH', os.path.join('data', 'models', 'bge-small-zh-v1.5'))


def _use_remote() -> bool:
    """Use the OpenAI-compatible embeddings endpoint only when it can succeed.

    Two conditions matter: the model name must look like an OpenAI embedding model,
    and the endpoint must actually serve embeddings. Checking the name alone sent
    requests to DeepSeek's /embeddings, which does not exist — vectors then stayed
    empty while every search quietly degraded to keyword-only.
    """
    cfg = runtime()
    if not cfg['openai_api_key'] or not cfg['openai_embedding_model'].startswith('text-embedding'):
        return False
    host = urlparse(cfg['openai_base_url']).netloc.lower()
    return not any(p in host for p in _NO_EMBEDDING_HOSTS)


def _get_local():
    global _local_model
    if _local_model is None:
        from fastembed import TextEmbedding
        # specific_model_path short-circuits the HuggingFace download entirely
        # (the model was fetched from modelscope.com into data/models/…).
        _local_model = TextEmbedding(model_name=_local_name(), specific_model_path=_local_path())
    return _local_model


def active_model_name() -> str:
    return runtime()['openai_embedding_model'] if _use_remote() else _local_name()


def content_hash(text: str) -> str:
    """Stable fingerprint of the embedding input (§33).

    Keyed on the *text*, not the chunk id: identical text embeds identically for a
    given model, so the hash survives re-chunking (which mints new chunk ids).
    """
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def pack(vec: list[float]) -> bytes:
    return struct.pack(f'<{len(vec)}f', *vec)


def unpack(blob: bytes, dims: int) -> np.ndarray:
    return np.frombuffer(blob, dtype='<f4', count=dims)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return 0.0 if denom == 0 else float(np.dot(a, b) / denom)


def embed_texts(texts: list[str]) -> tuple[list[list[float]], str]:
    """Embed texts via the OpenAI-compatible endpoint when configured; fall
    back to the local fastembed model otherwise (or when remote fails).
    Returns (vectors, model_name) so callers can store/compare per model."""
    if _use_remote():
        try:
            result = _client().embeddings.create(model=runtime()['openai_embedding_model'], input=texts)
            return [item.embedding for item in sorted(result.data, key=lambda x: x.index)], runtime()['openai_embedding_model']
        except Exception:
            pass
    vectors = _get_local().embed(texts)
    return [list(map(float, v)) for v in vectors], _local_name()


def embed_document(document_id: str) -> dict:
    conn = connect()
    rows = conn.execute('SELECT id,content FROM chunks WHERE document_id=? ORDER BY chunk_index', (document_id,)).fetchall()
    conn.close()
    if not rows:
        return {'document_id': document_id, 'embedded': 0, 'skipped': True}

    # The model we *ask* for. embed_texts may fall back to the local model (and
    # reports which model actually produced the vectors); a vector is only reusable
    # for the model that made it, so the two names must be kept apart below.
    query_model = active_model_name()
    hashes = [content_hash(r['content']) for r in rows]

    # Look the vectors up by (content_hash, model) instead of by chunk id. Text that
    # is byte-for-byte identical embeds identically for a given model, so re-chunking
    # a document — which mints fresh chunk ids — no longer throws away usable vectors.
    conn = connect()
    cached_vectors: dict[str, list[float]] = {}
    for h in set(hashes):
        hit = conn.execute(
            'SELECT embedding_blob,dims FROM embedding_cache WHERE content_hash=? AND model=?',
            (h, query_model)).fetchone()
        if hit is not None:
            cached_vectors[h] = list(unpack(hit['embedding_blob'], hit['dims']))
    conn.close()

    # Everything the cache could not answer goes out in a single batch: a miss must
    # cost one call, never one call per chunk.
    pending = [(r, h) for r, h in zip(rows, hashes) if h not in cached_vectors]
    computed_vectors: list[list[float]] = []
    model_name = query_model
    if pending:
        computed_vectors, model_name = embed_texts([r['content'] for r, _ in pending])
        # embed_texts hands back the model that *actually* served the request. Cache
        # under that real name: if the remote endpoint failed and we fell back to the
        # local model, the lookup above (done under `query_model`) simply missed —
        # which is honest, because vectors from model A are not hits for model B.
        for (_, h), vec in zip(pending, computed_vectors):
            cached_vectors[h] = vec

    # One vector per chunk, in chunk order, each either reused or freshly computed.
    vectors = [cached_vectors[h] for h in hashes]
    with transaction() as conn:
        for (_, h), vec in zip(pending, computed_vectors):
            conn.execute(
                '''INSERT INTO embedding_cache(content_hash,model,dims,embedding_blob)
                   VALUES(?,?,?,?)
                   ON CONFLICT(content_hash,model) DO UPDATE SET dims=excluded.dims,embedding_blob=excluded.embedding_blob,created_at=CURRENT_TIMESTAMP''',
                (h, model_name, len(vec), pack(vec)),
            )
        for row, vec in zip(rows, vectors):
            conn.execute(
                '''INSERT INTO chunk_embeddings(chunk_id,model,dims,embedding_blob)
                   VALUES(?,?,?,?)
                   ON CONFLICT(chunk_id) DO UPDATE SET model=excluded.model,dims=excluded.dims,embedding_blob=excluded.embedding_blob,created_at=CURRENT_TIMESTAMP''',
                (row['id'], model_name, len(vec), pack(vec)),
            )
    return {'document_id': document_id, 'embedded': len(rows), 'model': model_name,
            'dims': len(vectors[0]), 'cached': len(rows) - len(pending),
            'computed': len(pending)}


def backfill_embeddings() -> dict:
    """Embed every document that still has un-embedded chunks."""
    conn = connect()
    docs = [r['id'] for r in conn.execute('''SELECT d.id FROM documents d
        WHERE EXISTS (SELECT 1 FROM chunks c WHERE c.document_id=d.id)
        AND NOT EXISTS (SELECT 1 FROM chunks c JOIN chunk_embeddings ce ON ce.chunk_id=c.id WHERE c.document_id=d.id)''').fetchall()]
    conn.close()
    results = [embed_document(d) for d in docs]
    return {'embedded_documents': len(docs), 'chunks': sum(r.get('embedded', 0) for r in results),
            'model': results[0]['model'] if results else active_model_name()}


def semantic_search(query: str, limit: int = 10) -> list[dict]:
    qvec_raw, model_name = embed_texts([query])
    qvec = np.asarray(qvec_raw[0], dtype='<f4')
    conn = connect()
    rows = conn.execute('''
      SELECT ce.chunk_id, ce.dims, ce.embedding_blob,
             c.document_id, c.content, c.chunk_index, c.start_offset, c.end_offset,
             d.title, d.source_type
      FROM chunk_embeddings ce
      JOIN chunks c ON c.id=ce.chunk_id
      JOIN documents d ON d.id=c.document_id
      WHERE ce.model=?
    ''', (model_name,)).fetchall()
    conn.close()
    scored = []
    for r in rows:
        vec = unpack(r['embedding_blob'], r['dims'])
        scored.append({
            'id': r['chunk_id'], 'document_id': r['document_id'], 'title': r['title'],
            'content': r['content'], 'source_type': r['source_type'],
            'chunk_index': r['chunk_index'], 'start_offset': r['start_offset'], 'end_offset': r['end_offset'],
            'score': cosine(qvec, vec), 'method': 'semantic'
        })
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:limit]
