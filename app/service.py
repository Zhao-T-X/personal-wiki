from __future__ import annotations
import asyncio, hashlib, uuid
from .chunking import chunk_text
from .db import transaction, dumps
from .llm import extract
from .config import runtime
from .extraction import normalize_extraction
from .knowledge import persist_extraction
from .claim_relations import detect_claim_relations


def create_document(*, title, content, source_type='note', source_uri=None, metadata=None):
    doc_id = str(uuid.uuid4())
    content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
    with transaction() as conn:
        conn.execute('''INSERT INTO documents(id,title,content,source_type,source_uri,content_hash,metadata_json)
                        VALUES(?,?,?,?,?,?,?)''',
                     (doc_id,title,content,source_type,source_uri,content_hash,dumps(metadata or {})))
    return doc_id


def write_chunks(document_id: str, text: str):
    chunks = chunk_text(text)
    with transaction() as conn:
        conn.execute('DELETE FROM chunk_embeddings WHERE chunk_id IN (SELECT id FROM chunks WHERE document_id=?)', (document_id,))
        conn.execute('DELETE FROM chunks WHERE document_id=?', (document_id,))
        rows=[]
        for c in chunks:
            cid=str(uuid.uuid4())
            conn.execute('''INSERT INTO chunks(id,document_id,content,chunk_index,start_offset,end_offset)
                            VALUES(?,?,?,?,?,?)''',(cid,document_id,c.content,c.index,c.start_offset,c.end_offset))
            rows.append({'id':cid,'content':c.content,'chunk_index':c.index,'start_offset':c.start_offset,'end_offset':c.end_offset})
    return rows


# Tables that point at a document *without* ON DELETE CASCADE. Deleting the
# document row while any of these still reference it fails the foreign-key check,
# so they must be cleared first (chunks / embeddings / llm_runs cascade already).
_DERIVED_TABLES = ('claims','relations','events','ideas','questions')


def clear_derived(document_id: str):
    with transaction() as conn:
        for table in _DERIVED_TABLES:
            conn.execute(f'DELETE FROM {table} WHERE source_document_id=?',(document_id,))


def delete_document(document_id: str) -> bool:
    """Delete a document and everything derived from it. False if it does not exist.

    Runs in one transaction: a failure cannot leave the document half-removed, and
    the connection is always closed, so it can never leak a write lock onto
    unrelated requests (which surfaced as "database is locked").
    """
    with transaction() as conn:
        if not conn.execute('SELECT 1 FROM documents WHERE id=?', (document_id,)).fetchone():
            return False
        for table in _DERIVED_TABLES:
            conn.execute(f'DELETE FROM {table} WHERE source_document_id=?', (document_id,))
        conn.execute('DELETE FROM documents WHERE id=?', (document_id,))
    return True


async def index_document(document_id: str, *, use_llm=True):
    from .db import connect
    from .runlog import record_run
    conn=connect(); doc=conn.execute('SELECT * FROM documents WHERE id=?',(document_id,)).fetchone(); conn.close()
    if not doc: raise KeyError(document_id)
    clear_derived(document_id)
    chunks=write_chunks(document_id, doc['content'])
    if not use_llm:
        embedded=False
        if runtime()['auto_embed']:
            try:
                await asyncio.to_thread(embed_document, document_id)
                embedded=True
            except RuntimeError:
                pass
        return {'document_id':document_id,'chunks':len(chunks),'llm':'skipped','embedded':embedded}
    async with record_run('extract', document_id=document_id, agent_role='extractor') as run:
        result=normalize_extraction(await extract(chunks))
        with transaction() as conn:
            counts=persist_extraction(conn,document_id=document_id,extraction=result)
            # Relationship detection shares this transaction so a claim can never be
            # committed without the relation explaining how it fits what we knew.
            counts['claim_relations']=detect_claim_relations(conn,document_id=document_id)
        run.summary={**counts,'chunks':len(chunks)}
        embedded=False
        if runtime()['auto_embed']:
            try:
                await asyncio.to_thread(embed_document, document_id)
                embedded=True
            except RuntimeError:
                pass
        return {'document_id':document_id,'chunks':len(chunks),'llm':'success','counts':counts,'embedded':embedded}


def embed_document(document_id: str):
    from .embeddings import embed_document as _embed
    return _embed(document_id)
