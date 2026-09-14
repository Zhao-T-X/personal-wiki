"""§33 Embedding cache: vectors are reused by *content hash*, not by chunk id.

Re-chunking a document mints brand-new chunk ids for text that is byte-for-byte
unchanged; an id-keyed store would discard vectors that are still perfectly valid.
These tests prove the content-keyed reuse without touching a real model: the
``embed_texts`` backend is replaced by a deterministic, call-counting fake, and a
guard makes any attempt to reach fastembed / the remote endpoint raise.
"""
from __future__ import annotations

import importlib
import os
import sys

# Modules that bind the database path at import time must be reloaded after
# DATABASE_PATH moves, or they keep writing to the previous database.
_RELOAD = (
    'app.repositories.base',
    'app.repositories.document_repo',
    'app.embeddings',
)

_MODEL = 'fake-embed-v1'
_DIMS = 4


def _reload(tmp_path):
    """Same isolation contract as tests/test_correction.py."""
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config)
    importlib.reload(db)
    for name in _RELOAD:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


def _vector(text: str) -> list[float]:
    """A deterministic, content-dependent vector of fixed width (no model)."""
    return [float(len(text) % 7), float(sum(map(ord, text)) % 13),
            float(text.count('z')), 1.0]


class _FakeModel:
    """Stand-in for embed_texts: records every batch it is asked to embed."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        return [_vector(t) for t in texts], _MODEL

    @property
    def texts_computed(self) -> int:
        return sum(len(batch) for batch in self.calls)


def _patch(monkeypatch, embeddings) -> _FakeModel:
    model = _FakeModel()
    monkeypatch.setattr(embeddings, 'active_model_name', lambda: _MODEL)
    monkeypatch.setattr(embeddings, 'embed_texts', model)
    return model


def _blobs_by_content(db) -> dict[str, bytes]:
    conn = db.connect()
    out = {r['content']: bytes(r['embedding_blob']) for r in conn.execute(
        'SELECT c.content, ce.embedding_blob FROM chunk_embeddings ce '
        'JOIN chunks c ON c.id=ce.chunk_id')}
    conn.close()
    return out


def test_reuses_vectors_by_content_across_rechunk(tmp_path, monkeypatch):
    """CORE EVIDENCE: identical text survives a re-chunk with zero recomputation."""
    db = _reload(tmp_path)
    import app.embeddings as embeddings
    from app.repositories.document_repo import DocumentRepository

    model = _patch(monkeypatch, embeddings)
    docs = DocumentRepository()
    doc_id = docs.create(title='Rechunk', content='alpha beta gamma delta')
    contents = [('alpha beta', 0, 0, 10),
                ('beta gamma', 1, 10, 20),
                ('gamma delta', 2, 20, 31)]
    docs.replace_chunks(doc_id, contents)
    n = len(contents)

    first = embeddings.embed_document(doc_id)
    assert first['embedded'] == n
    assert first['computed'] == n
    assert first['cached'] == 0
    assert first['model'] == _MODEL
    assert first['dims'] == _DIMS
    assert len(model.calls) == 1          # one batch, not one call per chunk
    before = _blobs_by_content(db)

    calls_after_first = len(model.calls)

    # A second document whose *chunk texts* are identical (its document body
    # differs, because documents.content_hash is unique). Its chunk ids are brand
    # new, so an id-keyed store would recompute every vector; content-keyed reuse
    # must compute none. Since Phase 6 the chunk rows themselves are reused for
    # unchanged text, which is why this is exercised on a second document.
    other_id = docs.create(title='Same chunks elsewhere', content='a different document body')
    docs.replace_chunks(other_id, contents)

    second = embeddings.embed_document(other_id)
    assert second['embedded'] == n
    assert second['cached'] == n          # <- everything came from the cache
    assert second['computed'] == 0
    assert len(model.calls) == calls_after_first   # no new model call at all

    # Byte-identical vectors, and both documents carry them.
    assert _blobs_by_content(db) == before
    conn = db.connect()
    assert conn.execute('SELECT COUNT(*) c FROM chunk_embeddings').fetchone()['c'] == 2 * n
    conn.close()


def test_only_new_content_is_computed(tmp_path, monkeypatch):
    """A partial change recomputes only the changed text, not the whole document."""
    db = _reload(tmp_path)
    import app.embeddings as embeddings
    from app.repositories.document_repo import DocumentRepository

    model = _patch(monkeypatch, embeddings)
    docs = DocumentRepository()
    doc_id = docs.create(title='Growing', content='alpha beta gamma delta epsilon')
    docs.replace_chunks(doc_id, [('alpha beta', 0, 0, 10),
                                 ('beta gamma', 1, 10, 20),
                                 ('gamma delta', 2, 20, 30)])
    first = embeddings.embed_document(doc_id)
    assert (first['cached'], first['computed']) == (0, 3)

    docs.replace_chunks(doc_id, [('alpha beta', 0, 0, 10),
                                 ('beta gamma', 1, 10, 20),
                                 ('gamma delta', 2, 20, 30),
                                 ('epsilon zeta', 3, 30, 42)])
    second = embeddings.embed_document(doc_id)
    assert second['embedded'] == 4
    assert second['cached'] == 3
    assert second['computed'] == 1
    assert 0 < second['computed'] < 4
    assert model.calls[-1] == ['epsilon zeta']   # only the new text reached the model
    assert model.texts_computed == 3 + 1         # 3 first pass + 1 new


def test_embedding_cache_rows_and_dims_are_consistent(tmp_path, monkeypatch):
    db = _reload(tmp_path)
    import app.embeddings as embeddings
    from app.repositories.document_repo import DocumentRepository

    _patch(monkeypatch, embeddings)
    docs = DocumentRepository()
    doc_id = docs.create(title='Consistency', content='one two three')
    contents = [('one', 0, 0, 3), ('two', 1, 3, 6), ('three', 2, 6, 11)]
    docs.replace_chunks(doc_id, contents)
    embeddings.embed_document(doc_id)

    conn = db.connect()
    cache = {r['content_hash']: r for r in conn.execute(
        'SELECT content_hash,model,dims,embedding_blob FROM embedding_cache')}
    assert len(cache) == len(contents)
    for content, *_ in contents:
        h = embeddings.content_hash(content)
        assert h in cache
        assert cache[h]['model'] == _MODEL
        assert cache[h]['dims'] == _DIMS
        assert len(cache[h]['embedding_blob']) == _DIMS * 4   # float32 per dim

    stored = conn.execute(
        'SELECT ce.model, ce.dims, ce.embedding_blob, c.content FROM chunk_embeddings ce '
        'JOIN chunks c ON c.id=ce.chunk_id').fetchall()
    conn.close()
    assert len(stored) == len(contents)
    for r in stored:
        h = embeddings.content_hash(r['content'])
        assert r['model'] == _MODEL
        assert r['dims'] == _DIMS
        assert bytes(r['embedding_blob']) == bytes(cache[h]['embedding_blob'])


def test_backfill_embeddings_still_returns_embedded(tmp_path, monkeypatch):
    db = _reload(tmp_path)
    import app.embeddings as embeddings
    from app.repositories.document_repo import DocumentRepository

    _patch(monkeypatch, embeddings)
    docs = DocumentRepository()
    doc_id = docs.create(title='Backfill', content='alpha beta')
    docs.replace_chunks(doc_id, [('alpha', 0, 0, 5), ('beta', 1, 5, 9)])

    result = embeddings.backfill_embeddings()
    assert result['embedded_documents'] == 1
    assert result['chunks'] == 2
    assert result['model'] == _MODEL

    # embed_document keeps the 'embedded' key backfill relies on.
    single = embeddings.embed_document(doc_id)
    assert 'embedded' in single and single['embedded'] == 2
    # Nothing left to do once every chunk is embedded.
    assert embeddings.backfill_embeddings()['chunks'] == 0


def test_no_real_model_is_ever_invoked(tmp_path, monkeypatch):
    """Any path to a real backend raises; the fake's call count is the evidence."""
    db = _reload(tmp_path)
    import app.embeddings as embeddings
    from app.repositories.document_repo import DocumentRepository

    def _boom(*args, **kwargs):
        raise AssertionError('a real embedding backend was reached')

    monkeypatch.setattr(embeddings, '_get_local', _boom)
    monkeypatch.setattr(embeddings, '_client', _boom)
    model = _patch(monkeypatch, embeddings)

    docs = DocumentRepository()
    doc_id = docs.create(title='NoNetwork', content='alpha beta')
    contents = [('alpha', 0, 0, 5), ('beta', 1, 5, 9)]
    docs.replace_chunks(doc_id, contents)
    embeddings.embed_document(doc_id)
    docs.replace_chunks(doc_id, contents)
    embeddings.embed_document(doc_id)

    # Two full passes, but the model saw exactly one batch of two texts.
    assert len(model.calls) == 1
    assert model.texts_computed == 2
