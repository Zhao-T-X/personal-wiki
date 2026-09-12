"""Near-duplicate detection behind GET /api/entities/{id}/duplicates.

The resolver auto-merges at >=0.93 similarity, so this endpoint exists to surface
the pairs that fall just below it — exactly where a human has to decide. These
tests pin the behaviour that makes those pairs visible (legal-suffix stripping,
containment, type filtering) so a future threshold tweak cannot silently hide them.
"""
import sqlite3

from app.resolution import find_similar_entities


def _conn(rows):
    """Minimal entities table: the function only reads id/name/type/types_json/status."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE entities(
        id TEXT PRIMARY KEY, type TEXT, types_json TEXT, name TEXT,
        status TEXT, updated_at TEXT)''')
    for i, (name, etype) in enumerate(rows, 1):
        conn.execute('INSERT INTO entities(id,type,types_json,name,status,updated_at)'
                     ' VALUES(?,?,?,?,?,?)',
                     (f'e{i}', etype, f'["{etype}"]', name, 'candidate', f'2026-01-{i:02d}'))
    return conn


def test_legal_suffixes_do_not_hide_duplicates():
    """Without suffix stripping this pair scores ~0.67 and sits at the threshold edge."""
    conn = _conn([('Apple Inc.', 'Organization'), ('Apple Incorporated', 'Organization')])
    hits = find_similar_entities(conn, 'e1')
    assert [h['name'] for h in hits] == ['Apple Incorporated']
    assert hits[0]['similarity'] == 1.0


def test_containment_catches_shorter_name():
    conn = _conn([('Apple Inc.', 'Organization'), ('Apple Computer', 'Organization')])
    assert [h['name'] for h in find_similar_entities(conn, 'e1')] == ['Apple Computer']


def test_unrelated_names_are_not_reported():
    conn = _conn([('Apple Inc.', 'Organization'), ('Microsoft', 'Organization'),
                  ('Vision Pro', 'Organization')])
    assert find_similar_entities(conn, 'e1') == []


def test_different_entity_type_is_filtered_out():
    """Mirrors resolution: entities of unrelated types are never merge candidates."""
    conn = _conn([('Apple Inc.', 'Organization'), ('Apple', 'Technology')])
    assert find_similar_entities(conn, 'e1') == []


def test_results_are_sorted_and_capped():
    conn = _conn([('Apple Inc.', 'Organization'), ('Apple Computer', 'Organization'),
                  ('Apple Incorporated', 'Organization'), ('Apples', 'Organization'),
                  ('Microsoft', 'Organization')])
    hits = find_similar_entities(conn, 'e1', limit=2)
    assert len(hits) == 2
    assert hits[0]['similarity'] >= hits[1]['similarity']


def test_detection_is_symmetric():
    conn = _conn([('Apple Inc.', 'Organization'), ('Apple Computer', 'Organization')])
    assert [h['name'] for h in find_similar_entities(conn, 'e2')] == ['Apple Inc.']


def test_unknown_entity_returns_empty():
    conn = _conn([('Apple Inc.', 'Organization')])
    assert find_similar_entities(conn, 'missing') == []
