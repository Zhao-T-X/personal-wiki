"""One-sentence knowledge correction.

    sentence -> Intent Parser (LLM) -> Candidate Resolver -> Correction Planner
             -> user confirmation -> CORRECT Operation -> new Claim + Evidence
             + ClaimRelation -> ClaimStateResolver -> current knowledge

The LLM only *proposes*: it parses the sentence into a structured intent and
judges how the new statement relates to what we already knew. Execution is
deterministic (Workflow -> Operation -> Domain rule -> Repository) and the model
never touches the database.

A user correction is stored as its own small document, so the resulting claim
has real, traceable provenance — knowledge never appears without a source.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from ..chunking import chunk_text
from ..claim_relations import compare_claim
from ..db import loads, transaction
from ..domain.claim_evolution import evolution_reason, supersede_recommended
from ..domain.compiler import REJECTED, ClaimDraft, CompileResult, KnowledgeCompiler
from ..domain.operations import OperationError, OperationRequest, run
from ..domain.predicate_resolver import TEMPORAL_SIGNALS, resolve_predicate
from ..ontology import match_claim_predicates
from ..repositories import ClaimRepository, DocumentRepository, EntityRepository
from ..resolution import find_entity_id

# Most significant relationship first (matches app/claim_relations._PRIORITY).
_PRIORITY = {'duplicate': 0, 'supersedes': 1, 'contradicts': 2, 'coexists': 3}


@dataclass
class CorrectionIntent:
    text: str
    subject: str
    predicate: str                       # canonical (empty when unresolved)
    object: str = ''
    polarity: str = 'positive'
    confidence: float | None = None
    predicate_candidate: str = ''        # what the LLM proposed, verbatim
    temporal_signal: str | None = None
    predicate_resolution: dict | None = None
    # Carried so a correction can express the *same* draft an extraction would: a
    # route that silently hardcoded these would compile to a different canonical
    # claim than the other entries for the very same input.
    claim_type: str = 'factual'
    modality: str = 'asserted'
    context: dict = field(default_factory=dict)


@dataclass
class CorrectionPlan:
    text: str
    intent: CorrectionIntent
    subject_entity_id: str | None
    relationship: str          # duplicate | contradicts | coexists | supersedes | new | unresolved
    related_claim_id: str | None
    apply_supersede: bool
    candidates: list[dict] = field(default_factory=list)
    summary: str = ''
    verification: dict | None = None
    predicate_resolution: dict | None = None
    blocked: bool = False      # ontology gate refused to compile a predicate

    def to_dict(self) -> dict:
        return {
            'text': self.text,
            'intent': asdict(self.intent),
            'subject_entity_id': self.subject_entity_id,
            'relationship': self.relationship,
            'related_claim_id': self.related_claim_id,
            'apply_supersede': self.apply_supersede,
            'candidates': self.candidates,
            'summary': self.summary,
            'verification': self.verification,
            'predicate_resolution': self.predicate_resolution,
            'blocked': self.blocked,
        }


def _intent_system(text: str) -> str:
    """Build the intent prompt with a task-relevant vocabulary subset.

    The full predicate registry never enters the prompt: the runtime injects the
    few registered predicates the sentence plausibly maps to, turning the task
    from "define a predicate" into "select one of these". The model is also given
    an explicit failure exit, because a model with no legal way to say "no match"
    will invent one (ONTOLOGY MUTATION POLICY; ADR-011).
    """
    subset = match_claim_predicates(text, limit=8)
    vocabulary = ', '.join(subset) if subset else '(no registered predicate matched this sentence)'
    return (
        'You extract a single factual statement from a user correction sentence and reply with '
        'JSON only: {"subject": "...", "predicate_candidate": "...", "object": "...", '
        '"polarity": "positive|negative", "temporal_signal": null, "confidence": 0.0-1.0}. '
        'The ontology is a CONTROLLED vocabulary. predicate_candidate MUST be selected from: '
        f'{vocabulary}. '
        'Do NOT invent or combine predicates; do NOT create a predicate from adjectives, tense, '
        'time or status. Words describing time or evolution (new, current, former, previous, next, '
        'successor, ...) are NOT predicates - put them in temporal_signal instead. '
        'If no listed value expresses the relation, set predicate_candidate to null; an honest '
        'failure is required and inventing a predicate is not allowed. '
        'subject is the entity the sentence is about; object is the value the subject is being '
        'related to. Never invent facts that are not in the sentence.'
    )


_VERIFY_SYSTEM = (
    'You verify a new statement against an existing personal knowledge base. '
    'Given the new statement and existing claims/evidence, decide whether it is '
    'supported, contradicted, unrelated, or uncertain. '
    'Reply with JSON only: {"verdict": "supported|contradicted|unrelated|uncertain", '
    '"confidence": 0.0-1.0, "rationale": "...", "conflicting_claim_id": "..." or null}. '
    'A contradiction means the new statement and an existing claim cannot both be true, '
    'for example two different people holding the same single-valued role. '
    'Never invent facts not present in the supplied claims or evidence.'
)


def _intent_from_data(text: str, data: dict) -> CorrectionIntent | None:
    """Compile a model-shaped draft into an intent. The only place that does.

    Accepts both key shapes — the candidate form the prompt asks for
    (``subject_candidate`` / ``predicate_candidate`` / ``object_candidate``) and
    the older ``subject`` / ``predicate`` / ``object`` — so a replayed draft and a
    live model reply take exactly the same path through the resolver.
    """
    subject = str(data.get('subject') or data.get('subject_candidate') or '').strip()
    candidate = data.get('predicate_candidate')
    if candidate is None:
        candidate = data.get('predicate')
    candidate = str(candidate or '').strip()
    obj = str(data.get('object') or data.get('object_candidate') or '').strip()
    if not subject:
        return None
    # The LLM proposed a candidate; the compiler decides. An unresolved candidate
    # is a legal outcome, not a reason to drop the whole intent.
    resolution = resolve_predicate(candidate, text=text, limit=8)
    llm_signal = str(data.get('temporal_signal') or '').strip() or None
    signal = resolution.temporal_signal or (
        llm_signal if llm_signal in TEMPORAL_SIGNALS else None)
    polarity = str(data.get('polarity') or 'positive').strip().lower()
    if polarity not in ('positive', 'negative'):
        polarity = 'positive'
    try:
        raw = data.get('confidence')
        confidence = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        confidence = None
    return CorrectionIntent(text=text, subject=subject,
                            predicate=resolution.predicate or '',
                            object=obj, polarity=polarity, confidence=confidence,
                            predicate_candidate=candidate, temporal_signal=signal,
                            predicate_resolution=resolution.to_dict(),
                            claim_type=str(data.get('claim_type') or 'factual').strip().lower(),
                            modality=str(data.get('modality') or 'asserted').strip().lower(),
                            context=(data.get('context') if isinstance(data.get('context'), dict)
                                     else {}))


def intent_from_draft(text: str, draft: dict) -> CorrectionIntent | None:
    """Compile a draft without a model — for tests, replay and offline evaluation.

    Step 3→4 of the correction loop (LLM draft -> KnowledgeCompiler) is pure, so
    it can be exercised end to end with a recorded draft instead of a live call.
    """
    return _intent_from_data(text, dict(draft or {}))


async def parse_intent(text: str) -> CorrectionIntent | None:
    """LLM step: sentence -> structured intent. ``None`` when the model is
    unavailable, so the caller can degrade honestly instead of guessing."""
    import asyncio

    from ..config import runtime
    from ..llm import _client

    def _call() -> CorrectionIntent | None:
        try:
            client = _client()
            response = client.chat.completions.create(
                model=runtime()['openai_model'], temperature=0.0,
                response_format={'type': 'json_object'},
                messages=[{'role': 'system', 'content': _intent_system(text)},
                          {'role': 'user', 'content': text}])
            data = json.loads(response.choices[0].message.content or '{}')
        except Exception:
            return None
        return _intent_from_data(text, data)

    return await asyncio.to_thread(_call)


def _entity_types(name: str) -> list[str]:
    """Declared entity types for a name (empty when unknown — unknown is not illegal).

    Looked up through the shared resolver, alias table included, so a sentence
    phrased with an alias ("苹果" for "苹果公司") is still checked against the
    relation's domain/range instead of silently passing as unconstrained.
    """
    entity_id = find_entity_id(None, name=name)
    if not entity_id:
        return []
    row = EntityRepository().get_raw(entity_id)
    if not row:
        return []
    return [str(t) for t in loads(row.get('types_json') or '[]', [])]


def compile_intent(intent: CorrectionIntent) -> CompileResult:
    """The correction route into the compiler — the same door extraction uses.

    The intent already carries a *resolved* predicate (``intent_from_draft`` ran the
    resolver), so this compiles the canonical draft and answers, in one place, both
    "is this claim legal?" and "what exactly is the canonical claim?". Every field
    the correction eventually writes comes from here, which is what makes a
    correction and an extraction of the same draft compile to the same claim.
    """
    return KnowledgeCompiler().compile_claim(ClaimDraft(
        subject=intent.subject, predicate_candidate=intent.predicate,
        object=intent.object, claim_type=intent.claim_type, polarity=intent.polarity,
        modality=intent.modality, temporal_signal=intent.temporal_signal,
        context=intent.context, confidence=intent.confidence,
        subject_types=_entity_types(intent.subject),
        object_types=_entity_types(intent.object)))


def _domain_range_violation(intent: CorrectionIntent) -> str | None:
    """Check the relation's domain/range before planning anything.

    A predicate can resolve and still be used illegally — an Organization's CEO
    must be a Person, not a Location. This is the same gate the compiler applies
    (ADR-011), run here so a bad pairing is refused at plan time instead of being
    discovered after the user confirms.
    """
    if not intent.predicate or not intent.object:
        return None
    result = compile_intent(intent)
    if result.status == REJECTED and 'domain_range_violation' in result.reasons:
        return 'domain_range_violation'
    return None


def _pick(candidates: list[dict]) -> dict | None:
    ranked = [c for c in candidates if c.get('relationship') in _PRIORITY]
    if not ranked:
        return None
    ranked.sort(key=lambda c: (_PRIORITY[c['relationship']], -c.get('confidence', 0.0)))
    return ranked[0]


def build_plan(text: str, intent: CorrectionIntent) -> CorrectionPlan:
    """Deterministic, read-only planning: where does this new statement land?

    Does not write anything — creating a missing entity is deferred to
    :func:`apply_correction`, so a plan can be shown to the user first.
    """
    # Resolution, not a name lookup: a subject phrased with an alias ("苹果" for
    # "苹果公司") must reach the entity's real claims, or the correction looks
    # brand-new and is filed beside the fact it was meant to correct. Read-only,
    # so the plan is still safe to show before anything is written.
    subject_id = find_entity_id(None, name=intent.subject)

    # Ontology gate: without a compiled predicate there is no knowledge to plan.
    # Refusing here is the point — creating a claim with an invented predicate
    # would contaminate the ontology, which is exactly what must not happen.
    if not intent.predicate:
        candidate = intent.predicate_candidate or intent.text
        suggestions = (intent.predicate_resolution or {}).get('candidates') or []
        hint = (f' 可考虑：{", ".join(suggestions)}。' if suggestions else '')
        return CorrectionPlan(
            text=text, intent=intent, subject_entity_id=subject_id,
            relationship='unresolved', related_claim_id=None, apply_supersede=False,
            candidates=[], predicate_resolution=intent.predicate_resolution,
            blocked=True,
            summary=(f'无法把「{candidate}」映射到受控谓词表（候选：{intent.predicate_candidate or "无"}）。'
                     '为避免污染本体，系统不会创建新谓词，也不会写入这条知识。'
                     f'{hint}请改用已注册的谓词来表达。'))

    # Domain/range gate: the ontology can forbid the pairing even when the predicate
    # itself resolves.
    violation = _domain_range_violation(intent)
    if violation:
        return CorrectionPlan(
            text=text, intent=intent, subject_entity_id=subject_id,
            relationship='rejected', related_claim_id=None, apply_supersede=False,
            candidates=[], predicate_resolution=intent.predicate_resolution,
            blocked=True,
            summary=(f'该陈述违反「{intent.predicate}」的 domain/range 约束（{violation}），'
                     '因此不会写入知识库。'))

    candidates: list[dict] = []
    if subject_id:
        existing = ClaimRepository().related(subject_id=subject_id, predicate=intent.predicate,
                                             exclude_id='', limit=10)
        probe = {'subject_id': subject_id, 'predicate': intent.predicate, 'object_id': None,
                 'object_text': intent.object, 'polarity': intent.polarity}
        for claim in existing:
            verdict = compare_claim(probe, claim)
            candidates.append({
                'claim_id': claim['id'],
                'object': claim.get('object_name') or claim.get('object_text'),
                'content': claim.get('content'),
                'status': claim.get('status'),
                **verdict,
            })

    best = _pick(candidates)
    relationship = best['relationship'] if best else 'new'
    related_id = best['related_claim_id'] if best else None

    # Claim Evolution (ADR-015): a single-valued, evolvable relation plus a stated
    # change is enough to *recommend* superseding — decided from the registry, not
    # guessed. Planning stays read-only; the user confirms before anything is written.
    if best and supersede_recommended(relationship, predicate=intent.predicate,
                                      temporal_signal=intent.temporal_signal):
        relationship = 'supersedes'
        summary = (f'检测到已有断言「{best["content"]}」。'
                   + evolution_reason(predicate=intent.predicate,
                                      temporal_signal=intent.temporal_signal))
    elif best:
        summary = f'与已有断言「{best["content"]}」的关系：{relationship}'
    else:
        summary = '未找到可关联的既有断言，将作为新知识创建'

    return CorrectionPlan(text=text, intent=intent, subject_entity_id=subject_id,
                          relationship=relationship, related_claim_id=related_id,
                          apply_supersede=relationship == 'supersedes',
                          candidates=candidates, summary=summary,
                          predicate_resolution=intent.predicate_resolution)


async def verify_new_claim(plan: CorrectionPlan) -> CorrectionPlan:
    """Check a would-be 'new' claim against existing knowledge before creating it.

    The deterministic planner only matches exact subject+predicate pairs. This
    semantic verifier broadens the check: it retrieves claims about the same
    subject plus relevant evidence chunks, then asks the model whether the new
    statement is supported, contradicted, unrelated, or uncertain.

    If a high-confidence contradiction is found, the plan is upgraded from
    ``new`` to ``contradicts`` and the user must explicitly decide whether to
    override existing knowledge.
    """
    import asyncio

    from ..config import runtime
    from ..llm import _client
    from ..repositories import ClaimRepository
    from ..retrieval import evidence_hits

    intent = plan.intent
    subject_id = plan.subject_entity_id

    claims: list[dict] = []
    if subject_id:
        claims = [
            c for c in ClaimRepository().for_entity(subject_id)
            if c.get('subject_id') == subject_id
               and c.get('status') not in ('rejected', 'archived')
        ][:12]

    query = ' '.join(x for x in (intent.subject, intent.predicate, intent.object) if x).strip()
    chunks = evidence_hits(query, limit=6, claims_per_chunk=3) if query else []

    if not claims and not chunks:
        plan.verification = {
            'verdict': 'uncertain',
            'confidence': 0.0,
            'rationale': '知识库中没有找到相关证据，无法验证该陈述。',
            'conflicting_claim_id': None,
        }
        return plan

    def _call() -> dict:
        try:
            client = _client()
        except Exception as exc:
            return {
                'verdict': 'uncertain',
                'confidence': 0.0,
                'rationale': f'验证模型不可用：{exc}',
                'conflicting_claim_id': None,
            }

        claim_lines = []
        for c in claims:
            claim_lines.append(
                f"- claim_id: {c['id']} | {c.get('subject_name', '')} {c.get('predicate', '')} "
                f"{c.get('object_name') or c.get('object_text') or ''} | "
                f"status: {c.get('status', '')} | quote: {c.get('source_quote') or ''}"
            )
        chunk_lines = []
        for h in chunks:
            chunk_lines.append(
                f"- chunk_id: {h['chunk_id']} | doc: {h['title']}\n  {h['chunk_content'][:400]}"
            )
            for c in h.get('claims', []):
                chunk_lines.append(
                    f"  claim {c.get('id') or ''}: {c.get('subject_name', '')} {c.get('predicate', '')} "
                    f"{c.get('object_name') or c.get('object_text') or ''}"
                )

        prompt = (
            f"New statement to verify:\n"
            f"  text: {intent.text}\n"
            f"  subject: {intent.subject} | predicate: {intent.predicate} | object: {intent.object}\n\n"
            f"Existing claims about the same subject:\n"
            f"{chr(10).join(claim_lines) or '(none)'}\n\n"
            f"Relevant source chunks:\n"
            f"{chr(10).join(chunk_lines) or '(none)'}\n\n"
            "Is the new statement supported, contradicted, unrelated, or uncertain based on the existing knowledge?"
        )
        try:
            response = client.chat.completions.create(
                model=runtime()['openai_model'], temperature=0.0,
                response_format={'type': 'json_object'},
                messages=[{'role': 'system', 'content': _VERIFY_SYSTEM},
                          {'role': 'user', 'content': prompt}],
            )
            data = json.loads(response.choices[0].message.content or '{}')
        except Exception as exc:
            return {
                'verdict': 'uncertain',
                'confidence': 0.0,
                'rationale': f'验证模型调用失败：{exc}',
                'conflicting_claim_id': None,
            }

        verdict = str(data.get('verdict') or 'uncertain').strip().lower()
        if verdict not in ('supported', 'contradicted', 'unrelated', 'uncertain'):
            verdict = 'uncertain'
        try:
            confidence = float(data.get('confidence') or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            'verdict': verdict,
            'confidence': round(max(0.0, min(1.0, confidence)), 3),
            'rationale': str(data.get('rationale') or '').strip()[:800],
            'conflicting_claim_id': str(data.get('conflicting_claim_id') or '').strip() or None,
        }

    verification = await asyncio.to_thread(_call)
    plan.verification = verification

    if verification['verdict'] == 'contradicted' and verification['confidence'] >= 0.6:
        conflict_id = verification['conflicting_claim_id']
        if conflict_id and any(c.get('id') == conflict_id for c in claims):
            plan.relationship = 'contradicts'
            plan.related_claim_id = conflict_id
            plan.summary = (
                f"模型检测到与已有断言的矛盾（置信度 {verification['confidence']}）："
                f"{verification['rationale']} 若继续应用，将创建一条 contradicts 关系，"
                "原 Claim 不会被删除；你之后可在 Claim Relations 中将其改为 supersedes。"
            )
        else:
            plan.summary = (
                "模型认为新陈述可能与现有知识矛盾，但无法定位到具体 Claim。"
                f"{verification['rationale']}"
            )
    return plan


def apply_correction(plan: CorrectionPlan, *, relationship: str | None = None,
                     related_claim_id: str | None = None,
                     apply_supersede: bool | None = None) -> dict:
    """Execute the confirmed plan through the CORRECT operation.

    The correction sentence becomes its own document + chunk, so the new claim
    carries real provenance (document / chunk / offsets / quote).
    """
    intent = plan.intent
    if plan.blocked:
        raise OperationError('谓词未能映射到受控词表，纠正未执行：本体封闭，不允许创建新谓词。')
    # Defence in depth: re-compile even on the /apply path (manual entry included).
    # Two things are guaranteed at once — no route can persist an unregistered
    # predicate, and what is written is the *compiled* claim rather than an
    # ad-hoc reassembly of the intent's fields.
    compiled = compile_intent(intent)
    if not compiled.ok or compiled.claim is None:
        raise OperationError(
            f'"{intent.predicate or intent.predicate_candidate}" 不是已注册的谓词：'
            f'本体封闭，不允许创建或写入新谓词（{", ".join(compiled.reasons) or compiled.resolution.reason}）。')
    claim = compiled.claim
    predicate = claim.predicate
    relationship = plan.relationship if relationship is None else relationship
    related_claim_id = plan.related_claim_id if related_claim_id is None else related_claim_id
    if apply_supersede is None:
        apply_supersede = plan.apply_supersede

    with transaction() as conn:
        documents = DocumentRepository(conn)
        document_id = documents.create(title=f'纠正：{intent.text[:60]}', content=intent.text,
                                       source_type='correction')
        chunks = documents.replace_chunks(document_id, [
            (c.content, c.index, c.start_offset, c.end_offset) for c in chunk_text(intent.text)])
        if not chunks:
            raise OperationError('Correction text produced no chunk')
        chunk = chunks[0]
        # The temporal signal and the model's raw candidate travel in the claim's
        # context — the same place the extraction path puts them (app/knowledge.py).
        # Without this, the reason a supersede happened ("new") would live only in
        # the plan, which is discarded once it has been applied, and the stored
        # claim could not explain why it replaced its predecessor. Context is used
        # rather than a column so the predicate stays canonical with no migration.
        context = dict(claim.context)
        if claim.temporal_signal:
            context.setdefault('temporal_signal', claim.temporal_signal)
        if intent.predicate_candidate and intent.predicate_candidate != predicate:
            context.setdefault('predicate_candidate', intent.predicate_candidate)
        payload = {
            'subject': claim.subject, 'predicate': predicate,
            'object': claim.object, 'content': intent.text,
            'claim_type': claim.claim_type, 'polarity': claim.polarity,
            'modality': claim.modality, 'confidence': claim.confidence,
            'context': context,
            'source_document_id': document_id, 'source_chunk_id': chunk['id'],
            'source_start_offset': chunk['start_offset'], 'source_end_offset': chunk['end_offset'],
            'source_quote': intent.text,
            'related_claim_id': related_claim_id, 'relationship': relationship,
            'apply_supersede': apply_supersede,
        }
        result = run(OperationRequest(kind='CORRECT', payload=payload, actor='user',
                                      reason='one-sentence correction'), conn)
    return {'document_id': document_id, 'operation_id': result.operation_id, **result.affected}
