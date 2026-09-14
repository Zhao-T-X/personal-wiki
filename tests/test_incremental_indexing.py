"""Incremental indexing (task §32): re-index only what changed.

The old ``index_document`` wiped *all* derived knowledge and re-extracted *every*
chunk on every run, so editing one sentence re-ran the whole document. These
tests pin down the new contract:

* an unchanged document is not re-extracted at all (no LLM call) and keeps its
  exact Claim ids;
* only the chunks whose text changed are handed to the extractor, and unchanged
  chunks keep their knowledge;
* a change to the extractor/ontology fingerprint (``registry_version``) makes
  every chunk stale again and re-extracts them;
* shrinking a document prunes the surplus chunks without tripping foreign keys.

No network and no real LLM: ``app.service.extract`` is monkeypatched.
"""
import asyncio
import importlib
import os
import sys

_RELOAD = (
    'app.repositories.base',
    'app.repositories.document_repo',
    'app.repositories.entity_repo',
    'app.repositories.claim_repo',
    'app.repositories.relation_repo',
    'app.repositories.evidence_repo',
    'app.repositories.event_repo',
    'app.repositories.idea_repo',
    'app.repositories.question_repo',
    'app.resolution',
    'app.normalization',
    'app.knowledge',
    'app.claim_relations',
    'app.runlog',
    'app.service',
)


def _reload(tmp_path):
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


def _install_fake_extract(monkeypatch):
    """Record which chunk ids reach the extractor; return a fixed extraction.

    Every chunk yields exactly one claim whose ``source_chunk`` is that chunk and
    whose ``evidence_quote`` is a real substring of its content (so
    ``persist_extraction`` can locate it), which is all the pipeline needs.
    """
    import app.service as service
    calls: list[list[str]] = []

    async def fake_extract(chunks):
        calls.append([c['id'] for c in chunks])
        entities, claims = [], []
        for c in chunks:
            name = f"Subject{c['chunk_index']}"
            quote = c['content'].strip()[:24] or c['content'][:24]
            entities.append({'name': name, 'types': ['Concept'], 'aliases': []})
            claims.append({
                'subject': name,
                'predicate': 'defined_as',
                'object': f"Object{c['chunk_index']}",
                'claim_type': 'factual',
                'polarity': 'positive',
                'modality': 'asserted',
                'context': {},
                'confidence': 0.9,
                'source_chunk': c['id'],
                'evidence_quote': quote,
            })
        return {'entities': entities, 'claims': claims,
                'events': [], 'ideas': [], 'questions': []}

    monkeypatch.setattr(service, 'extract', fake_extract)
    return calls


def _long_text(paragraphs: int = 5) -> str:
    """A document long enough to chunk into several pieces."""
    return '\n\n'.join(
        f'Segment {i}. ' + ' '.join(f'token{i}_{j}' for j in range(120))
        for i in range(paragraphs))


def _chunk_ids(db, doc_id) -> set[str]:
    conn = db.connect()
    ids = {r['id'] for r in conn.execute('SELECT id FROM chunks WHERE document_id=?', (doc_id,))}
    conn.close()
    return ids


def _claims_by_chunk() -> dict[str, str]:
    """chunk id -> its claim id (the fake extractor writes one claim per chunk)."""
    from app.repositories.claim_repo import ClaimRepository
    return {c['source_chunk_id']: c['id'] for c in ClaimRepository().list(500)}


def _update_content(doc_id: str, content: str) -> None:
    from app.repositories.document_repo import DocumentRepository
    DocumentRepository().update(doc_id, title='Long', content=content, source_type='note',
                                source_uri=None, metadata={})


# -- unchanged document ---------------------------------------------------------

def test_reindex_unchanged_document_skips_extraction(tmp_path, monkeypatch):
    db = _reload(tmp_path)
    import app.service as service
    calls = _install_fake_extract(monkeypatch)

    doc_id = service.create_document(title='D', content='Alpha is a concept. Beta is a concept.')
    first = asyncio.run(service.index_document(doc_id, use_llm=True))
    assert first['llm'] == 'success'
    assert first['extracted_chunks'] == first['chunks'] == 1
    assert len(calls) == 1

    chunk_ids_before = _chunk_ids(db, doc_id)
    claims_before = _claims_by_chunk()
    assert claims_before

    second = asyncio.run(service.index_document(doc_id, use_llm=True))

    assert second['llm'] == 'skipped'
    assert second['reason'] == 'unchanged'
    assert second['extracted_chunks'] == 0
    assert second['reused_chunks'] == second['chunks']
    assert len(calls) == 1, 'extract must not be called again for an unchanged document'
    # The core evidence: derived knowledge was not discarded and recreated.
    assert _chunk_ids(db, doc_id) == chunk_ids_before
    assert _claims_by_chunk() == claims_before


# -- only the changed part ------------------------------------------------------

def test_only_changed_chunks_are_re_extracted(tmp_path, monkeypatch):
    db = _reload(tmp_path)
    import app.service as service
    calls = _install_fake_extract(monkeypatch)

    content = _long_text()
    doc_id = service.create_document(title='Long', content=content)
    first = asyncio.run(service.index_document(doc_id, use_llm=True))
    assert first['llm'] == 'success'
    assert first['chunks'] >= 2, 'this test needs a multi-chunk document'
    first_all = _chunk_ids(db, doc_id)
    assert len(calls) == 1 and set(calls[0]) == first_all

    claims_before = _claims_by_chunk()

    # Change only the tail, so the earlier chunks keep byte-identical content.
    _update_content(doc_id, content + '\n\nA brand new closing paragraph.')
    second = asyncio.run(service.index_document(doc_id, use_llm=True))

    assert second['llm'] == 'success'
    assert len(calls) == 2
    second_all = _chunk_ids(db, doc_id)
    reused = first_all & second_all
    re_extracted = set(calls[1])

    assert reused, 'some chunks should be preserved by content hash'
    assert len(re_extracted) < len(second_all)
    assert re_extracted == second_all - reused          # exactly the changed/new chunks
    assert re_extracted.isdisjoint(reused)              # nothing unchanged was re-extracted

    # Unchanged chunks kept their exact claims.
    claims_after = _claims_by_chunk()
    for cid in reused:
        if cid in claims_before:
            assert claims_after.get(cid) == claims_before[cid]


# -- extractor / ontology version bump ------------------------------------------

def test_extractor_version_bump_re_extracts_every_chunk(tmp_path, monkeypatch):
    db = _reload(tmp_path)
    import app.service as service
    calls = _install_fake_extract(monkeypatch)

    doc_id = service.create_document(title='Long', content=_long_text())
    first = asyncio.run(service.index_document(doc_id, use_llm=True))
    assert first['stale_chunks'] == first['chunks']
    first_all = _chunk_ids(db, doc_id)
    claims_before = _claims_by_chunk()

    # A registry / extraction-schema edit changes registry_version()'s fingerprint.
    # index_document reads it through the module global, so it can be patched.
    monkeypatch.setattr(service, 'registry_version', lambda: 'evolved-version')
    calls.clear()

    second = asyncio.run(service.index_document(doc_id, use_llm=True))

    assert second['llm'] == 'success'
    assert len(calls) == 1
    assert set(calls[0]) == first_all          # every chunk re-extracted
    assert second['extracted_chunks'] == len(first_all)
    # The knowledge was rebuilt under the new version.
    assert _claims_by_chunk() != claims_before

    conn = db.connect()
    versions = {r['extraction_version'] for r in
                conn.execute('SELECT extraction_version FROM chunks WHERE document_id=?', (doc_id,))}
    conn.close()
    assert versions == {'evolved-version'}

    # Now that the chunks carry the new version, a further index is a no-op again.
    calls.clear()
    third = asyncio.run(service.index_document(doc_id, use_llm=True))
    assert third['llm'] == 'skipped' and third['reason'] == 'unchanged'
    assert calls == []


# -- shrinking a document -------------------------------------------------------

def test_shrinking_document_prunes_chunks_and_their_knowledge(tmp_path, monkeypatch):
    db = _reload(tmp_path)
    import app.service as service
    calls = _install_fake_extract(monkeypatch)

    doc_id = service.create_document(title='Long', content=_long_text())
    first = asyncio.run(service.index_document(doc_id, use_llm=True))
    assert first['chunks'] >= 2
    old_ids = _chunk_ids(db, doc_id)

    _update_content(doc_id, 'Now this is short.')
    calls.clear()
    second = asyncio.run(service.index_document(doc_id, use_llm=True))

    # No foreign-key error, and the surplus chunks are gone.
    assert second['llm'] == 'success'
    current = _chunk_ids(db, doc_id)
    assert len(current) == 1
    assert old_ids - current, 'expected the surplus chunks to be removed'

    # Nothing derived may point at a chunk that no longer exists.
    conn = db.connect()
    claim_chunks = {r['source_chunk_id'] for r in
                    conn.execute('SELECT source_chunk_id FROM claims WHERE source_document_id=?', (doc_id,))}
    conn.close()
    assert claim_chunks <= current
    assert set(_claims_by_chunk()).issubset(current)


# -- repository contracts (regression) ------------------------------------------

def test_replace_chunks_reuses_unchanged_rows(tmp_path):
    _reload(tmp_path)
    from app.repositories.document_repo import DocumentRepository
    docs = DocumentRepository()
    doc_id = docs.create(title='D', content='a b c')

    first = docs.replace_chunks(doc_id, [('one two', 0, 0, 7), ('three', 1, 8, 13)])
    again = docs.replace_chunks(doc_id, [('one two', 0, 0, 7), ('three', 1, 8, 13)])
    assert [c['id'] for c in again] == [c['id'] for c in first]

    # Only the changed chunk is replaced; the untouched row keeps its id.
    changed = docs.replace_chunks(doc_id, [('one two', 0, 0, 7), ('THREE!', 1, 8, 14)])
    assert [c['chunk_index'] for c in changed] == [0, 1]
    assert changed[0]['id'] == first[0]['id']
    assert changed[1]['id'] != first[1]['id']


def test_delete_for_chunks_is_a_noop_for_empty_input(tmp_path):
    _reload(tmp_path)
    from app.repositories import (ClaimRepository, EventRepository, IdeaRepository,
                                  QuestionRepository, RelationRepository)
    for repo in (ClaimRepository(), RelationRepository(), EventRepository(),
                 IdeaRepository(), QuestionRepository()):
        assert repo.delete_for_chunks([]) == 0
