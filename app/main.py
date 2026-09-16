from __future__ import annotations
import sqlite3
import traceback
from fastapi import FastAPI, HTTPException, UploadFile, File, Body
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from . import __version__
from .config import runtime, save_settings
from .db import init_db, loads, dumps, transaction
from .models import (AskRequest, DocumentCreate, StatusUpdate, SearchRequest, EntityUpdate,
                     EntityCreate, IdeaCreate, QuestionCreate, EventCreate, ResearchCreate,
                     CorrectionRequest, CorrectionApplyRequest, CitationValidateRequest,
                     MergeRequest, ObjectLinkRequest, ObjectLinkConfirmRequest,
                     CurationDecisionRequest, SuppressionRequest, IntentRequest)
from .service import create_document, index_document, embed_document, delete_document
from .retrieval import (evidence_pack, format_evidence, lexical_search, search,
                        search_knowledge)
from .llm import answer
from .ontology import (CLAIM_PREDICATES, KNOWLEDGE_STATUSES, IDEA_STATUSES,
                       QUESTION_STATUSES, canonical_entity_type, claim_predicate_spec,
                       claim_registry_version, normalize_name)
from .importer import SUPPORTED
from .domain.claim_history import order_chain
from .graph import neighborhood
from .resolution import find_similar_entities
from .prompt_profiles import list_profiles, get_profile, update_profile, reset_profile, restore_version
from .repositories import (CatalogRepository, ClaimRepository, DocumentRepository, EntityRepository,
                           EventRepository, IdeaRepository, OperationRepository, QuestionRepository,
                           RelationRepository, ResearchRepository, RunRepository)
from .evaluation.runners import run_evaluation
from .experiments import (load_corpus, run_summary_view, diff_summaries)
# Aliased to avoid shadowing the existing /api/runs handlers `list_runs`/`get_run`.
from .evaluation.store import list_runs as list_eval_runs, get_run as get_eval_run
from .evaluation import baseline as _evaluation_baseline

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


@app.get('/api/claims/{claim_id}/history')
def claim_history(claim_id:str):
    """One fact's evolution, oldest first — what it used to say, and when that changed.

    Composed entirely from stored facts: the accepted supersede relations give the
    order *and* the moment each statement stopped holding, and the claim rows give
    the values plus their evidence. Nothing is inferred or reconstructed — which is
    only possible because superseding moves a lifecycle status instead of deleting
    the row (ADR-005). The seed may be any member of the chain, since a caller
    arriving from search has no idea where in the timeline it landed.
    """
    claims = ClaimRepository()
    claim = claims.get(claim_id)
    if not claim: raise HTTPException(404,'Claim not found')
    edges = claims.supersede_edges(claim['subject_id'], claim['predicate'])
    chain = order_chain(edges, claim_id)
    superseded_at = {e['target_claim_id']: e['created_at'] for e in edges}
    superseded_by = {e['target_claim_id']: e['source_claim_id'] for e in edges}
    corroboration = claims.corroboration_counts(chain.ordered_ids)

    nodes = []
    for cid in chain.ordered_ids:
        row = claims.get(cid) or {}
        nodes.append({
            'id': cid,
            'subject': row.get('subject_name') or '',
            'predicate': row.get('predicate') or '',
            'predicate_label': _predicate_label(row.get('predicate')),
            'object': row.get('object_name') or row.get('object_text') or '',
            'status': row.get('status'),
            # 生效期间: from when the statement was written until the moment a later
            # one was accepted. The newest member has no end yet.
            'effective_from': row.get('created_at'),
            'effective_to': superseded_at.get(cid),
            'superseded_by': superseded_by.get(cid),
            'source_quote': row.get('source_quote'),
            'source_document_id': row.get('source_document_id'),
            'source_chunk_id': row.get('source_chunk_id'),
            # 依据: its own quote, plus every accepted duplicate — the same statement
            # asserted by another source.
            'sources': (1 if row.get('source_quote') else 0) + corroboration.get(cid, 0),
            'corroborating': corroboration.get(cid, 0),
            'is_current': cid == chain.current_id,
        })
    return {'claim_id': claim_id, 'current_id': chain.current_id, 'cycles': chain.cycles,
            'superseded_count': chain.superseded_count, 'chain': nodes}

@app.get('/api/claim-relations')
def claim_relations_queue(status:str|None=None,limit:int=200):
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
        result = apply_correction(plan, relationship=payload.relationship,
                                  related_claim_id=payload.related_claim_id,
                                  apply_supersede=payload.apply_supersede or None)
    except OperationError as exc:
        raise HTTPException(422, str(exc)) from exc
    # Same post-operation check as every other path that produces knowledge: a
    # correction that lands beside a disagreeing current claim reports it here, so the
    # discovery arrives with the change instead of waiting to be stumbled upon.
    from .review import issues_for_claims
    touched = [str(result[k]) for k in ('claim_id', 'superseded_claim_id', 'related_claim_id')
               if result.get(k)]
    return {**result, 'issues': issues_for_claims(touched)}

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
    # Post-operation integrity check, reported with the thing that caused it. An
    # operation that produced or moved knowledge is exactly when a new disagreement can
    # appear, and telling the user at that moment is the difference between a fix and a
    # silent break. Only the claims the operation touched are examined — the same
    # bounded recheck a merge performs, not a workspace scan.
    from .review import issues_for_claims
    touched = [str(v) for k, v in (result.affected or {}).items()
               if v and 'claim' in str(k)]
    return {'operation_id': result.operation_id, 'kind': result.kind,
            'affected': result.affected, 'issues': issues_for_claims(touched)}

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
async def index_doc(doc_id:str, tag:str|None=None):
    try: return await index_document(doc_id,use_llm=True, experiment_tag=tag)
    except KeyError as exc: raise HTTPException(404,'Document not found') from exc
    except RuntimeError as exc: raise HTTPException(503,str(exc)) from exc
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc

# ---------------------------------------------------------------------------
# Extraction Before/After experiment endpoints (task: real-button-driven regression)
# ---------------------------------------------------------------------------

@app.get('/api/experiments/corpus')
def experiment_corpus():
    """Golden Corpus metadata: which documents exist and what they should/shouldn't extract."""
    c = load_corpus()
    return {'version': c.get('version'),
            'documents': [{'id': d['id'], 'name': d.get('name'), 'source': d.get('source'),
                          'expected_entities': d.get('expected_entities', []),
                          'expected_non_entities': d.get('expected_non_entities', []),
                          'proposed_entities': [e['name'] for e in d.get('extraction', {}).get('entities', [])]}
                         for d in c.get('documents', [])]}


@app.post('/api/experiments/run')
async def experiment_run(content:str = Body(...), title:str = Body(...), tag:str|None = Body(None)):
    """The real import button, experiment-tagged. Runs the exact production pipeline
    (create document -> chunk -> extract via LLM -> persist). Snapshots the result
    under llm_runs so it can be diffed later. Requires a configured LLM."""
    try:
        doc_id = create_document(title=title, content=content, source_type='note',
                                metadata={'experiment': True})
        return await index_document(doc_id, use_llm=True, experiment_tag=tag)
    except RuntimeError as exc:
        raise HTTPException(503, f'LLM not configured or extraction failed: {exc}') from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/api/experiments/run-corpus')
def experiment_run_corpus():
    """Run the Golden Corpus through the real persistence pipeline (gate off then on)
    and return the Before/After report. The only substituted step is the model's
    inference (the golden envelope stands in); everything downstream is shipping code.
    No LLM key required."""
    from .experiments import run_experiment
    return run_experiment()


@app.get('/api/experiments/snapshots')
def experiment_snapshots(tag:str|None=None, limit:int=50):
    """List extraction snapshots (runs that stored a full extraction envelope)."""
    out = []
    for snap in RunRepository().extraction_snapshots(limit):
        summary = snap['summary']
        if tag and summary.get('experiment_tag') != tag:
            continue
        out.append({'run_id': snap['id'], 'document_id': snap['document_id'],
                    'created_at': snap['created_at'], 'view': run_summary_view(summary)})
    return out


@app.get('/api/experiments/compare')
def experiment_compare(before:str, after:str):
    """Diff two extraction snapshots by run id."""
    repo = RunRepository()
    b = repo.get(before)
    a = repo.get(after)
    if not b or not a:
        raise HTTPException(404, 'run not found')
    return diff_summaries(b['summary'], a['summary'])

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


@app.get('/api/search/knowledge')
def api_search_knowledge(q:str,limit:int=8,semantic:bool=True):
    """Search, presented as knowledge rather than as passages.

    ``/api/search`` is left exactly as it was — a flat list of chunks, which the
    knowledge space and the command palette both read — and this sits beside it as
    the product-level view over the same recall: the knowledge first, the passages
    it came from second. Two shapes, two addresses, so no caller has to special-case
    the other's format.
    """
    if not q.strip(): return {'knowledge':[],'results':[]}
    return search_knowledge(q,max(1,min(limit,50)),semantic=semantic)

@app.post('/api/onebox/intent')
def onebox_intent(req:IntentRequest):
    """One entry point in front of four workflows that already exist.

    Read-only and side-effect free: it returns the intent, how confident the reading
    is, why, and the *existing* endpoint(s) that would carry it out. Whether to act is
    the caller's decision — which is what makes a wrong reading cheap to fix: nothing
    has been written, so the user can re-route without retyping anything.

    Deliberately no model call. The sentence's own shape, plus the registry-driven
    signals the query router already extracts, decide this — asking a model here would
    make One Box the slowest way to use the wiki.
    """
    from .intent import classify
    return classify(req.text,context_claim_id=req.context_claim_id).to_dict()


@app.get('/api/integrity/scan')
def integrity_scan(duplicates:int=200,unlinked:int=200):
    """What looks wrong in the knowledge base right now — candidates, not verdicts.

    Read-only and side-effect free: nothing here changes knowledge, it only reports
    what a person may want to look at. Both findings are L2 — explained, then
    confirmed by the user — so this endpoint never repairs anything by itself.

    The defaults are the window the Review Inbox counts (app/review.py), so the badge
    and this list can never disagree about the same backlog. They are generous on
    purpose: the backlog is real, and a window small enough to look tidy would hide
    the part of it nobody can see.
    """
    from .integrity import scan as integrity_scan_rows
    return integrity_scan_rows(duplicate_limit=max(1,min(duplicates,500)),
                              unlinked_limit=max(1,min(unlinked,500)))


@app.get('/api/integrity/merge-impact')
def integrity_merge_impact(keep:str,drop:str):
    """Dry run: what merging these two entities would move, and what it would disturb.

    Includes the conflicts that would *appear* as a result. Merging does not reconcile
    facts, so the user is told before confirming — not after.
    """
    from .integrity import merge_impact
    try: return merge_impact(keep_id=keep,drop_id=drop)
    except ValueError as exc: raise HTTPException(404,str(exc)) from exc


@app.post('/api/integrity/merge')
def integrity_merge(req:MergeRequest):
    """Confirm a merge, then report what it disturbed.

    One subject for two rows, nothing else: claims keep their content, status and
    evidence, and no winner is chosen between conflicting facts. The response carries
    the post-merge recheck, because the conflicts this creates are a result of the
    operation rather than a separate thing to remember.
    """
    from .integrity import merge_entities
    try: return merge_entities(keep_id=req.keep_id,drop_id=req.drop_id)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc


@app.get('/api/integrity/curation')
def curation_decisions(limit:int=100):
    """What has been decided about the wiki's own bookkeeping, newest first.

    Not knowledge — see app/repositories/curation_repo.py. Listed so a decision can be
    seen and taken back: a judgement made once is not permanent truth.
    """
    from .integrity import curation_decisions as rows_
    return rows_(limit=max(1,min(limit,500)))


@app.post('/api/integrity/curation')
def curation_decide(req:CurationDecisionRequest):
    """Remember that two entities are not the same thing, so the scan stops asking."""
    from .integrity import record_not_same
    try:
        return record_not_same(entity_id_a=req.entity_id_a,entity_id_b=req.entity_id_b,
                               reason=req.reason or None)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc


@app.delete('/api/integrity/curation/{decision_id}')
def curation_revoke(decision_id:str):
    """Undo a curation decision. The pair goes back to being an ordinary candidate."""
    from .integrity import revoke_curation
    try: return revoke_curation(decision_id)
    except ValueError as exc: raise HTTPException(404,str(exc)) from exc


@app.post('/api/integrity/object-links')
def integrity_object_links(req:ObjectLinkRequest):
    """Link free-text objects to entities that already exist (never creates one)."""
    from .integrity import apply_object_links
    return apply_object_links(min_confidence=req.min_confidence,limit=max(1,min(req.limit,500)))


@app.post('/api/integrity/object-links/confirm')
def integrity_confirm_object_link(req:ObjectLinkConfirmRequest):
    """Confirm one suggestion by hand: this text refers to that entity.

    The half of the L2 tier a batch tool cannot do. A suggestion with several
    candidates has no target until a person picks one, so this is the only path that
    can act on it — and it is audited as a human decision rather than a system match.
    """
    from .integrity import confirm_object_link
    try: return confirm_object_link(claim_id=req.claim_id,entity_id=req.entity_id)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc


@app.get('/api/integrity/suppressions')
def integrity_suppressions(limit:int=100):
    """Suggestions the user has dismissed, so the decision can be read back and undone.

    Not knowledge and not a curation decision — see
    app/repositories/suppression_repo.py for why the two are kept apart.
    """
    from .integrity import dismissals
    return dismissals(limit=max(1,min(limit,500)))


@app.post('/api/integrity/suppressions')
def integrity_suppress(req:SuppressionRequest):
    """Stop offering one suggestion. Keyed on the claim and the literal it was about."""
    from .integrity import dismiss_suggestion
    try:
        return dismiss_suggestion(claim_id=req.claim_id,object_text=req.object_text,
                                  reason=req.reason or None)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc


@app.delete('/api/integrity/suppressions/{suppression_id}')
def integrity_revoke_suppression(suppression_id:str):
    """Un-ignore: the suggestion becomes an ordinary candidate again."""
    from .integrity import revoke_dismissal
    try: return revoke_dismissal(suppression_id)
    except ValueError as exc: raise HTTPException(404,str(exc)) from exc


@app.get('/api/documents/{doc_id}/chunks')
def document_chunks(doc_id: str):
    documents=DocumentRepository()
    rows=documents.chunks(doc_id)
    if not rows and not documents.exists(doc_id):
        # Distinguish a valid empty/unindexed doc from missing doc.
        raise HTTPException(404,'Document not found')
    return rows


def _predicate_label(predicate: str | None) -> str | None:
    """The registry's label for a predicate, or ``None`` when it declares none.

    ``has_ceo`` renders as 首席执行官 instead of the raw identifier. Predicates
    without a label stay raw on purpose: inventing a translation in the API layer
    would be a second vocabulary, and the registry is its only home (ADR-011).
    """
    spec = claim_predicate_spec(predicate or '')
    return spec.label if spec else None


@app.get('/api/ontology/predicates')
def ontology_predicates():
    """The registry's *display layer*: what each predicate is called in words.

    Any surface that shows a claim needs to name its predicate, and there are many
    such surfaces. Shipping the labels once — instead of repeating a
    ``predicate_label`` field on every claim-shaped response — keeps the vocabulary
    in a single place; a frontend map of its own would be a second ontology, and a
    per-endpoint copy would drift from the registry one response at a time.

    Labels are display metadata only. Code always branches on the predicate itself.
    """
    labels: dict[str, str | None] = {}
    for predicate in sorted(CLAIM_PREDICATES):
        spec = claim_predicate_spec(predicate)
        labels[predicate] = spec.label if spec else None
    return {'version': claim_registry_version(), 'labels': labels}


@app.get('/api/documents/{doc_id}/knowledge')
def document_knowledge(doc_id: str):
    """What this document contributed — the answer to "so what did it read out of it?"

    Composed from existing reads only: nothing is inferred, and the names are taken
    from the claims themselves, so this view can never disagree with the knowledge
    base — it *is* the knowledge base, filtered to one document.

    ``predicate_label`` is the registry's own label (``None`` when the registry
    declares none). It travels from the registry so no surface has to keep a
    second vocabulary of predicate names.
    """
    if not DocumentRepository().exists(doc_id):
        raise HTTPException(404,'Document not found')
    claims = ClaimRepository().for_document(doc_id, limit=200)
    names: list[str] = []
    seen: set[str] = set()
    for c in claims:
        for n in (c.get('subject_name'), c.get('object_name')):
            if n and n not in seen:
                seen.add(n); names.append(n)
    return {
        'document_id': doc_id,
        'names': names,
        'claims': [{
            'id': c.get('id'), 'subject': c.get('subject_name') or '',
            'predicate': c.get('predicate'),
            'predicate_label': _predicate_label(c.get('predicate')),
            'object': c.get('object_name') or c.get('object_text') or '',
            'status': c.get('status'), 'confidence': c.get('confidence'),
            'quote': c.get('source_quote'),
        } for c in claims],
        'counts': {
            'claims': len(claims),
            'names': len(names),
            'pending': sum(1 for c in claims if c.get('status') == 'candidate'),
        },
    }

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

@app.get('/api/research/{task_id}')
def research_detail(task_id:str):
    """One research task, with the knowledge its question bears on.

    Findings stay prose (that is what research produces); ``knowledge`` is the middle
    layer — the related claims with their state, projected through the shared read
    model so a card here reads exactly like a card anywhere else. The pending ones are
    the point: they are what the research is still waiting on, and their cards offer
    [采纳] rather than [纠正].

    Fetched on demand rather than for every task in the list, so a long list of tasks
    still costs one request.
    """
    from .service import research_candidates
    task=ResearchRepository().get(task_id)
    if not task: raise HTTPException(404,'Research task not found')
    return {**task,'knowledge':research_candidates(task_id)}


@app.post('/api/research/{task_id}/candidates')
async def research_propose_candidates(task_id:str):
    """Turn a task's findings into proposed knowledge — compiled, not pasted.

    The findings become a document, the extraction pipeline reads it, and whatever it
    can ground in a quote lands as a ``candidate`` claim: 研究候选, waiting for a human.
    Nothing writes a claim from prose directly — this is the same path every other
    document takes, which is exactly why a research candidate can be trusted no further
    than its evidence.
    """
    from .service import propose_research_candidates
    try: return await propose_research_candidates(task_id)
    except ValueError as exc: raise HTTPException(422,str(exc)) from exc


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

@app.get('/api/review/inbox')
def review_inbox():
    """How much is waiting for a decision, by kind — the number the sidebar shows.

    ``/api/review`` below stays exactly as it was: raw unreviewed rows, one list per
    table, which the Review page renders. This answers the product question instead —
    "how much work is waiting for me" — and they are deliberately two endpoints so
    neither has to grow a flag to serve the other (P2.5 §3).

    The total is the sum of its groups, and every group is filtered to something a
    person can still act on: a sidebar count and the page it opens must not disagree,
    and a queue that can never be emptied is one nobody reads.
    """
    from .review import inbox
    return inbox()


@app.get('/api/review')
def review(limit:int=200):
    """Raw unreviewed rows, one list per table — what the Review page renders.

    The default is the window the Review Inbox counts (app/review.py): a badge and the
    list it opens must describe the same set, so the page calls this without a limit
    and both read the same number from the same place.
    """
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

_NO_EVIDENCE_REPLY = ('知识库中没有找到与该问题匹配的证据。请先导入相关文档并建立索引，'
                      '或换用 Agent 问答让 Agent 尝试检索。')
# §23: a FACT_LOOKUP that cannot be answered *certainly* must refuse, not guess.
#   ambiguous -> several current claims compete, so there is no single answer;
#   stale     -> the only claims are superseded, so the wiki's structured knowledge
#                on this exact (subject, predicate) is not current. Falling through
#                to retrieval would hand the model an outdated claim (retrieval keeps
#                a superseded claim that nothing current covers) and invite it to
#                present that as today's answer — the exact failure §23 exists to
#                prevent.
# ``no_object_value`` is deliberately absent: that claim *is* current and simply has
# no object, so the answer may still exist as prose in a document — it falls through.
_DIRECT_REFUSALS = {
    'ambiguous_multiple_current_claims':
        '知识库中没有足够证据确定唯一答案（存在多条冲突记录），因此不作答。',
    # The wording must satisfy the shared refusal semantics (app/domain/refusal.py):
    # a safety stop the evaluator does not recognise would be scored as a
    # hallucination, so the endpoint and the evaluator must agree (ADR-014).
    'no_current_claim':
        '知识库中没有当前有效值：关于该问题的记录已被取代或过时，因此不作答。',
    # A proposal is not knowledge: the wiki *does* hold something about this fact, but
    # it is waiting for the user, so the honest answer names that instead of implying
    # there is nothing. Wording stays inside the insufficient-evidence family so the
    # evaluator recognises it as a refusal rather than scoring it as a hallucination.
    'candidate_not_accepted':
        '知识库中没有当前有效值：相关的陈述目前只是研究候选，尚未被采纳为知识，因此不作答。',
    'ambiguous_multiple_historical_claims':
        '知识库中没有足够证据确定唯一的历史值（存在多条历史记录），因此不作答。',
}


@app.post('/api/ask')
def ask(req:AskRequest):
    """Grounded answer through the Context Runtime.

    Route first (task §19/§20): the Query Router decides whether this question can
    be answered from stored knowledge at all. A simple fact lookup is answered from
    its Claim with **0 LLM calls**; one that cannot be answered *certainly* (ambiguous
    or superseded-only) is refused rather than guessed (§23). Everything else keeps
    the retrieval + generation path below.

    Minimum sufficient evidence: dedup the retrieved chunks, escalate each hit only
    as far as its own signals justify (L1 quote by default), add summary-first
    knowledge, then let the compiler fit everything into the KnowledgeAgent budget.
    """
    from .runlog import record_run
    from .retrieval import entity_summaries, evidence_hits
    from .domain.query_router import FACT_LOOKUP, STRUCTURED_REASONING
    from .workflows.ask_workflow import (ANSWERED, plan_question, supporting_knowledge,
                                         try_direct_answer, try_historical_answer)
    from .context import COMPILER, PLANNER
    from .context.providers import (EntitySummary, EvidenceHit, EvidenceProvider,
                                    KnowledgeProvider, ProviderRegistry, apply_escalation,
                                    dedup_hits)
    from .runtime import TaskContext, features_for

    _signals, route_plan = plan_question(req.question)
    if route_plan.route == FACT_LOOKUP:
        lookup, direct = 'claim', try_direct_answer(req.question)
    elif route_plan.route == STRUCTURED_REASONING:
        # Past-tense questions are stored knowledge too: superseding keeps the old
        # claim (it is never deleted), so "who WAS the CEO" is answered from
        # history with 0 LLM as well — and that is what proves evolution works.
        lookup, direct = 'history', try_historical_answer(req.question)
    else:
        lookup, direct = '', None
    if direct is not None and (direct.status == ANSWERED or direct.reason in _DIRECT_REFUSALS):
        reply = direct.answer if direct.status == ANSWERED else _DIRECT_REFUSALS[direct.reason]
        with record_run('ask', agent_role='ask') as run:
            # No step is recorded: a direct lookup is not a model call, so the
            # run's step_count stays 0 and the LLM-call accounting stays honest.
            run.summary={'question':req.question[:200], 'route':direct.route,
                         'lookup':lookup, 'answer_chars':len(reply or ''),
                         'evidence_count':len(direct.citations), 'llm_calls':0}
        # The answer keeps being prose; the support is the shared knowledge view, so a
        # 0-LLM lookup states exactly which stored claim it read instead of asking the
        # reader to trust a sentence with nothing behind it.
        direct_claims=[direct.claim['claim_id']] if direct.claim else []
        return {'question':req.question,'answer':reply,'citations':direct.citations,
                'evidence':direct.evidence,'answer_value':direct.answer_value,
                'knowledge':supporting_knowledge(direct_claims,question=req.question),
                'route':direct.route,'lookup':lookup,'status':direct.status,'direct':True,
                'reason':direct.reason,'llm_expected':False}

    raw=evidence_hits(req.question,req.top_k)
    hits,dropped=dedup_hits([EvidenceHit.from_dict(h) for h in raw])
    if not hits:
        # Record the attempt too, so "no evidence" answers stay observable.
        with record_run('ask', agent_role='ask') as run:
            with run.step('answer', input_summary=req.question[:200]) as step:
                step.output='No matching evidence was found in the knowledge base.'
            run.summary={'question':req.question[:200],'answer_chars':0,'evidence_count':0}
        return {'question':req.question,'answer':_NO_EVIDENCE_REPLY,'citations':[],'evidence':[],
                'knowledge':[],'route':route_plan.route,'status':'no_evidence','direct':False,
                'llm_expected':route_plan.llm_expected}

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
    # The claims the retrieved chunks were read for — collapsed to one entry per fact,
    # current first, and capped. The answer stays the answer; this is what backs it.
    cited_claims=[str(c.get('id')) for h in raw for c in (h.get('claims') or []) if c.get('id')]
    return {'question':req.question,'answer':response,'citations':citations,
            'evidence':[h.to_response() for h in hits],
            'knowledge':supporting_knowledge(cited_claims,question=req.question),
            'route':route_plan.route,'direct':False,
            'llm_expected':route_plan.llm_expected,
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

# --- evaluation board (Phase 7) --------------------------------------------
class EvalRunRequest(__import__('pydantic').BaseModel):
    suite: str


@app.post('/api/eval/run')
def eval_run(req: EvalRunRequest):
    """Run one evaluation suite end-to-end and persist the result."""
    try:
        return run_evaluation(req.suite)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/api/eval/runs')
def eval_runs(limit: int = 20):
    """Most-recent evaluation runs as ``{run_id, suite, created_at, summary}``."""
    return list_eval_runs(limit)


@app.get('/api/eval/runs/{run_id}')
def eval_run_detail(run_id: str):
    item = get_eval_run(run_id)
    if item is None:
        raise HTTPException(404, 'Evaluation run not found')
    return item


@app.get('/api/eval/baseline')
def eval_baseline(suite: str | None = None):
    """Baseline metrics: for one suite, or all suites keyed by name."""
    if suite is not None:
        data = _evaluation_baseline.read_baseline(suite)
        if data is None:
            raise HTTPException(404, f'No baseline recorded for suite: {suite}')
        return data
    return {'suites': _evaluation_baseline.read_all_baselines()}


class EvalBaselinePinRequest(__import__('pydantic').BaseModel):
    suite: str
    run_id: str


@app.post('/api/eval/baseline/pin')
def eval_baseline_pin(req: EvalBaselinePinRequest):
    """Pin a run's summary as the new baseline for its suite."""
    if req.suite not in ('extraction', 'qa', 'retrieval'):
        raise HTTPException(422, f'Unknown suite: {req.suite}')
    item = get_eval_run(req.run_id)
    if item is None:
        raise HTTPException(404, 'Evaluation run not found')
    if item['suite'] != req.suite:
        raise HTTPException(422, 'run_id does not belong to the given suite')
    return _evaluation_baseline.pin_baseline(req.suite, item['summary'])


from pathlib import Path as _Path
_DIST = _Path('web/dist')

if (_DIST / 'assets').exists():
    app.mount('/assets', StaticFiles(directory=str(_DIST / 'assets')), name='assets')
app.mount('/static', StaticFiles(directory='web'), name='static')

@app.get('/')
def index():
    if (_DIST / 'index.html').exists(): return FileResponse(_DIST / 'index.html')
    return FileResponse('web/index.html')
