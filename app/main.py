from __future__ import annotations
import sqlite3
import traceback
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from . import __version__
from .config import runtime, save_settings
from .db import connect, init_db, loads, dumps
from .models import (AskRequest, DocumentCreate, StatusUpdate, SearchRequest, EntityUpdate,
                     EntityCreate, IdeaCreate, QuestionCreate, EventCreate, ResearchCreate)
from .service import create_document, index_document, embed_document, delete_document
from .retrieval import evidence_pack, format_evidence, search, lexical_search
from .llm import answer
from .ontology import KNOWLEDGE_STATUSES, IDEA_STATUSES, QUESTION_STATUSES
from .importer import SUPPORTED
from .graph import neighborhood
from .resolution import find_similar_entities
from .prompt_profiles import list_profiles, get_profile, update_profile, reset_profile, restore_version

app=FastAPI(title='LLM-Wiki', version=__version__)

@app.on_event('startup')
def startup(): init_db()

@app.get('/api/agent/prompts')
def agent_prompts():
    return {'profiles': list_profiles()}


@app.get('/api/agent/prompts/{role}')
def agent_prompt(role: str):
    try:
        return get_profile(role)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


class AgentPromptUpdateRequest(__import__('pydantic').BaseModel):
    custom_prompt: str = ''
    note: str = ''


@app.put('/api/agent/prompts/{role}')
def update_agent_prompt(role: str, payload: AgentPromptUpdateRequest):
    try:
        return update_profile(role, payload.custom_prompt, payload.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


class AgentPromptRestoreRequest(__import__('pydantic').BaseModel):
    version: int


@app.post('/api/agent/prompts/{role}/restore')
def restore_agent_prompt(role: str, payload: AgentPromptRestoreRequest):
    try:
        return restore_version(role, payload.version)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post('/api/agent/prompts/{role}/reset')
def reset_agent_prompt(role: str):
    try:
        return reset_profile(role)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get('/api/agent/roles')
def agent_roles():
    return {
        'roles': [
            {'id':'personal','name':'PersonalAgent','description':'通用入口与个人助手'},
            {'id':'knowledge','name':'KnowledgeAgent','description':'知识检索、实体、关系与证据'},
            {'id':'research','name':'ResearchAgent','description':'多步研究、比较与综合'},
            {'id':'curator','name':'CuratorAgent','description':'去重、冲突与知识质量'},
            {'id':'review','name':'ReviewAgent','description':'候选知识人工审核辅助'},
            {'id':'extractor','name':'ExtractionAgent','description':'文档知识抽取'},
        ]
    }


@app.get('/api/health')
def health():
    cfg=runtime(); return {'ok':True,'version':__version__,'llm_model':cfg['openai_model'],'embedding_model':cfg['openai_embedding_model'],'llm_configured':bool(cfg['openai_api_key']),'database_path':cfg['database_path']}

@app.get('/api/settings')
def get_settings_api():
    cfg=runtime()
    safe=dict(cfg)
    safe['openai_api_key_configured']=bool(cfg['openai_api_key'])
    safe['openai_api_key']=''
    return safe

@app.put('/api/settings')
def update_settings_api(payload: dict):
    allowed={'database_path','openai_api_key','openai_base_url','openai_model','openai_embedding_model','embedding_dims','llm_batch_chunks','max_search_results','auto_embed','agentscope_enabled','agent_context_budgets'}
    updates={k:v for k,v in payload.items() if k in allowed}
    if 'database_path' in updates and not str(updates['database_path']).strip():
        raise HTTPException(422,'database_path cannot be empty')
    if 'openai_api_key' in updates and updates['openai_api_key']=='' and runtime()['openai_api_key']:
        # Empty means keep the existing key when the UI leaves the password field blank.
        updates.pop('openai_api_key')
    cfg=save_settings(updates)
    # Initialize the selected database immediately so switching DB is usable without restart.
    init_db()
    safe=dict(cfg); safe['openai_api_key_configured']=bool(cfg['openai_api_key']); safe['openai_api_key']=''
    return safe

class AgentAskRequest(__import__('pydantic').BaseModel):
    message: str
    role: str = 'auto'
    # Optional multi-turn handle (P3): when present, the conversation's
    # compressed state is rolled forward and persisted in conversation_summaries.
    conversation_id: str = ''


@app.post('/api/agent/ask')
async def agent_ask(req: AgentAskRequest):
    cfg = runtime()
    if not cfg.get('agentscope_enabled', True):
        raise HTTPException(503, 'AgentScope is disabled in settings')
    try:
        from .workflows.agent_workflow import run_agent
        result = await run_agent(req.message, req.role, req.conversation_id)
        result['framework'] = 'AgentScope 2.x'
        return result
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:
        # Always return JSON so the web UI never fails with
        # "Unexpected token I ... is not valid JSON".
        detail = f'{type(exc).__name__}: {exc}'
        print('AgentScope error:', detail)
        traceback.print_exc()
        raise HTTPException(502, f'AgentScope request failed: {detail}') from exc


@app.post('/api/documents')
def create_doc(data: DocumentCreate):
    try: did=create_document(title=data.title,content=data.content,source_type=data.source_type,source_uri=data.source_uri,metadata=data.metadata)
    except sqlite3.IntegrityError as exc: raise HTTPException(409,'A document with the same content already exists') from exc
    return {'id':did,'title':data.title}

@app.post('/api/documents/import')
async def import_file(file: UploadFile=File(...)):
    name=file.filename or 'import.txt'; suffix='.'+name.rsplit('.',1)[-1].lower() if '.' in name else ''
    if suffix not in SUPPORTED: raise HTTPException(415,f'Unsupported file type: {suffix}')
    raw=await file.read(); text=raw.decode('utf-8',errors='replace')
    return create_doc(DocumentCreate(title=name,content=text,source_type=SUPPORTED[suffix]))

@app.get('/api/documents')
def list_docs(limit:int=100):
    limit=max(1,min(limit,500))
    conn=connect()
    rows=conn.execute('''SELECT d.id,d.title,d.source_type,d.source_uri,d.created_at,d.updated_at,
        (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) chunk_count,
        (SELECT status FROM llm_runs r WHERE r.document_id=d.id ORDER BY r.created_at DESC LIMIT 1) last_run_status,
        (SELECT created_at FROM llm_runs r WHERE r.document_id=d.id ORDER BY r.created_at DESC LIMIT 1) last_run_at
        FROM documents d ORDER BY d.updated_at DESC LIMIT ?''',(limit,)).fetchall()
    total=conn.execute('SELECT COUNT(*) c FROM documents').fetchone()['c']; conn.close()
    return [dict(r)|{'total':total} for r in rows]

@app.get('/api/events')
def events_list(limit:int=100,offset:int=0,status:str|None=None):
    sql='SELECT id,event_type,description,participants_json,time_json,location,status,confidence,source_document_id,source_quote,created_at FROM events'; params=[]
    if status: sql+=' WHERE status=?'; params.append(status)
    sql+=' ORDER BY created_at DESC LIMIT ? OFFSET ?'; params.extend([max(1,min(limit,200)),max(0,offset)])
    conn=connect(); rows=conn.execute(sql,params).fetchall(); conn.close()
    return [dict(r)|{'participants':loads(r['participants_json'],[]),'time':loads(r['time_json'],{})} for r in rows]

@app.get('/api/ideas')
def ideas_list(limit:int=100,offset:int=0,status:str|None=None):
    sql='SELECT id,content,status,confidence,source_document_id,source_quote,created_at FROM ideas'; params=[]
    if status: sql+=' WHERE status=?'; params.append(status)
    sql+=' ORDER BY created_at DESC LIMIT ? OFFSET ?'; params.extend([max(1,min(limit,200)),max(0,offset)])
    conn=connect(); rows=conn.execute(sql,params).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.get('/api/questions')
def questions_list(limit:int=100,offset:int=0,status:str|None=None):
    sql='SELECT id,content,status,source_document_id,source_quote,created_at FROM questions'; params=[]
    if status: sql+=' WHERE status=?'; params.append(status)
    sql+=' ORDER BY created_at DESC LIMIT ? OFFSET ?'; params.extend([max(1,min(limit,200)),max(0,offset)])
    conn=connect(); rows=conn.execute(sql,params).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.get('/api/stats/timeseries')
def stats_timeseries(days:int=14):
    days=max(1,min(days,90))
    conn=connect()
    def series(table):
        rows=conn.execute(f"SELECT date(created_at) d,COUNT(*) c FROM {table} WHERE created_at>=date('now',?) GROUP BY date(created_at)",(f'-{days} days',)).fetchall()
        return {r['d']:r['c'] for r in rows}
    docs,ents,clms=series('documents'),series('entities'),series('claims'); conn.close()
    out=[]; from datetime import date,timedelta
    for i in range(days-1,-1,-1):
        d=str(date.today()-timedelta(days=i))
        out.append({'date':d,'documents':docs.get(d,0),'entities':ents.get(d,0),'claims':clms.get(d,0)})
    return out

@app.get('/api/database/integrity')
def database_integrity():
    conn=connect()
    integrity=conn.execute('PRAGMA integrity_check').fetchone()[0]
    page_count=conn.execute('PRAGMA page_count').fetchone()[0]
    page_size=conn.execute('PRAGMA page_size').fetchone()[0]
    conn.close()
    return {'integrity':integrity,'page_count':page_count,'size_mb':round(page_count*page_size/1048576,1)}

@app.get('/api/claims/{claim_id}')
def get_claim(claim_id:str):
    conn=connect()
    row=conn.execute('''SELECT c.*,s.name subject_name,o.name object_name FROM claims c
        JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id WHERE c.id=?''',(claim_id,)).fetchone()
    if not row: conn.close(); raise HTTPException(404,'Claim not found')
    conn.close(); return dict(row)|{'context':loads(row['context_json'],{})}

@app.get('/api/claims/{claim_id}/relations')
def claim_relations_for(claim_id:str):
    """How this claim relates to other claims (both directions).

    Claims are never overwritten, so this is the only place knowledge evolution is
    expressed — including whether a newer claim supersedes this one.
    """
    conn=connect()
    rows=conn.execute('''SELECT r.id,r.relationship,r.confidence,r.reason,r.suggested_action,r.status,r.created_at,
                                r.source_claim_id,r.target_claim_id,
                                c.id other_id,c.predicate,c.content,c.object_text,
                                c.confidence other_confidence,c.status other_status,c.created_at other_created_at,
                                c.source_document_id,c.source_quote,
                                s.name subject_name,o.name object_name,d.title document_title
                         FROM claim_relations r
                         JOIN claims c ON c.id = CASE WHEN r.source_claim_id=? THEN r.target_claim_id ELSE r.source_claim_id END
                         JOIN entities s ON s.id=c.subject_id
                         LEFT JOIN entities o ON o.id=c.object_id
                         LEFT JOIN documents d ON d.id=c.source_document_id
                         WHERE r.source_claim_id=? OR r.target_claim_id=?
                         ORDER BY r.created_at DESC''',(claim_id,claim_id,claim_id)).fetchall()
    conn.close()
    return {'claim_id':claim_id,'relations':[dict(r) for r in rows]}

@app.get('/api/claim-relations')
def claim_relations_queue(status:str|None=None,limit:int=100):
    """Claim-to-claim relationships awaiting a decision, newest first."""
    limit=max(1,min(limit,500))
    sql='''SELECT r.id,r.relationship,r.confidence,r.reason,r.suggested_action,r.status,r.created_by,r.created_at,
                  ns.id new_id,ns.predicate new_predicate,ns.content new_content,ns.object_text new_object_text,
                  ns.confidence new_confidence,ns.status new_status,ns.source_quote new_quote,
                  ns.source_document_id new_document_id,nsu.name new_subject,nou.name new_object,
                  nd.title new_document_title,
                  os.id old_id,os.predicate old_predicate,os.content old_content,os.object_text old_object_text,
                  os.confidence old_confidence,os.status old_status,os.source_quote old_quote,
                  os.source_document_id old_document_id,osu.name old_subject,oou.name old_object,
                  od.title old_document_title
           FROM claim_relations r
           JOIN claims ns ON ns.id=r.source_claim_id
           JOIN entities nsu ON nsu.id=ns.subject_id
           LEFT JOIN entities nou ON nou.id=ns.object_id
           LEFT JOIN documents nd ON nd.id=ns.source_document_id
           JOIN claims os ON os.id=r.target_claim_id
           JOIN entities osu ON osu.id=os.subject_id
           LEFT JOIN entities oou ON oou.id=os.object_id
           LEFT JOIN documents od ON od.id=os.source_document_id'''
    params=[]
    if status: sql+=' WHERE r.status=?'; params.append(status)
    sql+=' ORDER BY r.created_at DESC LIMIT ?'; params.append(limit)
    conn=connect(); rows=conn.execute(sql,params).fetchall(); conn.close()
    return [dict(r) for r in rows]

@app.post('/api/claim-relations/{relation_id}/analyze')
async def analyze_claim_relation(relation_id:str):
    """Ask the model how two claims relate — a suggestion the user may accept.

    Deterministic detection runs at import time. This is the on-demand escape hatch
    for cases structure cannot read (differently-worded statements, evidence that
    states a replacement). It writes nothing by itself.
    """
    conn=connect()
    rel=conn.execute('SELECT * FROM claim_relations WHERE id=?',(relation_id,)).fetchone()
    if not rel:
        conn.close(); raise HTTPException(404,'Claim relation not found')
    rel=dict(rel)

    def _brief(claim_id):
        row=conn.execute('''SELECT c.predicate,c.content,c.object_text,c.source_quote,
                                   s.name subject_name,o.name object_name
                            FROM claims c JOIN entities s ON s.id=c.subject_id
                            LEFT JOIN entities o ON o.id=c.object_id WHERE c.id=?''',(claim_id,)).fetchone()
        return dict(row) if row else {}
    new_claim=_brief(rel['source_claim_id']); old_claim=_brief(rel['target_claim_id'])
    conn.close()

    from .claim_relations import analyze_relation
    verdict=await analyze_relation(new_claim,old_claim)
    if verdict is None:
        raise HTTPException(503,'模型不可用或未配置，无法进行语义判断')
    return verdict

@app.patch('/api/claim-relations/{relation_id}')
def update_claim_relation(relation_id:str,payload:dict):
    """Resolve a suggested knowledge change.

    `relationship='supersedes'` + `status='accepted'` is the only path that makes an
    older claim stop being current. It moves that claim's *lifecycle status* — its
    text, object, evidence and provenance are never rewritten — and records the
    previous status so the decision can be undone exactly rather than guessed at.
    """
    from .claim_relations import RELATIONSHIPS
    body=payload or {}
    new_status=body.get('status')
    if new_status not in ('accepted','rejected','candidate'):
        raise HTTPException(422,'status must be accepted, rejected or candidate')
    relationship=body.get('relationship')
    if relationship is not None and relationship not in RELATIONSHIPS:
        raise HTTPException(422,f'Unknown relationship: {relationship}')
    conn=connect()
    row=conn.execute('SELECT * FROM claim_relations WHERE id=?',(relation_id,)).fetchone()
    if not row:
        conn.close(); raise HTTPException(404,'Claim relation not found')
    rel=dict(row)
    try:
        if relationship is not None:
            conn.execute('UPDATE claim_relations SET status=?,relationship=? WHERE id=?',
                         (new_status,relationship,relation_id))
        else:
            conn.execute('UPDATE claim_relations SET status=? WHERE id=?',(new_status,relation_id))

        if relationship=='supersedes' and new_status=='accepted':
            older=conn.execute('SELECT status FROM claims WHERE id=?',(rel['target_claim_id'],)).fetchone()
            conn.execute('UPDATE claim_relations SET target_previous_status=? WHERE id=?',
                         (older['status'] if older else None,relation_id))
            conn.execute("UPDATE claims SET status='superseded' WHERE id=?",(rel['target_claim_id'],))
        elif rel.get('target_previous_status'):
            # Any retreat from a confirmed supersession restores the older claim.
            conn.execute('UPDATE claims SET status=? WHERE id=? AND status=?',
                         (rel['target_previous_status'],rel['target_claim_id'],'superseded'))
        conn.commit()
    finally:
        conn.close()
    return {'id':relation_id,'status':new_status,'relationship':relationship}

@app.get('/api/entities/{entity_id}/object')
def entity_object(entity_id:str):
    conn=connect()
    e=conn.execute('SELECT * FROM entities WHERE id=?',(entity_id,)).fetchone()
    if not e: conn.close(); raise HTTPException(404,'Entity not found')
    claims=[dict(r) for r in conn.execute('''SELECT c.*,s.name subject_name,o.name object_name FROM claims c
        JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id
        WHERE c.subject_id=? OR c.object_id=? ORDER BY c.created_at DESC''',(entity_id,entity_id)).fetchall()]
    relations=[dict(r) for r in conn.execute('''SELECT r.*,a.name source_name,b.name target_name FROM relations r
        JOIN entities a ON a.id=r.source_id JOIN entities b ON b.id=r.target_id
        WHERE r.source_id=? OR r.target_id=? ORDER BY r.created_at DESC''',(entity_id,entity_id)).fetchall()]
    docs=list({c['source_document_id'] for c in claims})
    events,ideas,questions=[],[],[]
    if docs:
        q=','.join('?'*len(docs)); args=tuple(docs)
        events=[dict(r) for r in conn.execute(f'SELECT * FROM events WHERE source_document_id IN ({q}) ORDER BY created_at DESC LIMIT 50',args).fetchall()]
        ideas=[dict(r) for r in conn.execute(f'SELECT * FROM ideas WHERE source_document_id IN ({q}) ORDER BY created_at DESC LIMIT 50',args).fetchall()]
        questions=[dict(r) for r in conn.execute(f'SELECT id,content,status,source_document_id,source_quote,created_at FROM questions WHERE source_document_id IN ({q}) ORDER BY created_at DESC LIMIT 50',args).fetchall()]
    conn.close()
    evidence=[c for c in claims if c['source_quote']]
    type_decision=[]
    for c in claims:
        if c['subject_id']==entity_id and c['predicate']=='defined_as' and c['object_text']:
            type_decision.append({'type':c['object_text'][:60],'reason':f"来源明确定义：{(c['source_quote'] or '')[:90]}",'ok':c['polarity']=='positive'})
    counts={'claims':len(claims),'relations':len(relations),'events':len(events),'ideas':len(ideas),
            'questions':len(questions),'evidence':len(evidence),'documents':len(docs)}
    return {'entity':dict(e)|{'aliases':loads(e['aliases_json'],[]),'properties':loads(e['properties_json'],{})},
            'counts':counts,'claims':claims,'relations':relations,'evidence':evidence,
            'events':events,'ideas':ideas,'questions':questions,'type_decision':type_decision}

@app.get('/api/conflicts')
def conflicts_list(limit:int=50):
    conn=connect()
    rows=conn.execute('''SELECT c.id,s.name subject_name,c.predicate,c.polarity,c.content,c.object_text,c.status,
        c.source_document_id,c.source_quote,c.modality,c.confidence,c.created_at
        FROM claims c JOIN entities s ON s.id=c.subject_id ORDER BY c.subject_id,c.predicate''').fetchall()
    conn.close()
    groups={}
    for r in rows:
        groups.setdefault((r['subject_name'],r['predicate']),[]).append(dict(r))
    out=[]
    for (subj,pred),cs in groups.items():
        if len(cs)>=2 and len({c['polarity'] for c in cs})>1:
            out.append({'subject_name':subj,'predicate':pred,'claims':cs})
            if len(out)>=max(1,min(limit,200)): break
    return out

@app.get('/api/knowledge/health')
def knowledge_health():
    conn=connect()
    def count(sql, *args): return conn.execute(sql, args).fetchone()['c']
    total_claims=count('SELECT COUNT(*) c FROM claims')
    verified_claims=count("SELECT COUNT(*) c FROM claims WHERE status='verified'")
    total_entities=count('SELECT COUNT(*) c FROM entities')
    verified_entities=count("SELECT COUNT(*) c FROM entities WHERE status='verified'")
    object_text_claims=count('SELECT COUNT(*) c FROM claims WHERE object_id IS NULL')
    relations=count('SELECT COUNT(*) c FROM relations')
    open_q=count("SELECT COUNT(*) c FROM questions WHERE status='open'")
    stale=count("SELECT COUNT(*) c FROM entities WHERE status='candidate' AND updated_at<datetime('now','-90 days')")
    conn.close()
    return {'total_claims':total_claims,'verified_claims':verified_claims,
            'verified_claim_ratio':round(verified_claims/max(1,total_claims)*100,1),
            'total_entities':total_entities,'verified_entities':verified_entities,
            'verified_entity_ratio':round(verified_entities/max(1,total_entities)*100,1),
            'object_text_claims':object_text_claims,'relations':relations,
            'open_questions':open_q,'stale_candidates':stale}

@app.get('/api/skills')
def skills_list():
    from pathlib import Path
    root=Path('skills'); out=[]
    if root.exists():
        for p in sorted(root.rglob('*.md')):
            head=''
            for line in p.read_text(encoding='utf-8',errors='replace').splitlines():
                if line.startswith('# '): head=line[2:].strip(); break
            out.append({'name':p.stem,'purpose':head})
    return out

@app.get('/api/skills/{name}')
def skill_detail(name:str):
    from pathlib import Path
    for p in sorted(Path('skills').rglob(name+'.md')):
        return {'name':p.stem,'content':p.read_text(encoding='utf-8',errors='replace')}
    raise HTTPException(404,'Skill not found')

@app.get('/api/documents/{doc_id}')
def get_doc(doc_id:str):
    conn=connect(); row=conn.execute('SELECT * FROM documents WHERE id=?',(doc_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404,'Document not found')
    item=dict(row); item['metadata']=loads(item.pop('metadata_json'),{}); return item

@app.post('/api/documents/{doc_id}/index')
async def index_doc(doc_id:str):
    try: return await index_document(doc_id,use_llm=True)
    except KeyError as exc: raise HTTPException(404,'Document not found') from exc
    except RuntimeError as exc: raise HTTPException(503,str(exc)) from exc
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc

@app.post('/api/documents/{doc_id}/index/local')
async def index_local(doc_id:str):
    try: return await index_document(doc_id,use_llm=False)
    except KeyError as exc: raise HTTPException(404,'Document not found') from exc

@app.post('/api/documents/{doc_id}/embed')
def embed_doc(doc_id:str):
    try: return embed_document(doc_id)
    except KeyError as exc: raise HTTPException(404,'Document not found') from exc
    except RuntimeError as exc: raise HTTPException(503,str(exc)) from exc

@app.post('/api/embeddings/backfill')
def embeddings_backfill():
    from .embeddings import backfill_embeddings
    try: return backfill_embeddings()
    except RuntimeError as exc: raise HTTPException(503,str(exc)) from exc

@app.get('/api/search')
def api_search(q:str,limit:int=10,semantic:bool=True):
    if not q.strip(): return []
    return search(q,max(1,min(limit,50)),semantic=semantic)

@app.post('/api/search')
def post_search(req:SearchRequest): return search(req.query,req.limit,req.semantic)

@app.get('/api/documents/{doc_id}/chunks')
def document_chunks(doc_id: str):
    conn=connect(); rows=conn.execute('SELECT id,document_id,content,chunk_index,start_offset,end_offset,created_at FROM chunks WHERE document_id=? ORDER BY chunk_index',(doc_id,)).fetchall(); conn.close()
    if not rows:
        # Distinguish a valid empty/unindexed doc from missing doc.
        conn=connect(); exists=conn.execute('SELECT 1 FROM documents WHERE id=?',(doc_id,)).fetchone(); conn.close()
        if not exists: raise HTTPException(404,'Document not found')
    return [dict(r) for r in rows]

@app.get('/api/entities')
def entities(limit:int=100,offset:int=0,status:str|None=None,type:str|None=None,q:str|None=None):
    conn=connect(); sql='SELECT id,type,name,description,properties_json,status,created_at,updated_at FROM entities'; params=[]; cond=[]
    if status: cond.append('status=?'); params.append(status)
    if type: cond.append('type=?'); params.append(type)
    if q: cond.append('(lower(name) LIKE ? OR lower(IFNULL(description,\'\')) LIKE ?)'); params.extend([f'%{q.lower()}%',f'%{q.lower()}%'])
    if cond: sql+=' WHERE '+' AND '.join(cond)
    total=conn.execute('SELECT COUNT(*) c FROM entities'+((' WHERE '+' AND '.join(c for c in cond)) if cond else ''),params).fetchone()['c']
    sql+=' ORDER BY updated_at DESC LIMIT ? OFFSET ?'; params.extend([max(1,min(limit,500)),max(0,offset)])
    rows=conn.execute(sql,params).fetchall(); conn.close()
    return [dict(r)|{'properties':loads(r['properties_json'],{}),'total':total} for r in rows]

@app.get('/api/entities/{entity_id}')
def entity(entity_id:str):
    conn=connect(); e=conn.execute('SELECT * FROM entities WHERE id=?',(entity_id,)).fetchone()
    if not e: conn.close(); raise HTTPException(404,'Entity not found')
    claims=conn.execute('''SELECT c.*,d.title FROM claims c JOIN documents d ON d.id=c.source_document_id WHERE c.subject_id=? OR c.object_id=? ORDER BY c.created_at DESC LIMIT 100''',(entity_id,entity_id)).fetchall()
    conn.close(); item=dict(e); item['aliases']=loads(item.pop('aliases_json'),[]); item['properties']=loads(item.pop('properties_json'),{}); item['claims']=[dict(x) for x in claims]; return item

@app.patch('/api/entities/{entity_id}')
def update_entity(entity_id:str,payload:EntityUpdate):
    fields=[]; params=[]
    if payload.description is not None: fields.append('description=?'); params.append(payload.description)
    if payload.name is not None:
        fields.append('name=?'); params.append(payload.name.strip())
        fields.append('aliases_json=?'); params.append(dumps(sorted({payload.name.strip(), *(payload.aliases or [])})))
    elif payload.aliases is not None:
        fields.append('aliases_json=?'); params.append(dumps(payload.aliases))
    if payload.type is not None:
        from .ontology import canonical_entity_type
        try: et=canonical_entity_type(payload.type)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
        fields.append('type=?'); params.append(et)
        fields.append('types_json=?'); params.append(dumps([et]))
    if payload.properties: fields.append('properties_json=?'); params.append(dumps(payload.properties))
    if not fields:
        conn=connect(); exists=conn.execute('SELECT 1 FROM entities WHERE id=?',(entity_id,)).fetchone(); conn.close()
        if not exists: raise HTTPException(404,'Entity not found')
        return {'id':entity_id,'updated':False}
    conn=connect()
    try:
        cur=conn.execute(f'UPDATE entities SET {",".join(fields)},updated_at=CURRENT_TIMESTAMP WHERE id=?',(*params,entity_id))
        row=conn.execute('SELECT name,aliases_json FROM entities WHERE id=?',(entity_id,)).fetchone()
        if row:
            from .ontology import normalize_name
            conn.execute('DELETE FROM entity_aliases WHERE entity_id=?',(entity_id,))
            for alias in loads(row['aliases_json'],[]):
                conn.execute('INSERT OR IGNORE INTO entity_aliases(entity_id,alias,alias_normalized) VALUES(?,?,?)',(entity_id,alias,normalize_name(alias)))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback(); conn.close(); raise HTTPException(409,'Another entity already uses this name') from exc
    conn.close()
    if not cur.rowcount: raise HTTPException(404,'Entity not found')
    return {'id':entity_id,'updated':True}

@app.post('/api/entities')
def create_entity(payload:EntityCreate):
    import uuid
    from .ontology import canonical_entity_type, normalize_name
    try: et=canonical_entity_type(payload.type)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    eid=str(uuid.uuid4()); name=payload.name.strip()
    aliases=sorted({name, *[a.strip() for a in payload.aliases if a.strip()]})
    conn=connect()
    try:
        conn.execute('INSERT INTO entities(id,type,types_json,name,aliases_json,description,properties_json,status) VALUES(?,?,?,?,?,?,?,?)',
                     (eid,et,dumps([et]),name,dumps(aliases),payload.description,dumps(payload.properties),'candidate'))
        for alias in aliases:
            conn.execute('INSERT OR IGNORE INTO entity_aliases(entity_id,alias,alias_normalized) VALUES(?,?,?)',(eid,alias,normalize_name(alias)))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback(); conn.close(); raise HTTPException(409,'An entity with this name already exists') from exc
    conn.close(); return {'id':eid,'name':name,'status':'candidate'}

@app.post('/api/ideas')
def create_idea(payload:IdeaCreate):
    import uuid
    iid=str(uuid.uuid4())
    conn=connect(); conn.execute('INSERT INTO ideas(id,content,status,source_document_id) VALUES(?,?,?,?)',(iid,payload.content,payload.status,payload.source_document_id)); conn.commit(); conn.close()
    return {'id':iid,'status':payload.status}

@app.post('/api/questions')
def create_question(payload:QuestionCreate):
    import uuid
    qid=str(uuid.uuid4())
    conn=connect(); conn.execute('INSERT INTO questions(id,content,status,source_document_id) VALUES(?,?,?,?)',(qid,payload.content,payload.status,payload.source_document_id)); conn.commit(); conn.close()
    return {'id':qid,'status':payload.status}

@app.post('/api/events')
def create_event(payload:EventCreate):
    import uuid
    eid=str(uuid.uuid4())
    conn=connect()
    conn.execute('''INSERT INTO events(id,event_type,description,participants_json,time_json,location,status,source_document_id)
                    VALUES(?,?,?,?,?,?,?,?)''',
                 (eid,payload.event_type,payload.description,dumps(payload.participants),dumps(payload.time),payload.location,payload.status,payload.source_document_id))
    conn.commit(); conn.close(); return {'id':eid,'status':payload.status}

@app.get('/api/research')
def research_list(limit:int=50):
    conn=connect(); rows=conn.execute('SELECT * FROM research_tasks ORDER BY created_at DESC LIMIT ?',(max(1,min(limit,200)),)).fetchall(); conn.close(); return [dict(r) for r in rows]

@app.post('/api/research')
def research_create(payload:ResearchCreate):
    import uuid
    rid=str(uuid.uuid4())
    conn=connect()
    conn.execute('INSERT INTO research_tasks(id,question_id,question_text,status) VALUES(?,?,?,?)',(rid,payload.question_id,payload.question_text,'open'))
    conn.commit(); conn.close(); return {'id':rid,'status':'open'}

@app.post('/api/research/{task_id}/run')
async def research_run(task_id:str):
    conn=connect(); task=conn.execute('SELECT * FROM research_tasks WHERE id=?',(task_id,)).fetchone()
    if not task: conn.close(); raise HTTPException(404,'Research task not found')
    conn.execute("UPDATE research_tasks SET status='running',updated_at=CURRENT_TIMESTAMP WHERE id=?",(task_id,)); conn.commit(); conn.close()
    try:
        from .workflows.agent_workflow import run_research_pipeline
        result=await run_research_pipeline(task['question_text'])
        conn=connect()
        conn.execute("UPDATE research_tasks SET status='completed',findings=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(result['answer'],task_id))
        conn.commit(); conn.close()
        return {'id':task_id,'status':'completed','findings':result['answer'],'agent':result['agent'],
                'packet_id':result.get('packet_id')}
    except Exception as exc:
        conn=connect(); conn.execute("UPDATE research_tasks SET status='failed',findings=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(f'{type(exc).__name__}: {exc}',task_id)); conn.commit(); conn.close()
        raise HTTPException(502,f'Research failed: {exc}') from exc

@app.post('/api/runs/{run_id}/cancel')
def cancel_run(run_id:str):
    from .runlog import request_cancel
    conn=connect(); row=conn.execute('SELECT status FROM llm_runs WHERE id=?',(run_id,)).fetchone(); conn.close()
    if not row: raise HTTPException(404,'Run not found')
    if row['status']!='started': raise HTTPException(409,'Run already finished')
    request_cancel(run_id)
    return {'id':run_id,'cancelling':True}

@app.get('/api/entities/{entity_id}/graph')
def entity_graph(entity_id:str,depth:int=1,limit:int=100):
    result=neighborhood(entity_id,depth,limit)
    if result is None: raise HTTPException(404,'Entity not found')
    return result

@app.get('/api/entities/{entity_id}/duplicates')
def entity_duplicates(entity_id:str,limit:int=5):
    """Entities that look like the same thing but were never auto-merged."""
    conn=connect()
    try:
        out=find_similar_entities(conn,entity_id,limit=max(1,min(limit,20)))
    finally:
        conn.close()
    return {'entity_id':entity_id,'duplicates':out}

@app.get('/api/claims')
def claims(limit:int=100,status:str|None=None):
    conn=connect(); sql='''SELECT c.id,c.predicate,c.content,c.object_text,c.context_json,c.confidence,c.status,s.id subject_id,s.name subject_name,o.id object_id,o.name object_name,c.source_document_id,c.source_chunk_id,c.source_start_offset,c.source_end_offset,c.source_quote FROM claims c JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id'''; params=[]
    if status: sql+=' WHERE c.status=?'; params.append(status)
    sql+=' ORDER BY c.created_at DESC LIMIT ?'; params.append(max(1,min(limit,500))); rows=conn.execute(sql,params).fetchall(); conn.close(); return [dict(r)|{'context':loads(r['context_json'],{})} for r in rows]

@app.get('/api/relations')
def relations(limit:int=100,status:str|None=None):
    conn=connect(); sql='''SELECT r.*,a.name source_name,b.name target_name FROM relations r JOIN entities a ON a.id=r.source_id JOIN entities b ON b.id=r.target_id'''; params=[]
    if status: sql+=' WHERE r.status=?'; params.append(status)
    sql+=' ORDER BY r.created_at DESC LIMIT ?'; params.append(max(1,min(limit,500))); rows=conn.execute(sql,params).fetchall(); conn.close(); return [dict(r)|{'context':loads(r['context_json'],{})} for r in rows]

@app.get('/api/review')
def review(limit:int=100):
    limit=max(1,min(limit,500)); conn=connect()
    out={}
    out['entities']=[dict(r) for r in conn.execute(
        "SELECT id,name,type,description,status,created_at FROM entities WHERE status='candidate' ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()]
    out['claims']=[dict(r) for r in conn.execute(
        '''SELECT c.id,c.predicate,c.content,c.object_text,c.confidence,c.status,c.polarity,c.modality,c.claim_type,
                  c.source_document_id,c.source_chunk_id,c.source_quote,c.source_start_offset,c.source_end_offset,
                  s.name subject_name,o.name object_name
           FROM claims c JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id
           WHERE c.status='candidate' ORDER BY c.created_at DESC LIMIT ?''',(limit,)).fetchall()]
    out['relations']=[dict(r) for r in conn.execute(
        '''SELECT r.id,r.predicate,r.confidence,r.status,r.source_document_id,
                  a.name source_name,b.name target_name
           FROM relations r JOIN entities a ON a.id=r.source_id JOIN entities b ON b.id=r.target_id
           WHERE r.status='candidate' ORDER BY r.created_at DESC LIMIT ?''',(limit,)).fetchall()]
    conn.close(); return out

@app.patch('/api/knowledge/{kind}/{item_id}/status')
def update_status(kind:str,item_id:str,payload:StatusUpdate):
    allowed=KNOWLEDGE_STATUSES if kind in {'entity','claim','relation'} else IDEA_STATUSES if kind=='idea' else QUESTION_STATUSES if kind=='question' else set()
    if payload.status not in allowed: raise HTTPException(422,'Invalid status')
    table={'entity':'entities','claim':'claims','relation':'relations','idea':'ideas','question':'questions'}.get(kind)
    if not table: raise HTTPException(400,'Unsupported knowledge kind')
    conn=connect(); cur=conn.execute(f'UPDATE {table} SET status=? WHERE id=?',(payload.status,item_id)); conn.commit(); conn.close()
    if not cur.rowcount: raise HTTPException(404,'Knowledge object not found')
    return {'id':item_id,'status':payload.status}

@app.get('/api/runs')
def list_runs(limit:int=50, task_type:str|None=None, status:str|None=None):
    limit=max(1,min(limit,200))
    sql='SELECT r.*,d.title document_title FROM llm_runs r LEFT JOIN documents d ON d.id=r.document_id'
    params=[]
    cond=[]
    if task_type: cond.append('r.task_type=?'); params.append(task_type)
    if status: cond.append('r.status=?'); params.append(status)
    if cond: sql+=' WHERE '+' AND '.join(cond)
    sql+=' ORDER BY r.created_at DESC LIMIT ?'; params.append(limit)
    conn=connect(); rows=conn.execute(sql,params).fetchall(); conn.close()
    out=[]
    for r in rows:
        item=dict(r); item['summary']=loads(item.pop('summary_json'),{}); out.append(item)
    return out

@app.get('/api/runs/{run_id}')
def get_run(run_id:str):
    conn=connect()
    run=conn.execute('SELECT r.*,d.title document_title FROM llm_runs r LEFT JOIN documents d ON d.id=r.document_id WHERE r.id=?',(run_id,)).fetchone()
    if not run: conn.close(); raise HTTPException(404,'Run not found')
    steps=conn.execute('SELECT * FROM llm_run_steps WHERE run_id=? ORDER BY step_index',(run_id,)).fetchall()
    conn.close()
    item=dict(run); item['summary']=loads(item.pop('summary_json'),{}); item['steps']=[dict(s) for s in steps]
    return item

@app.get('/api/context/cache')
def context_cache_stats():
    """Context Cache hit-rate observability (P3)."""
    from .context.cache import cache_stats
    return cache_stats()


@app.get('/api/context/runs')
def context_runs(limit:int=50, agent:str|None=None):
    from .context import list_context_runs
    return list_context_runs(limit=limit, agent_name=agent)

@app.get('/api/context/runs/{context_run_id}')
def context_run_detail(context_run_id:str):
    from .context import get_context_run
    item=get_context_run(context_run_id)
    if item is None: raise HTTPException(404,'Context run not found')
    return item

@app.get('/api/context/budgets')
def context_budgets():
    from .context import AGENT_BUDGETS, DEFAULT_BUDGET
    return {'budgets':AGENT_BUDGETS,'default':DEFAULT_BUDGET}

@app.get('/api/context/metrics')
def context_metrics(days:int=14):
    """Token accounting: planned context per agent + provider-reported usage per task."""
    days=max(1,min(days,90)); window=f'-{days} days'
    conn=connect()
    agents=[dict(r) for r in conn.execute('''
        SELECT agent_name, COUNT(*) calls,
               CAST(AVG(actual_tokens) AS INT) avg_context_tokens,
               COALESCE(SUM(actual_tokens),0) context_tokens,
               COALESCE(SUM(trimmed_tokens),0) trimmed_tokens,
               COALESCE(SUM(over_budget),0) over_budget_calls,
               ROUND(AVG(efficiency),4) avg_efficiency
        FROM context_runs WHERE created_at>=datetime('now',?)
        GROUP BY agent_name ORDER BY calls DESC''',(window,)).fetchall()]
    tasks=[dict(r) for r in conn.execute('''
        SELECT r.task_type, COUNT(DISTINCT r.id) runs, COUNT(s.id) steps,
               COALESCE(SUM(s.prompt_tokens),0) prompt_tokens,
               COALESCE(SUM(s.completion_tokens),0) completion_tokens,
               COALESCE(SUM(CASE WHEN s.usage_source='provider' THEN 1 ELSE 0 END),0) provider_steps
        FROM llm_runs r LEFT JOIN llm_run_steps s ON s.run_id=r.id
        WHERE r.created_at>=datetime('now',?)
        GROUP BY r.task_type ORDER BY runs DESC''',(window,)).fetchall()]
    conn.close()
    for t in tasks:
        total=t['prompt_tokens']+t['completion_tokens']
        t['total_tokens']=total
        t['tokens_per_run']=round(total/t['runs']) if t['runs'] else 0
    return {'window_days':days,'agents':agents,'by_task_type':tasks,
            'totals':{'prompt_tokens':sum(t['prompt_tokens'] for t in tasks),
                      'completion_tokens':sum(t['completion_tokens'] for t in tasks),
                      'context_calls':sum(a['calls'] for a in agents),
                      'context_tokens':sum(a['context_tokens'] for a in agents)}}

@app.post('/api/ask')
def ask(req:AskRequest):
    """Grounded answer through the Context Runtime.

    Minimum sufficient evidence: dedup the retrieved chunks, escalate each hit only
    as far as its own signals justify (L1 quote by default), add summary-first
    knowledge, then let the compiler fit everything into the KnowledgeAgent budget.
    """
    from .runlog import record_run
    from .retrieval import entity_summaries, evidence_hits
    from .context import COMPILER, PLANNER
    from .context.providers import (EntitySummary, EvidenceHit, EvidenceProvider,
                                    KnowledgeProvider, ProviderRegistry, apply_escalation,
                                    dedup_hits)
    from .runtime import TaskContext, features_for

    raw=evidence_hits(req.question,req.top_k)
    hits,dropped=dedup_hits([EvidenceHit.from_dict(h) for h in raw])
    if not hits:
        # Record the attempt too, so "no evidence" answers stay observable.
        with record_run('ask', agent_role='ask') as run:
            with run.step('answer', input_summary=req.question[:200]) as step:
                step.output='No matching evidence was found in the knowledge base.'
            run.summary={'question':req.question[:200],'answer_chars':0,'evidence_count':0}
        return {'question':req.question,'answer':'知识库中没有找到与该问题匹配的证据。请先导入相关文档并建立索引，或换用 Agent 问答让 Agent 尝试检索。','citations':[],'evidence':[]}

    apply_escalation(hits)
    task=TaskContext(task_type='ask', agent='KnowledgeAgent', goal=req.question,
                     features=features_for('KnowledgeAgent','ask'))
    plan=PLANNER.plan(task)
    registry=ProviderRegistry()
    registry.register(KnowledgeProvider([EntitySummary.from_dict(s) for s in entity_summaries(raw)]))
    registry.register(EvidenceProvider(hits))
    compiled=COMPILER.compile(registry.collect(task,plan), plan)
    try: response=answer(req.question, compiled.context, context=compiled)
    except RuntimeError as exc: raise HTTPException(503,str(exc)) from exc
    citations=[{'document_id':h.document_id,'chunk_id':h.chunk_id,'title':h.title,'start_offset':h.start_offset,'end_offset':h.end_offset} for h in hits]
    return {'question':req.question,'answer':response,'citations':citations,
            'evidence':[h.to_response() for h in hits],
            'context':{'tokens':compiled.total_tokens,'budget':compiled.plan.hard_budget,
                       'withheld':len(compiled.withheld),'saved_tokens':compiled.saved_tokens,
                       'dedup_dropped':len(dropped),
                       'levels':[h.level for h in hits]}}


@app.put('/api/documents/{doc_id}')
def update_doc(doc_id: str, data: DocumentCreate):
    import hashlib
    content_hash = hashlib.sha256(data.content.encode('utf-8')).hexdigest()
    conn = connect()
    try:
        cur = conn.execute("UPDATE documents SET title=?,content=?,source_type=?,source_uri=?,content_hash=?,metadata_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (data.title, data.content, data.source_type, data.source_uri, content_hash, dumps(data.metadata), doc_id))
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback(); conn.close(); raise HTTPException(409, 'Another document already has the same content') from exc
    conn.close()
    if not cur.rowcount:
        raise HTTPException(404, 'Document not found')
    return {'id': doc_id, 'updated': True}

@app.delete('/api/documents/{doc_id}')
def delete_doc(doc_id: str):
    if not delete_document(doc_id):
        raise HTTPException(404, 'Document not found')
    return {'id': doc_id, 'deleted': True}

@app.get('/api/graph')
def global_graph(limit: int = 500):
    limit = max(1, min(limit, 2000)); conn = connect()
    nodes = [dict(r) for r in conn.execute('SELECT id,type,name,description,status FROM entities ORDER BY updated_at DESC LIMIT ?', (limit,)).fetchall()]
    edges = [dict(r) for r in conn.execute("SELECT r.id,r.source_id,r.target_id,r.predicate,r.confidence,r.status,r.source_document_id,r.source_chunk_id,a.name source_name,b.name target_name FROM relations r JOIN entities a ON a.id=r.source_id JOIN entities b ON b.id=r.target_id ORDER BY r.created_at DESC LIMIT ?", (limit,)).fetchall()]
    claims = [dict(r) for r in conn.execute("SELECT c.id,c.subject_id,c.object_id,c.object_text,c.predicate,c.confidence,c.status,c.source_document_id,c.source_chunk_id,s.name subject_name,o.name object_name FROM claims c JOIN entities s ON s.id=c.subject_id LEFT JOIN entities o ON o.id=c.object_id ORDER BY c.created_at DESC LIMIT ?", (limit,)).fetchall()]
    conn.close(); return {'nodes': nodes, 'edges': edges, 'claims': claims}

@app.get('/api/stats')
def stats():
    conn = connect(); tables = ['documents','chunks','entities','claims','relations','ideas','questions','events','chunk_embeddings','llm_runs','llm_run_steps','context_runs','context_sections']
    out = {t: conn.execute(f'SELECT COUNT(*) c FROM {t}').fetchone()['c'] for t in tables}; conn.close(); return out

@app.get('/api/export')
def export_all():
    conn = connect(); out = {}
    configs = {
      'documents':'SELECT * FROM documents ORDER BY created_at', 'chunks':'SELECT * FROM chunks ORDER BY document_id,chunk_index',
      'entities':'SELECT * FROM entities ORDER BY created_at', 'entity_aliases':'SELECT * FROM entity_aliases ORDER BY entity_id',
      'claims':'SELECT * FROM claims ORDER BY created_at', 'relations':'SELECT * FROM relations ORDER BY created_at',
      'ideas':'SELECT * FROM ideas ORDER BY created_at', 'questions':'SELECT * FROM questions ORDER BY created_at', 'events':'SELECT * FROM events ORDER BY created_at'
    }
    for table, sql in configs.items(): out[table] = [dict(r) for r in conn.execute(sql).fetchall()]
    conn.close(); return out

from pathlib import Path as _Path
_DIST = _Path('web/dist')

if (_DIST / 'assets').exists():
    app.mount('/assets', StaticFiles(directory=str(_DIST / 'assets')), name='assets')
app.mount('/static', StaticFiles(directory='web'), name='static')

@app.get('/')
def index():
    if (_DIST / 'index.html').exists(): return FileResponse(_DIST / 'index.html')
    return FileResponse('web/index.html')
