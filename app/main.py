from __future__ import annotations
import sqlite3
import traceback
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from . import __version__
from .config import runtime, save_settings
from .db import init_db, loads, dumps, transaction
from .models import (AskRequest, DocumentCreate, StatusUpdate, SearchRequest, EntityUpdate,
                     EntityCreate, IdeaCreate, QuestionCreate, EventCreate, ResearchCreate,
                     CorrectionRequest, CorrectionApplyRequest, CitationValidateRequest)
from .service import create_document, index_document, embed_document, delete_document
from .retrieval import evidence_pack, format_evidence, search, lexical_search
from .llm import answer
from .ontology import (KNOWLEDGE_STATUSES, IDEA_STATUSES, QUESTION_STATUSES,
                       canonical_entity_type, normalize_name)
from .importer import SUPPORTED
from .graph import neighborhood
from .resolution import find_similar_entities
from .prompt_profiles import list_profiles, get_profile, update_profile, reset_profile, restore_version
from .repositories import (CatalogRepository, ClaimRepository, DocumentRepository, EntityRepository,
                           EventRepository, IdeaRepository, OperationRepository, QuestionRepository,
                           RelationRepository, ResearchRepository, RunRepository)

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
    items,total=DocumentRepository().list(limit)
    return [item|{'total':total} for item in items]

@app.get('/api/events')
def events_list(limit:int=100,offset:int=0,status:str|None=None):
    return EventRepository().list(max(1,min(limit,200)),max(0,offset),status)

@app.get('/api/ideas')
def ideas_list(limit:int=100,offset:int=0,status:str|None=None):
    return IdeaRepository().list(max(1,min(limit,200)),max(0,offset),status)

@app.get('/api/questions')
def questions_list(limit:int=100,offset:int=0,status:str|None=None):
    return QuestionRepository().list(max(1,min(limit,200)),max(0,offset),status)

@app.get('/api/stats/timeseries')
def stats_timeseries(days:int=14):
    return CatalogRepository().timeseries(max(1,min(days,90)))

@app.get('/api/database/integrity')
def database_integrity():
    return CatalogRepository().integrity()

@app.get('/api/claims/{claim_id}')
def get_claim(claim_id:str):
    item=ClaimRepository().get(claim_id)
    if not item: raise HTTPException(404,'Claim not found')
    return item

@app.get('/api/claims/{claim_id}/relations')
def claim_relations_for(claim_id:str):
    """How this claim relates to other claims (both directions).

    Claims are never overwritten, so this is the only place knowledge evolution is
    expressed — including whether a newer claim supersedes this one.
    """
    return {'claim_id':claim_id,'relations':ClaimRepository().relations_for_claim(claim_id)}

@app.get('/api/claim-relations')
def claim_relations_queue(status:str|None=None,limit:int=100):
    """Claim-to-claim relationships awaiting a decision, newest first."""
    return ClaimRepository().relation_queue(status,max(1,min(limit,500)))

@app.post('/api/claim-relations/{relation_id}/analyze')
async def analyze_claim_relation(relation_id:str):
    """Ask the model how two claims relate — a suggestion the user may accept.

    Deterministic detection runs at import time. This is the on-demand escape hatch
    for cases structure cannot read (differently-worded statements, evidence that
    states a replacement). It writes nothing by itself.
    """
    claims=ClaimRepository()
    rel=claims.relation(relation_id)
    if not rel:
        raise HTTPException(404,'Claim relation not found')
    new_claim=claims.brief(rel['source_claim_id']); old_claim=claims.brief(rel['target_claim_id'])

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
    with transaction() as conn:
        claims=ClaimRepository(conn)
        rel=claims.relation(relation_id)
        if not rel:
            raise HTTPException(404,'Claim relation not found')
        claims.update_relation(relation_id,status=new_status,relationship=relationship)
        if relationship=='supersedes' and new_status=='accepted':
            older=claims.status_of(rel['target_claim_id'])
            claims.set_relation_previous_status(relation_id,older)
            claims.set_status(rel['target_claim_id'],'superseded')
        elif rel.get('target_previous_status'):
            # Any retreat from a confirmed supersession restores the older claim.
            claims.restore_status(rel['target_claim_id'],rel['target_previous_status'],only_if='superseded')
    return {'id':relation_id,'status':new_status,'relationship':relationship}

@app.post('/api/knowledge/corrections')
async def correction_plan(payload: CorrectionRequest):
    """Plan a one-sentence correction. Writes nothing.

    The model parses the sentence and judges its relationship to existing claims;
    when no exact match is found, a second model pass verifies the new statement
    against the existing knowledge base. The user then confirms through ``/apply``.
    """
    from .workflows.correction_workflow import build_plan, parse_intent, verify_new_claim
    intent = await parse_intent(payload.text)
    if intent is None:
        raise HTTPException(503, '模型不可用或未配置，无法解析纠正语句')
    plan = build_plan(payload.text, intent)
    # An ontology-blocked plan has no predicate to check against; skip the model
    # pass and return the honest "cannot map to a registered predicate" plan.
    if plan.relationship == 'new' and not plan.blocked:
        plan = await verify_new_claim(plan)
    return plan.to_dict()

@app.post('/api/knowledge/corrections/apply')
def correction_apply(payload: CorrectionApplyRequest):
    """Execute a confirmed correction through the CORRECT operation."""
    from .domain.operations import OperationError
    from .workflows.correction_workflow import CorrectionIntent, apply_correction, build_plan
    intent = CorrectionIntent(text=payload.text, subject=payload.subject,
                              predicate=payload.predicate, object=payload.object,
                              polarity=payload.polarity, confidence=payload.confidence)
    plan = build_plan(payload.text, intent)
    try:
        return apply_correction(plan, relationship=payload.relationship,
                                related_claim_id=payload.related_claim_id,
                                apply_supersede=payload.apply_supersede or None)
    except OperationError as exc:
        raise HTTPException(422, str(exc)) from exc

@app.get('/api/knowledge/claims/{claim_id}/quality')
def claim_quality(claim_id:str):
    """Deterministic seven-dimension quality score for one claim (no LLM)."""
    from .domain.knowledge_quality import score_claim_by_id
    q = score_claim_by_id(claim_id)
    if q is None:
        raise HTTPException(404, 'Claim not found')
    return q.to_dict()


@app.get('/api/knowledge/quality/review')
def quality_review(limit:int=50, threshold:float=0.70):
    """Claims the quality scorer recommends for human review, weakest first."""
    from .domain.knowledge_quality import review_queue
    return review_queue(limit=max(1, min(limit, 200)), threshold=threshold)


@app.get('/api/knowledge/operations')
def knowledge_operations(kind:str|None=None, limit:int=50):
    """Audit trail: every knowledge mutation, newest first."""
    return OperationRepository().list(max(1,min(limit,200)), kind)

@app.get('/api/knowledge/operations/kinds')
def knowledge_operation_kinds():
    from .domain.operations import registered_operations
    return {'kinds': sorted(registered_operations())}

@app.post('/api/knowledge/operations')
def knowledge_operation(payload: dict):
    """Execute a knowledge operation (CREATE / SUPERSEDE / MERGE / ARCHIVE / ...).

    The only sanctioned way to change knowledge: it validates, mutates lifecycle
    and relationships (never content), and is recorded in the audit trail.
    """
    from .domain.operations import OperationError, OperationRequest, run
    body = payload or {}
    kind = body.get('kind')
    if not kind: raise HTTPException(422, 'kind is required')
    try:
        with transaction() as conn:
            result = run(OperationRequest(kind=kind, payload=body.get('payload') or {},
                                          actor=body.get('actor', 'user'),
                                          reason=body.get('reason', '')), conn)
    except OperationError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'operation_id': result.operation_id, 'kind': result.kind, 'affected': result.affected}

@app.get('/api/entities/{entity_id}/object')
def entity_object(entity_id:str):
    entities=EntityRepository()
    e=entities.get_full(entity_id)
    if not e: raise HTTPException(404,'Entity not found')
    claims=ClaimRepository().for_entity(entity_id)
    relations=RelationRepository().for_entity(entity_id)
    docs=list({c['source_document_id'] for c in claims})
    events=EventRepository().for_documents(docs,50) if docs else []
    ideas=IdeaRepository().for_documents(docs,50) if docs else []
    questions=QuestionRepository().for_documents(docs,50) if docs else []
    evidence=[c for c in claims if c['source_quote']]
    type_decision=[]
    for c in claims:
        if c['subject_id']==entity_id and c['predicate']=='defined_as' and c['object_text']:
            type_decision.append({'type':c['object_text'][:60],'reason':f"来源明确定义：{(c['source_quote'] or '')[:90]}",'ok':c['polarity']=='positive'})
    counts={'claims':len(claims),'relations':len(relations),'events':len(events),'ideas':len(ideas),
            'questions':len(questions),'evidence':len(evidence),'documents':len(docs)}
    return {'entity':e,'counts':counts,'claims':claims,'relations':relations,'evidence':evidence,
            'events':events,'ideas':ideas,'questions':questions,'type_decision':type_decision}

@app.get('/api/conflicts')
def conflicts_list(limit:int=50):
    rows=ClaimRepository().conflicts_rows()
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
    entities=EntityRepository().health_counts()
    claims=ClaimRepository().health_counts()
    relations=RelationRepository().count()
    open_q=QuestionRepository().open_count()
    return {'total_claims':claims['total'],'verified_claims':claims['verified'],
            'verified_claim_ratio':round(claims['verified']/max(1,claims['total'])*100,1),
            'total_entities':entities['total'],'verified_entities':entities['verified'],
            'verified_entity_ratio':round(entities['verified']/max(1,entities['total'])*100,1),
            'object_text_claims':claims['object_text'],'relations':relations,
            'open_questions':open_q,'stale_candidates':entities['stale']}

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
    item=DocumentRepository().get(doc_id)
    if not item: raise HTTPException(404,'Document not found')
    item['metadata']=loads(item.pop('metadata_json','{}'),{}); return item

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
    documents=DocumentRepository()
    rows=documents.chunks(doc_id)
    if not rows and not documents.exists(doc_id):
        # Distinguish a valid empty/unindexed doc from missing doc.
        raise HTTPException(404,'Document not found')
    return rows

@app.get('/api/entities')
def entities(limit:int=100,offset:int=0,status:str|None=None,type:str|None=None,q:str|None=None):
    items,total=EntityRepository().list(limit=max(1,min(limit,500)),offset=max(0,offset),
                                        status=status,type=type,q=q)
    return [item|{'total':total} for item in items]

@app.get('/api/entities/{entity_id}')
def entity(entity_id:str):
    entities=EntityRepository()
    item=entities.get(entity_id)
    if not item: raise HTTPException(404,'Entity not found')
    item['claims']=entities.count_claims_for(entity_id)
    return item

@app.patch('/api/entities/{entity_id}')
def update_entity(entity_id:str,payload:EntityUpdate):
    changes={}
    if payload.description is not None: changes['description']=payload.description
    if payload.name is not None:
        changes['name']=payload.name.strip()
        changes['aliases_json']=dumps(sorted({payload.name.strip(), *(payload.aliases or [])}))
    elif payload.aliases is not None:
        changes['aliases_json']=dumps(payload.aliases)
    if payload.type is not None:
        try: et=canonical_entity_type(payload.type)
        except ValueError as exc: raise HTTPException(422,str(exc)) from exc
        changes['type']=et
        changes['types_json']=dumps([et])
    if payload.properties: changes['properties_json']=dumps(payload.properties)
    entities=EntityRepository()
    if not changes:
        if not entities.exists(entity_id): raise HTTPException(404,'Entity not found')
        return {'id':entity_id,'updated':False}
    aliases=loads(changes['aliases_json'],[]) if 'aliases_json' in changes else None
    try:
        rowcount=entities.update(entity_id,changes,aliases=aliases,normalize=normalize_name)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409,'Another entity already uses this name') from exc
    if not rowcount: raise HTTPException(404,'Entity not found')
    return {'id':entity_id,'updated':True}

@app.post('/api/entities')
def create_entity(payload:EntityCreate):
    import uuid
    try: et=canonical_entity_type(payload.type)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc
    eid=str(uuid.uuid4()); name=payload.name.strip()
    aliases=sorted({name, *[a.strip() for a in payload.aliases if a.strip()]})
    try:
        EntityRepository().insert(eid,type=et,types=[et],name=name,aliases=aliases,
                                  description=payload.description,properties=payload.properties,
                                  normalize=normalize_name)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409,'An entity with this name already exists') from exc
    return {'id':eid,'name':name,'status':'candidate'}

@app.post('/api/ideas')
def create_idea(payload:IdeaCreate):
    iid=IdeaRepository().insert(content=payload.content,status=payload.status,source_document_id=payload.source_document_id)
    return {'id':iid,'status':payload.status}

@app.post('/api/questions')
def create_question(payload:QuestionCreate):
    qid=QuestionRepository().insert(content=payload.content,status=payload.status,source_document_id=payload.source_document_id)
    return {'id':qid,'status':payload.status}

@app.post('/api/events')
def create_event(payload:EventCreate):
    eid=EventRepository().insert(event_type=payload.event_type,description=payload.description,
                                 participants=payload.participants,time=payload.time,location=payload.location,
                                 status=payload.status,source_document_id=payload.source_document_id)
    return {'id':eid,'status':payload.status}

@app.get('/api/research')
def research_list(limit:int=50):
    return ResearchRepository().list(max(1,min(limit,200)))

@app.post('/api/research')
def research_create(payload:ResearchCreate):
    rid=ResearchRepository().create(question_id=payload.question_id,question_text=payload.question_text)
    return {'id':rid,'status':'open'}

@app.post('/api/research/{task_id}/run')
async def research_run(task_id:str):
    research=ResearchRepository()
    task=research.get(task_id)
    if not task: raise HTTPException(404,'Research task not found')
    research.set_status(task_id,'running')
    try:
        from .workflows.agent_workflow import run_research_pipeline
        result=await run_research_pipeline(task['question_text'])
        research.set_status(task_id,'completed',result['answer'])
        return {'id':task_id,'status':'completed','findings':result['answer'],'agent':result['agent'],
                'packet_id':result.get('packet_id')}
    except Exception as exc:
        research.set_status(task_id,'failed',f'{type(exc).__name__}: {exc}')
        raise HTTPException(502,f'Research failed: {exc}') from exc

@app.post('/api/runs/{run_id}/cancel')
def cancel_run(run_id:str):
    from .runlog import request_cancel
    status=RunRepository().status(run_id)
    if status is None: raise HTTPException(404,'Run not found')
    if status!='started': raise HTTPException(409,'Run already finished')
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
    entities=EntityRepository()
    with entities.read() as conn:
        out=find_similar_entities(conn,entity_id,limit=max(1,min(limit,20)))
    return {'entity_id':entity_id,'duplicates':out}

@app.get('/api/claims')
def claims(limit:int=100,status:str|None=None):
    return ClaimRepository().list(max(1,min(limit,500)),status)

@app.get('/api/relations')
def relations(limit:int=100,status:str|None=None):
    return RelationRepository().list(max(1,min(limit,500)),status)

@app.get('/api/review')
def review(limit:int=100):
    limit=max(1,min(limit,500))
    return {'entities':EntityRepository().candidates(limit),
            'claims':ClaimRepository().candidates(limit),
            'relations':RelationRepository().candidates(limit)}

@app.patch('/api/knowledge/{kind}/{item_id}/status')
def update_status(kind:str,item_id:str,payload:StatusUpdate):
    allowed=KNOWLEDGE_STATUSES if kind in {'entity','claim','relation'} else IDEA_STATUSES if kind=='idea' else QUESTION_STATUSES if kind=='question' else set()
    if payload.status not in allowed: raise HTTPException(422,'Invalid status')
    if kind=='entity':
        rowcount=EntityRepository().update(item_id,{'status':payload.status})
    elif kind=='claim':
        rowcount=ClaimRepository().set_status(item_id,payload.status)
    elif kind=='relation':
        rowcount=RelationRepository().set_status(item_id,payload.status)
    elif kind=='idea':
        rowcount=IdeaRepository().set_status(item_id,payload.status)
    elif kind=='question':
        rowcount=QuestionRepository().set_status(item_id,payload.status)
    else:
        raise HTTPException(400,'Unsupported knowledge kind')
    if not rowcount: raise HTTPException(404,'Knowledge object not found')
    return {'id':item_id,'status':payload.status}

@app.get('/api/runs')
def list_runs(limit:int=50, task_type:str|None=None, status:str|None=None):
    return RunRepository().list(max(1,min(limit,200)),task_type,status)

@app.get('/api/runs/{run_id}')
def get_run(run_id:str):
    item=RunRepository().get(run_id)
    if not item: raise HTTPException(404,'Run not found')
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
    return CatalogRepository().context_metrics(max(1,min(days,90)))

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


@app.post('/api/qa/validate')
def qa_validate(req: CitationValidateRequest):
    """Citation Validation: is the answer grounded in its citations?

    Deterministic dimensions (locatability / coverage / currentness) always run.
    The semantic ``support`` dimension calls the configured model; if the model
    is unavailable the report degrades to the three deterministic dimensions with
    a ``llm_unavailable`` flag rather than failing.
    """
    from .workflows.citation_validation_workflow import validate_citations_sync
    report = validate_citations_sync(req.question, req.answer, req.citations)
    return report.to_dict()


@app.put('/api/documents/{doc_id}')
def update_doc(doc_id: str, data: DocumentCreate):
    try:
        rowcount=DocumentRepository().update(doc_id,title=data.title,content=data.content,
                                             source_type=data.source_type,source_uri=data.source_uri,
                                             metadata=data.metadata)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, 'Another document already has the same content') from exc
    if not rowcount:
        raise HTTPException(404, 'Document not found')
    return {'id': doc_id, 'updated': True}

@app.delete('/api/documents/{doc_id}')
def delete_doc(doc_id: str):
    if not delete_document(doc_id):
        raise HTTPException(404, 'Document not found')
    return {'id': doc_id, 'deleted': True}

@app.get('/api/graph')
def global_graph(limit: int = 500):
    limit = max(1, min(limit, 2000))
    catalog = CatalogRepository()
    return {'nodes': catalog.graph_nodes(limit),
            'edges': RelationRepository().global_edges(limit),
            'claims': catalog.graph_claims(limit)}

@app.get('/api/stats')
def stats():
    return CatalogRepository().stats()

@app.get('/api/export')
def export_all():
    return CatalogRepository().export_all()

from pathlib import Path as _Path
_DIST = _Path('web/dist')

if (_DIST / 'assets').exists():
    app.mount('/assets', StaticFiles(directory=str(_DIST / 'assets')), name='assets')
app.mount('/static', StaticFiles(directory='web'), name='static')

@app.get('/')
def index():
    if (_DIST / 'index.html').exists(): return FileResponse(_DIST / 'index.html')
    return FileResponse('web/index.html')
