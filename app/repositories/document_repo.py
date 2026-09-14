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


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


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

    def get(self, doc_id: str) -> dict | None:
        with self.read() as conn:
            return row(conn.execute('SELECT * FROM documents WHERE id=?', (doc_id,)))

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

    def replace_chunks(self, document_id: str, chunks) -> list[dict]:
        """Replace all chunks (and their embeddings) for a document.

        ``chunks`` is an iterable of ``(content, chunk_index, start_offset, end_offset)``.
        """
        out: list[dict] = []
        with self.write() as conn:
            conn.execute('DELETE FROM chunk_embeddings WHERE chunk_id IN '
                         '(SELECT id FROM chunks WHERE document_id=?)', (document_id,))
            conn.execute('DELETE FROM chunks WHERE document_id=?', (document_id,))
            for content, index, start_offset, end_offset in chunks:
                cid = str(uuid.uuid4())
                conn.execute('''INSERT INTO chunks(id,document_id,content,chunk_index,start_offset,end_offset)
                                VALUES(?,?,?,?,?,?)''', (cid, document_id, content, index, start_offset, end_offset))
                out.append({'id': cid, 'content': content, 'chunk_index': index,
                            'start_offset': start_offset, 'end_offset': end_offset})
        return out

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
