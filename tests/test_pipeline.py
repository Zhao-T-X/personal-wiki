import os


def _reload_db(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'test.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import importlib
    import sys
    import app.config as config
    import app.db as db
    importlib.reload(config); importlib.reload(db)
    # Modules that bound the previous db helpers at import time must be
    # reloaded too, otherwise they keep writing to an older database.
    for name in _DB_BOUND_MODULES:
        module = sys.modules.get(name)
        if module is not None:
            importlib.reload(module)
    db.init_db()
    return db


_DB_BOUND_MODULES = (
    'app.service', 'app.knowledge', 'app.resolution',
    'app.retrieval', 'app.graph', 'app.importer', 'app.embeddings',
)


def test_persist_extraction_with_provenance(tmp_path):
    db = _reload_db(tmp_path)
    from app.service import create_document
    from app.chunking import chunk_text
    from app.knowledge import persist_extraction

    doc_id = create_document(title='T', content='RAG helps with changing knowledge.', source_type='note', source_uri=None, metadata={})
    conn = db.connect()
    c = chunk_text('RAG helps with changing knowledge.')[0]
    cid='chunk-1'
    conn.execute('INSERT INTO chunks(id,document_id,content,chunk_index,start_offset,end_offset) VALUES(?,?,?,?,?,?)', (cid,doc_id,c.content,0,c.start_offset,c.end_offset))
    extraction={
      'entities':[{'name':'RAG','types':['Concept','Technology'],'aliases':['Retrieval-Augmented Generation'],'description':'retrieval augmented generation'}],
      'claims':[{'subject':'RAG','predicate':'supports','object':'changing knowledge','content':'RAG supports changing knowledge.','claim_type':'factual','polarity':'positive','modality':'asserted','context':{},'confidence':0.9,'source_chunk':cid,'evidence_quote':'RAG helps with changing knowledge.'}],
      'events':[], 'ideas':[], 'questions':[]
    }
    counts=persist_extraction(conn,document_id=doc_id,extraction=extraction)
    conn.commit()
    row=conn.execute('SELECT source_document_id,source_chunk_id,source_start_offset,source_end_offset FROM claims').fetchone()
    conn.close()
    assert counts['claims']==1
    assert row[0]==doc_id and row[1]==cid
    assert row[2]==0 and row[3]==len(c.content)


def test_persist_auto_creates_unresolved_claim_subject(tmp_path):
    db = _reload_db(tmp_path)
    from app.service import create_document
    from app.chunking import chunk_text
    from app.knowledge import persist_extraction

    doc_id = create_document(title='T', content='queryOrderPaging returns paginated orders.', source_type='note', source_uri=None, metadata={})
    conn = db.connect()
    c = chunk_text('queryOrderPaging returns paginated orders.')[0]
    cid='chunk-1'
    conn.execute('INSERT INTO chunks(id,document_id,content,chunk_index,start_offset,end_offset) VALUES(?,?,?,?,?,?)', (cid,doc_id,c.content,0,c.start_offset,c.end_offset))
    extraction={
      'entities':[],
      'claims':[{'subject':'queryOrderPaging','predicate':'returns','object':'paginated orders','claim_type':'factual','polarity':'positive','modality':'asserted','context':{},'confidence':0.9,'source_chunk':cid,'evidence_quote':'queryOrderPaging returns paginated orders.'}],
      'events':[], 'ideas':[], 'questions':[]
    }
    counts=persist_extraction(conn,document_id=doc_id,extraction=extraction)
    conn.commit()
    entity=conn.execute('SELECT status,type,name FROM entities').fetchone()
    conn.close()
    assert counts['claims']==1
    assert counts['auto_created_subjects']==1
    assert entity['status']=='candidate' and entity['type']=='Resource' and entity['name']=='queryOrderPaging'


def test_persist_tolerates_imprecise_evidence_quotes(tmp_path):
    db = _reload_db(tmp_path)
    from app.service import create_document
    from app.chunking import chunk_text
    from app.knowledge import persist_extraction

    content = 'GOMS查询接口：订单分页查询支持按时间过滤。'
    doc_id = create_document(title='Q', content=content, source_type='note', source_uri=None, metadata={})
    conn = db.connect()
    c = chunk_text(content)[0]
    cid='chunk-1'
    conn.execute('INSERT INTO chunks(id,document_id,content,chunk_index,start_offset,end_offset) VALUES(?,?,?,?,?,?)', (cid,doc_id,c.content,0,c.start_offset,c.end_offset))
    extraction={
      'entities':[{'name':'GOMS','types':['Technology']}],
      'claims':[
        # Full-width vs half-width punctuation variant → unicode-normalized match.
        {'subject':'GOMS','predicate':'defined_as','object':'全球订单管理系统','claim_type':'definitional','polarity':'positive','modality':'asserted','context':{},'confidence':0.9,'source_chunk':cid,'evidence_quote':'GOMS查询接口:订单分页查询支持按时间过滤.'},
        # Paraphrased quote, not present in any form → chunk-level fallback.
        {'subject':'GOMS','predicate':'provides','object':'订单分页查询','claim_type':'factual','polarity':'positive','modality':'asserted','context':{},'confidence':0.8,'source_chunk':cid,'evidence_quote':'订单分页查询可以按时间区间过滤数据。'}
      ],
      'events':[],'ideas':[],'questions':[]
    }
    counts=persist_extraction(conn,document_id=doc_id,extraction=extraction)
    conn.commit()
    rows=conn.execute('SELECT source_quote FROM claims ORDER BY source_start_offset').fetchall()
    conn.close()
    assert counts['claims']==2
    assert counts['imprecise_quotes']==1
    assert len(rows)==2
