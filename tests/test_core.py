import os
import tempfile
from pathlib import Path


def _reload_db(tmp_path):
    os.environ["DATABASE_PATH"] = str(tmp_path / "test.db")
    os.environ["SETTINGS_PATH"] = str(tmp_path / "settings.json")
    import importlib
    import sys
    import app.config as config
    import app.db as db
    importlib.reload(config)
    importlib.reload(db)
    # Modules that bound the previous db helpers at import time must be
    # reloaded too, otherwise they keep writing to an older database.
    for name in _DB_BOUND_MODULES:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


_DB_BOUND_MODULES = (
    "app.service",
    "app.knowledge",
    "app.resolution",
    "app.retrieval",
    "app.graph",
    "app.importer",
    "app.embeddings",
)


def test_chunking_offsets():
    from app.chunking import chunk_text
    text = "hello world\n\n" * 100
    chunks = chunk_text(text, max_chars=100, overlap=10)
    assert chunks
    assert chunks[0].start_offset == 0
    assert chunks[-1].end_offset == len(text)
    assert all(c.end_offset > c.start_offset for c in chunks)


def test_document_fts_search(tmp_path):
    db = _reload_db(tmp_path)
    conn = db.connect()
    conn.execute("INSERT INTO documents(id,title,content,source_type,content_hash) VALUES('d1','RAG','retrieval augmented generation','note','hash1')")
    conn.commit()
    rows = conn.execute(
        "SELECT d.id FROM documents_fts f JOIN documents d ON d.rowid=f.rowid WHERE documents_fts MATCH ?",
        ('"retrieval augmented"',)
    ).fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["d1"]


def test_entity_alias_resolution(tmp_path):
    db = _reload_db(tmp_path)
    from app.resolution import resolve_or_create_entity
    conn = db.connect()
    first = resolve_or_create_entity(conn, name="Retrieval-Augmented Generation", entity_types=["Concept", "Technology"], aliases=["RAG"], description=None, properties={})
    second = resolve_or_create_entity(conn, name="RAG", entity_types=["Concept", "Technology"], aliases=[], description=None, properties={})
    conn.commit()
    rows = conn.execute("SELECT id FROM entities").fetchall()
    conn.close()
    assert first == second
    assert len(rows) == 1


def test_provenance_requires_real_chunk(tmp_path):
    db = _reload_db(tmp_path)
    conn = db.connect()
    conn.execute("INSERT INTO documents(id,title,content,source_type,content_hash) VALUES('d2','T','x','note','hash2')")
    conn.commit()
    try:
        conn.execute("INSERT INTO claims(id,subject_id,predicate,source_document_id,source_chunk_id,source_start_offset,source_end_offset) VALUES('c1','nope','x','d2','missing',0,1)")
        conn.commit()
        assert False, "foreign key should reject missing subject/chunk"
    except Exception:
        conn.rollback()
    conn.close()
