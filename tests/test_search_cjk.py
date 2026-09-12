"""Chinese search: a query must match a document that contains it.

The original index used the unicode61 tokenizer, which treats a whole run of CJK
characters as a single token. A document titled "苹果的SEO是乔布斯" was therefore
only found by a query repeating that title verbatim — searching "苹果", "SEO" or
"乔布斯" all returned nothing. These tests pin the substring behaviour that the
trigram tokenizer and the query splitter are there to provide.
"""
import importlib
import os
import sys


def _reload_db(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config as config
    import app.db as db
    importlib.reload(config); importlib.reload(db)
    for name in ('app.retrieval', 'app.embeddings'):
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


def _seed(db, doc_id, title, content):
    """Insert a document *and* a chunk: search() expands matches into chunks, so a
    document without chunks yields no results regardless of the index."""
    conn = db.connect()
    conn.execute("INSERT INTO documents(id,title,content,source_type,content_hash)"
                 " VALUES(?,?,?,?,?)", (doc_id, title, content, 'note', f'hash-{doc_id}'))
    conn.execute("INSERT INTO chunks(id,document_id,content,chunk_index,start_offset,end_offset)"
                 " VALUES(?,?,?,?,?,?)", (f'{doc_id}-c0', doc_id, content, 0, 0, len(content)))
    conn.commit(); conn.close()


def _titles(q):
    from app.retrieval import search
    return [r['title'] for r in search(q)]


def test_whole_question_matches_documents_containing_parts(tmp_path):
    db = _reload_db(tmp_path)
    _seed(db, 'd1', '苹果的SEO是乔布斯', '苹果的SEO是乔布斯')
    _seed(db, 'd2', '苹果的SEO是库克', '苹果的SEO是库克')
    _seed(db, 'd3', 'unrelated', 'nothing to see here')

    titles = _titles('苹果的SEO是谁')
    assert '苹果的SEO是乔布斯' in titles
    assert '苹果的SEO是库克' in titles
    assert 'unrelated' not in titles


def test_single_chinese_term_matches(tmp_path):
    """A term appearing inside a longer run is what used to fail completely."""
    db = _reload_db(tmp_path)
    _seed(db, 'd1', '苹果的SEO是乔布斯', '苹果的SEO是乔布斯')
    _seed(db, 'd2', '苹果的SEO是库克', '苹果的SEO是库克')

    assert _titles('乔布斯') == ['苹果的SEO是乔布斯']
    assert _titles('库克') == ['苹果的SEO是库克']
    assert set(_titles('苹果')) == {'苹果的SEO是乔布斯', '苹果的SEO是库克'}


def test_short_query_falls_back_to_like(tmp_path):
    """Two characters is below the trigram minimum, so LIKE must cover it."""
    db = _reload_db(tmp_path)
    _seed(db, 'd1', '苹果的SEO是乔布斯', '苹果的SEO是乔布斯')
    _seed(db, 'd2', 'RAG notes', 'RAG improves answer quality.')

    assert _titles('苹果') == ['苹果的SEO是乔布斯']


def test_latin_search_unchanged(tmp_path):
    db = _reload_db(tmp_path)
    _seed(db, 'd1', 'RAG notes', 'RAG improves answer quality. Fine-tuning is another option.')
    _seed(db, 'd2', 'Other', 'nothing here')

    assert _titles('RAG') == ['RAG notes']


def test_mixed_script_query_splits_at_the_boundary():
    from app.retrieval import _terms
    assert _terms('苹果的SEO是谁') == ['苹果的', 'SEO', '是谁']
    # Latin chunks keep their hyphens; only script boundaries split.
    assert _terms('RAG vs Fine-tuning') == ['RAG', 'vs', 'Fine-tuning']
    assert _terms('苹果的SEO是谁？') == ['苹果的', 'SEO', '是谁']


def test_fts_query_is_empty_for_too_short_input():
    """Callers rely on '' meaning "cannot serve this with FTS, fall back"."""
    from app.retrieval import _fts_query
    assert _fts_query('苹果') == ''
    assert '"苹果的"' in _fts_query('苹果的SEO是谁')
