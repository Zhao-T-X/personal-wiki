from __future__ import annotations
import asyncio
from .chunking import chunk_text
from .db import transaction
from .llm import extract
from .config import runtime
from .extraction import normalize_extraction
from .knowledge import persist_extraction
from .claim_relations import detect_claim_relations
from .integrity import autolink_object_literals
from .ontology import registry_version
from .repositories import (ClaimRepository, DocumentRepository, EventRepository,
                           IdeaRepository, QuestionRepository, RelationRepository,
                           ResearchRepository)


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


async def index_document(document_id: str, *, use_llm=True, experiment_tag: str | None = None):
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
            # L1 object linking shares this transaction, so a literal that exactly
            # matches an entity we already have is attached where its cause is — the
            # extraction that produced it — rather than by a scan at start-up. The
            # ambiguous ones are deliberately left for the suggestion path, and every
            # automatic link is audited like any other knowledge write.
            counts['object_links'] = autolink_object_literals(conn, document_id=document_id)['linked']
            # Record which extractor+ontology produced this chunk's knowledge, so a
            # registry change makes it stale again on the next index run.
            DocumentRepository(conn).set_extraction_version(stale_ids, version)
        # The extraction snapshot: everything needed to replay "what did the model pull
        # out of this document, and what did we keep?" is captured under the run id, so
        # a later experiment can diff Before/After without re-running the model.
        run.summary = {
            **counts,
            'chunks': len(stale_rows),
            'extraction_version': version,
            'experiment_tag': experiment_tag,
            'extraction': result,
        }
        report.update(llm='success', counts=counts, extracted_chunks=len(stale_rows),
                      embedded=await _maybe_embed(document_id))
        return report


def research_document_id(task_id: str) -> str | None:
    """The document holding this task's findings, once it has been recorded.

    One document per task, linked by ``source_uri``. That link is what makes a
    candidate traceable back to the research that proposed it, and what makes "which
    knowledge came from this task?" a lookup instead of a similarity guess.
    """
    return DocumentRepository().by_source_uri(f'research:{task_id}')


def research_candidates(task_id: str, *, limit: int = 6) -> list[dict]:
    """The knowledge this research task has *proposed* and nobody has accepted yet.

    Read from provenance: the pending claims that came out of the task's own findings
    document. So 「这条从哪来」 always has an answer, and a task that proposed nothing
    returns an empty list instead of a pile of loosely related knowledge.
    """
    from .readmodels.knowledge_view import (RESEARCH_CANDIDATE, best_per_statement,
                                            rank_key)

    doc_id = research_document_id(task_id)
    if not doc_id:
        return []
    claims_repo = ClaimRepository()
    claims = claims_repo.candidates_for_document(doc_id)
    if not claims:
        return []
    evidence = {str(c['id']): {'chunk_id': c.get('source_chunk_id'),
                               'document_id': c.get('source_document_id'),
                               'document_title': None} for c in claims}
    views = best_per_statement(claims, evidence=evidence, origin=RESEARCH_CANDIDATE,
                               disputed_ids=claims_repo.disputed_claim_ids(
                                   [str(c['id']) for c in claims]))
    views.sort(key=rank_key)
    return [v.to_dict() for v in views[:limit]]


async def propose_research_candidates(task_id: str) -> dict:
    """Record a task's findings, then let the normal pipeline propose knowledge from them.

    Research produces prose; knowledge is a claim with evidence. The bridge is the one
    every other document takes — the findings become a document, the extraction
    pipeline reads it, the compiler decides what the statements mean — so a research
    candidate is compiled exactly like any other claim and can carry no more than its
    evidence supports. Nothing here writes a claim directly: that would be the one
    place in the product where prose became knowledge without being compiled.

    Candidates land as ``candidate`` claims awaiting a human, and their document
    linkage is what the research page reads back.
    """
    research = ResearchRepository()
    task = research.get(task_id)
    if not task:
        raise ValueError('Research task not found')
    findings = str(task.get('findings') or '').strip()
    if not findings:
        # Findings are the only source a candidate may come from. Proposing from
        # nothing would be inventing knowledge, not compiling it.
        raise ValueError('Research has no findings yet')

    documents = DocumentRepository()
    title = f"研究：{str(task['question_text'])[:60]}"
    uri = f'research:{task_id}'
    metadata = {'research_task_id': task_id}
    doc_id = research_document_id(task_id)
    if doc_id:
        documents.update(doc_id, title=title, content=findings, source_type='research',
                         source_uri=uri, metadata=metadata)
    else:
        doc_id = documents.create(title=title, content=findings, source_type='research',
                                  source_uri=uri, metadata=metadata)
    report = await index_document(doc_id, use_llm=True)
    return {'task_id': task_id, 'document_id': doc_id, 'report': report,
            'knowledge': research_candidates(task_id)}


def embed_document(document_id: str):
    from .embeddings import embed_document as _embed
    return _embed(document_id)
