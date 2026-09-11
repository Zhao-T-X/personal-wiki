from __future__ import annotations
import json
from .config import runtime
from .agents.extraction_agent import extract_structured
from .extraction import normalize_extraction
from .runlog import current_run, is_cancelled, record_run


def _client():
    cfg = runtime()
    if not cfg['openai_api_key']:
        raise RuntimeError('API key is not configured')
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError('Install requirements.txt first') from exc
    return OpenAI(api_key=cfg['openai_api_key'], base_url=cfg['openai_base_url'])


def _empty():
    return {'entities':[], 'claims':[], 'events':[], 'ideas':[], 'questions':[]}


def _merge(results: list[dict]) -> dict:
    out = _empty()
    for r in results:
        for key in out:
            out[key].extend(r.get(key, []))
    return out


def _repair_instruction(raw: dict, error: str | None = None) -> str:
    """Build the re-extraction instruction used when normalization fails.

    ``error`` carries the validator's message - including the registry-subset
    predicate suggestions - so the repair call fixes the actual violation
    instead of guessing.
    """
    tail = f'\n\nValidation error to fix:\n{error}' if error else ''
    return f"""Re-read the source semantics and re-extract using LLM-Wiki Knowledge Extraction v2.0. Do not mechanically edit invalid JSON. Do not invent facts. Do not output relations or IDs. Every Claim, Event, Idea, and Question must include an exact evidence_quote.{tail}

Previous JSON:
{json.dumps(raw, ensure_ascii=False)}"""


def _provider_usage(usage) -> dict | None:
    """Normalise an OpenAI-compatible usage object into the runlog shape."""
    if usage is None:
        return None
    return {
        'prompt_tokens': int(getattr(usage, 'prompt_tokens', 0) or 0),
        'completion_tokens': int(getattr(usage, 'completion_tokens', 0) or 0),
        'usage_source': 'provider',
    }


async def _extract_one(payload: str, step_name: str, input_summary: str) -> dict:
    """Run one extraction pass, recording it as a step of the current run."""
    run = current_run()
    if run is None:
        return await extract_structured(payload)
    with run.step(step_name, input_summary=input_summary) as step:
        data = await extract_structured(payload)
        step.output = json.dumps(data, ensure_ascii=False)
        return data


async def extract(chunks: list[dict]) -> dict:
    """Extract knowledge with the AgentScope extraction agent.

    The agent owns the reasoning-acting loop. It can open the Knowledge
    Extraction skill, load the registries on demand, and must return its
    result through the structured-output contract defined in
    ``app.agents.extraction_agent``.
    """
    results = []
    batch_size = max(1, int(runtime()['llm_batch_chunks']))
    batches = [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]
    run = current_run()
    for n, batch in enumerate(batches, 1):
        if run is not None and is_cancelled(run.id):
            raise RuntimeError('提取已被用户取消')
        payload = '\n\n'.join(f"[CHUNK {c['id']}]\n{c['content']}" for c in batch)
        chars = sum(len(c['content']) for c in batch)
        data = await _extract_one(
            payload, 'extract_batch',
            f'batch {n}/{len(batches)} · {len(batch)} chunks · {chars} chars')
        try:
            results.append(normalize_extraction(data))
        except ValueError as exc:
            repaired = await _extract_one(
                _repair_instruction(data, error=str(exc)), 'extract_repair',
                f'batch {n}/{len(batches)} · retry after validation failure')
            results.append(normalize_extraction(repaired))
    return _merge(results)


def answer(question: str, evidence_pack: str, *, context=None) -> str:
    """Answer from the supplied evidence.

    ``context`` is the compiled Context Runtime result; when given, its trace is
    recorded inside this run so the token ledger and the answer stay linked.
    """
    with record_run('ask', agent_role='ask') as run:
        with run.step('answer', input_summary=question[:200]) as step:
            if context is not None:
                from .context import record_context
                record_context(context)
            client = _client()
            response = client.chat.completions.create(
                model=runtime()['openai_model'], temperature=0.15,
                messages=[
                    {'role':'system','content':'You are a careful personal knowledge-base assistant. Use only the supplied evidence. Cite sources inline exactly as [doc:ID chunk:ID]. Do not fabricate. Note conflicts and uncertainty.'},
                    {'role':'user','content':f'Question:\n{question}\n\nEvidence:\n{evidence_pack}'},
                ],
            )
            content = response.choices[0].message.content or ''
            step.set_usage(_provider_usage(getattr(response, 'usage', None)))
            step.output = content
            run.summary = {'question': question[:200], 'answer_chars': len(content)}
            return content
