from __future__ import annotations
import asyncio, hashlib, uuid
from .chunking import chunk_text
from .db import transaction, dumps
from .llm import extract
from .config import runtime
from .extraction import normalize_extraction
from .knowledge import persist_extraction


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


def clear_derived(document_id: str):
    with transaction() as conn:
        for table in ('claims','relations','events','ideas','questions'):
            conn.execute(f'DELETE FROM {table} WHERE source_document_id=?',(document_id,))


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
