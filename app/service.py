from __future__ import annotations
import asyncio
from .chunking import chunk_text
from .db import transaction
from .llm import extract
from .config import runtime
from .extraction import normalize_extraction
from .knowledge import persist_extraction
from .claim_relations import detect_claim_relations
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


async def index_document(document_id: str, *, use_llm=True):
    from .runlog import record_run
    doc = DocumentRepository().get(document_id)
    if not doc:
        raise KeyError(document_id)
    clear_derived(document_id)
    chunks = write_chunks(document_id, doc['content'])
    if not use_llm:
        embedded = False
        if runtime()['auto_embed']:
            try:
                await asyncio.to_thread(embed_document, document_id)
                embedded = True
            except RuntimeError:
                pass
        return {'document_id': document_id, 'chunks': len(chunks), 'llm': 'skipped', 'embedded': embedded}
    async with record_run('extract', document_id=document_id, agent_role='extractor') as run:
        result = normalize_extraction(await extract(chunks))
        with transaction() as conn:
            counts = persist_extraction(conn, document_id=document_id, extraction=result)
            # Relationship detection shares this transaction so a claim can never be
            # committed without the relation explaining how it fits what we knew.
            counts['claim_relations'] = detect_claim_relations(conn, document_id=document_id)
        run.summary = {**counts, 'chunks': len(chunks)}
        embedded = False
        if runtime()['auto_embed']:
            try:
                await asyncio.to_thread(embed_document, document_id)
                embedded = True
            except RuntimeError:
                pass
        return {'document_id': document_id, 'chunks': len(chunks), 'llm': 'success',
                'counts': counts, 'embedded': embedded}


def embed_document(document_id: str):
    from .embeddings import embed_document as _embed
    return _embed(document_id)
