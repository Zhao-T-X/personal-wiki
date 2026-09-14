from __future__ import annotations
import asyncio
from .chunking import chunk_text
from .db import transaction
from .llm import extract
from .config import runtime
from .extraction import normalize_extraction
from .knowledge import persist_extraction
from .claim_relations import detect_claim_relations
from .ontology import registry_version
from .repositories import (ClaimRepository, DocumentRepository, EventRepository,
                           IdeaRepository, QuestionRepository, RelationRepository)


def create_document(*, title, content, source_type='note', source_uri=None, metadata=None):
    return DocumentRepository().create(title=title, content=content, source_type=source_type,
                                       source_uri=source_uri, metadata=metadata)


def write_chunks(document_id: str, text: str):
    chunks = chunk_text(text)
    return DocumentRepository().replace_chunks(
        document_id, [(c.content, c.index, c.start_offset, c.end_offset) for c in chunks])


# Everything derived from a document. Deleting the document row while any of these
# still reference it fails the foreign-key check, so they must be cleared first
# (chunks / embeddings / llm_runs cascade already).
_DERIVED_DELETES = (ClaimRepository, RelationRepository, EventRepository,
                    IdeaRepository, QuestionRepository)


def clear_derived(document_id: str) -> None:
    with transaction() as conn:
        for repo in _DERIVED_DELETES:
            repo(conn).delete_for_document(document_id)


def delete_document(document_id: str) -> bool:
    """Delete a document and everything derived from it, in one transaction.

    A failure cannot leave the document half-removed, and the connection is
    always closed, so it can never leak a write lock onto unrelated requests.
    Returns False if the document does not exist.
    """
    with transaction() as conn:
        documents = DocumentRepository(conn)
        if not documents.exists(document_id):
            return False
        for repo in _DERIVED_DELETES:
            repo(conn).delete_for_document(document_id)
        documents.delete(document_id)
    return True


async def _maybe_embed(document_id: str) -> bool:
    """Embed the document when auto-embed is on; a missing model is not a failure."""
    if not runtime()['auto_embed']:
        return False
    try:
        await asyncio.to_thread(embed_document, document_id)
        return True
    except RuntimeError:
        return False


async def index_document(document_id: str, *, use_llm=True):
    """Index a document incrementally (task §32).

    Only the chunks whose text changed — or whose knowledge was produced by an
    older extractor/ontology — are re-extracted. Unchanged chunks keep their rows,
    their embeddings and their derived knowledge, and when nothing is stale the
    LLM is not called at all.
    """
    from .runlog import record_run
    doc = DocumentRepository().get(document_id)
    if not doc:
        raise KeyError(document_id)

    targets = [(c.content, c.index, c.start_offset, c.end_offset) for c in chunk_text(doc['content'])]
    version = registry_version()

    # Chunk rows and the knowledge referencing them must move together. sync_chunks
    # deletes the replaced/removed chunk rows, so their claims/relations/events/
    # ideas/questions are cleared first — otherwise the foreign-key check fails and
    # the whole transaction rolls back.
    with transaction() as conn:
        documents = DocumentRepository(conn)
        plan = documents.plan_chunks(document_id, targets)
        if plan['dropped']:
            for repo in _DERIVED_DELETES:
                repo(conn).delete_for_chunks(plan['dropped'])
        sync = documents.sync_chunks(document_id, targets)

        # A chunk whose knowledge was compiled by an older extractor/ontology is
        # stale even though its text did not change: that is what makes an ontology
        # edit re-extract by itself. Only when we are about to re-extract — with
        # use_llm=False the knowledge is kept rather than destroyed.
        stale_set = set(sync['stale'])
        version_stale = [c['id'] for c in sync['chunks']
                         if c['id'] not in stale_set and c['extraction_version'] != version]
        if use_llm and version_stale:
            for repo in _DERIVED_DELETES:
                repo(conn).delete_for_chunks(version_stale)
        stale_ids = [*sync['stale'], *version_stale]
        chunks_by_id = {c['id']: c for c in sync['chunks']}

    report = {'document_id': document_id, 'chunks': len(sync['chunks']),
              'reused_chunks': len(sync['unchanged']), 'stale_chunks': len(stale_ids),
              'extracted_chunks': 0}

    if not use_llm:
        report.update(llm='skipped', embedded=await _maybe_embed(document_id))
        return report

    stale_rows = [chunks_by_id[cid] for cid in stale_ids]
    if not stale_rows:
        # Nothing changed and the extractor/ontology version is unchanged: skip the
        # extraction call entirely. Unchanged chunks keep their existing embeddings.
        report.update(llm='skipped', reason='unchanged', embedded=False)
        return report

    async with record_run('extract', document_id=document_id, agent_role='extractor') as run:
        result = normalize_extraction(await extract(stale_rows))
        with transaction() as conn:
            counts = persist_extraction(conn, document_id=document_id, extraction=result)
            # Relationship detection shares this transaction so a claim can never be
            # committed without the relation explaining how it fits what we knew.
            counts['claim_relations'] = detect_claim_relations(conn, document_id=document_id)
            # Record which extractor+ontology produced this chunk's knowledge, so a
            # registry change makes it stale again on the next index run.
            DocumentRepository(conn).set_extraction_version(stale_ids, version)
        run.summary = {**counts, 'chunks': len(stale_rows)}
        report.update(llm='success', counts=counts, extracted_chunks=len(stale_rows),
                      embedded=await _maybe_embed(document_id))
        return report


def embed_document(document_id: str):
    from .embeddings import embed_document as _embed
    return _embed(document_id)
