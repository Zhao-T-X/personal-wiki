"""Documents and chunks — the raw layer (never overwritten by structured output)."""
from __future__ import annotations

import hashlib
import uuid

from ..db import dumps
from .base import Repository, one, row, rows

_LIST_SQL = '''SELECT d.id,d.title,d.source_type,d.source_uri,d.created_at,d.updated_at,
    (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) chunk_count,
    (SELECT status FROM llm_runs r WHERE r.document_id=d.id ORDER BY r.created_at DESC LIMIT 1) last_run_status,
    (SELECT created_at FROM llm_runs r WHERE r.document_id=d.id ORDER BY r.created_at DESC LIMIT 1) last_run_at
    FROM documents d ORDER BY d.updated_at DESC LIMIT ?'''

_CHUNK_DOC_SQL = '''SELECT c.id,c.document_id,c.content,c.chunk_index,c.start_offset,c.end_offset,d.title,d.source_type
                    FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id=?'''

# Incremental processing (§32) needs the two fingerprint columns that ``chunks()``
# (a public, stable shape) deliberately omits.
_CHUNK_FINGERPRINTS_SQL = '''SELECT id,document_id,content,chunk_index,start_offset,end_offset,
                                    content_hash,extraction_version,created_at
                             FROM chunks WHERE document_id=? ORDER BY chunk_index'''


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


def _normalize_targets(chunks) -> list[tuple]:
    """Coerce an iterable of ``(content, index, start, end)`` into a list."""
    return [(content, int(index), int(start_offset), int(end_offset))
            for content, index, start_offset, end_offset in chunks]


def _diff_chunks(existing: list[dict], targets: list[tuple]) -> dict:
    """Classify ``targets`` against the stored chunks by content fingerprint.

    A chunk is *unchanged* when the stored ``content_hash`` equals the hash of the
    incoming content at the same ``chunk_index``; its row — and therefore its id,
    embeddings and ``extraction_version`` — is reused. A NULL stored hash means
    "unknown" (a row predating the fingerprint column), so it is treated as
    changed and re-extracted once: guessing "unchanged" would silently keep stale
    knowledge, so the safe direction is to redo it.

    Returns index/id classifications, never touching the database.
    """
    by_index = {row['chunk_index']: row for row in existing}
    wanted = {index for _, index, _, _ in targets}
    removed = [row['id'] for index, row in sorted(by_index.items()) if index not in wanted]
    unchanged: list[str] = []
    created: list[int] = []
    changed: list[int] = []
    replaced: list[str] = []
    for content, index, _, _ in sorted(targets, key=lambda t: t[1]):
        row = by_index.get(index)
        if row is None:
            created.append(index)
        elif row['content_hash'] == _hash(content):
            unchanged.append(row['id'])
        else:
            changed.append(index)
            replaced.append(row['id'])
    return {'unchanged': unchanged, 'created': created, 'changed': changed,
            'replaced': replaced, 'removed': removed}


class DocumentRepository(Repository):
    # -- documents ---------------------------------------------------------
    def create(self, *, title: str, content: str, source_type: str = 'note',
               source_uri: str | None = None, metadata: dict | None = None) -> str:
        doc_id = str(uuid.uuid4())
        with self.write() as conn:
            conn.execute('''INSERT INTO documents(id,title,content,source_type,source_uri,content_hash,metadata_json)
                            VALUES(?,?,?,?,?,?,?)''',
                         (doc_id, title, content, source_type, source_uri, _hash(content), dumps(metadata or {})))
        return doc_id

    def by_source_uri(self, source_uri: str) -> str | None:
        """The document a given origin already produced, if any.

        ``source_uri`` is what links a generated document to the thing that generated
        it — a research task, for instance — so re-running a proposal updates the
        document instead of piling up copies, and a claim's origin is a lookup rather
        than a guess.
        """
        with self.read() as conn:
            from .base import one
            return one(conn.execute('SELECT id FROM documents WHERE source_uri=? LIMIT 1',
                                    (source_uri,)), 'id')

    def get(self, doc_id: str) -> dict | None:
        with self.read() as conn:
            return row(conn.execute('SELECT * FROM documents WHERE id=?', (doc_id,)))

    def source_types(self, document_ids: list[str]) -> dict[str, str]:
        """``source_type`` per document id, in one query — provenance for a batch.

        What several callers need to know about a set of documents when deciding how to
        treat the claims that came from them (a research proposal is not knowledge yet;
        an extraction is), and asking one document at a time would turn a three-claim
        answer into three round trips for one column.
        """
        ids = [i for i in dict.fromkeys(document_ids) if i]
        if not ids:
            return {}
        marks = ','.join('?' * len(ids))
        with self.read() as conn:
            return {r['id']: r['source_type'] for r in rows(conn.execute(
                f'SELECT id, source_type FROM documents WHERE id IN ({marks})', ids))}

    def exists(self, doc_id: str) -> bool:
        with self.read() as conn:
            return conn.execute('SELECT 1 FROM documents WHERE id=?', (doc_id,)).fetchone() is not None

    def list(self, limit: int) -> tuple[list[dict], int]:
        with self.read() as conn:
            items = rows(conn.execute(_LIST_SQL, (limit,)))
            total = one(conn.execute('SELECT COUNT(*) c FROM documents'), 'c')
        return items, total

    def update(self, doc_id: str, *, title: str, content: str, source_type: str,
               source_uri: str | None, metadata: dict | None) -> int:
        """Returns affected row count. May raise sqlite3.IntegrityError (content hash)."""
        with self.write() as conn:
            cur = conn.execute(
                "UPDATE documents SET title=?,content=?,source_type=?,source_uri=?,content_hash=?,"
                "metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (title, content, source_type, source_uri, _hash(content), dumps(metadata or {}), doc_id))
            return cur.rowcount

    def delete(self, doc_id: str) -> bool:
        with self.write() as conn:
            return bool(conn.execute('DELETE FROM documents WHERE id=?', (doc_id,)).rowcount)

    # -- chunks ------------------------------------------------------------
    def chunks(self, doc_id: str) -> list[dict]:
        with self.read() as conn:
            return rows(conn.execute(
                'SELECT id,document_id,content,chunk_index,start_offset,end_offset,created_at '
                'FROM chunks WHERE document_id=? ORDER BY chunk_index', (doc_id,)))

    def chunk_rows(self, doc_id: str) -> list[dict]:
        """Chunks joined with document metadata (retrieval expansion)."""
        with self.read() as conn:
            return rows(conn.execute(
                '''SELECT c.id,c.document_id,c.content,c.chunk_index,c.start_offset,c.end_offset,
                          d.title,d.source_type
                   FROM chunks c JOIN documents d ON d.id=c.document_id
                   WHERE c.document_id=? ORDER BY c.chunk_index''', (doc_id,)))

    def chunk(self, chunk_id: str) -> dict | None:
        with self.read() as conn:
            return row(conn.execute(_CHUNK_DOC_SQL, (chunk_id,)))

    def plan_chunks(self, document_id: str, chunks) -> dict:
        """Read-only diff of a document's stored chunks against ``chunks``.

        ``chunks`` is an iterable of ``(content, chunk_index, start_offset, end_offset)``.
        Nothing is written; the caller uses the result to clear only the derived
        rows whose chunks are about to disappear (foreign keys must be satisfied
        *before* :meth:`sync_chunks` deletes the chunk rows).
        """
        targets = _normalize_targets(chunks)
        with self.read() as conn:
            existing = rows(conn.execute(_CHUNK_FINGERPRINTS_SQL, (document_id,)))
        diff = _diff_chunks(existing, targets)
        return {
            'unchanged': diff['unchanged'],            # existing ids kept as-is
            'created': diff['created'],                # indexes with no existing row
            'changed': diff['changed'],                # indexes whose content changed
            'replaced': diff['replaced'],              # existing ids discarded (content changed)
            'removed': diff['removed'],                # existing ids discarded (index gone)
            'dropped': [*diff['replaced'], *diff['removed']],
        }

    def sync_chunks(self, document_id: str, chunks) -> dict:
        """Reconcile a document's chunks with ``chunks``, reusing unchanged rows.

        ``chunks`` is an iterable of ``(content, chunk_index, start_offset, end_offset)``.

        Rows whose content hash is unchanged keep their id, their
        ``extraction_version`` and their embeddings, so knowledge derived from them
        is never thrown away. Rows whose content changed (or whose index no longer
        exists) are replaced by fresh rows with a NULL ``extraction_version``.

        The caller must first clear the derived claims/relations/events/ideas/
        questions that point at ``plan_chunks(...)['dropped']``: those chunk rows
        are deleted here and a live reference would fail the foreign-key check.

        Returns ``{'chunks', 'unchanged', 'created', 'changed', 'replaced',
        'removed', 'stale', 'dropped'}`` where ``stale`` is ``created + changed``
        (the chunk ids that need extraction) and ``dropped`` is ``replaced +
        removed`` (the ids that were discarded).
        """
        targets = _normalize_targets(chunks)
        with self.write() as conn:
            diff = _diff_chunks(rows(conn.execute(_CHUNK_FINGERPRINTS_SQL, (document_id,))), targets)
            for old_id in (*diff['replaced'], *diff['removed']):
                # chunk_embeddings cascades with the chunk row; derived knowledge
                # referencing it was cleared by the caller beforehand.
                conn.execute('DELETE FROM chunks WHERE id=?', (old_id,))
            created: list[str] = []
            changed: list[str] = []
            for content, index, start_offset, end_offset in sorted(targets, key=lambda t: t[1]):
                if index not in diff['created'] and index not in diff['changed']:
                    continue
                cid = str(uuid.uuid4())
                conn.execute(
                    '''INSERT INTO chunks(id,document_id,content,chunk_index,start_offset,end_offset,
                                          content_hash,extraction_version)
                       VALUES(?,?,?,?,?,?,?,NULL)''',
                    (cid, document_id, content, index, start_offset, end_offset, _hash(content)))
                (created if index in diff['created'] else changed).append(cid)
            return {
                'chunks': rows(conn.execute(_CHUNK_FINGERPRINTS_SQL, (document_id,))),
                'unchanged': diff['unchanged'],
                'created': created,
                'changed': changed,
                'replaced': diff['replaced'],
                'removed': diff['removed'],
                'stale': [*created, *changed],
                'dropped': [*diff['replaced'], *diff['removed']],
            }

    def replace_chunks(self, document_id: str, chunks) -> list[dict]:
        """Replace the chunks for a document; returns the resulting chunk rows.

        Kept as the stable entry point for existing callers: it now delegates to
        :meth:`sync_chunks`, so an identical chunk keeps its row (and its
        embeddings) instead of always being dropped and re-created. Changed and
        removed chunks are still replaced, and their embeddings cascade away with
        them.
        """
        return self.sync_chunks(document_id, chunks)['chunks']

    def set_extraction_version(self, chunk_ids, version: str) -> int:
        """Stamp the extractor+ontology version onto the chunks that were just extracted."""
        ids = list(chunk_ids)
        if not ids:
            return 0
        marks = ','.join('?' for _ in ids)
        with self.write() as conn:
            return conn.execute(
                f'UPDATE chunks SET extraction_version=? WHERE id IN ({marks})',
                (version, *ids)).rowcount

    # -- lexical search ----------------------------------------------------
    def search_fts(self, match: str, cap: int) -> list[dict]:
        if not match:
            return []
        with self.read() as conn:
            try:
                return rows(conn.execute('''
                  SELECT d.id AS document_id, d.title, d.source_type, d.content,
                         bm25(documents_fts) AS score
                  FROM documents_fts f
                  JOIN documents d ON d.rowid=f.rowid
                  WHERE documents_fts MATCH ?
                  ORDER BY score
                  LIMIT ?''', (match, cap)))
            except Exception:
                return []

    def search_like(self, terms: list[str], cap: int) -> list[dict]:
        if not terms:
            return []
        clause = ' OR '.join(['d.title LIKE ? OR d.content LIKE ?'] * len(terms))
        params: list = []
        for term in terms:
            params += [f'%{term}%', f'%{term}%']
        with self.read() as conn:
            return rows(conn.execute(
                f'''SELECT d.id AS document_id, d.title, d.source_type, d.content, 0.0 AS score
                    FROM documents d WHERE {clause} LIMIT ?''', (*params, cap)))
