from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .config import runtime, llm_test_mode
from .agents.extraction_agent import (
    KIND_KEYS, detect_structured, extract_structured, scope_hint, wanted_kinds)
from .extraction import normalize_extraction
from .extraction_items import ACCEPTED, SECTIONS, compile_extraction_items
from .runlog import current_run, is_cancelled, record_run

# The real agent entry points, remembered so the test-mode guard can step aside when a
# test has swapped in its own fake provider (a fake costs nothing, so it needs no guard).
_REAL_DETECT = detect_structured
_REAL_EXTRACT = extract_structured

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'llm'


def _fixture_key(payload: str) -> str:
    """Stable identity of an extraction payload, for record/replay fixtures."""
    return hashlib.sha1((payload or '').encode('utf-8')).hexdigest()[:12]


def _replay_fixture(kind: str, payload: str) -> dict | None:
    path = _FIXTURE_DIR / f'{kind}-{_fixture_key(payload)}.json'
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def _resolve_call(kind: str, payload: str) -> dict | None:
    """Resolve an LLM call under the active test mode.

    * ``live`` → ``None`` (call the real agent);
    * ``disabled`` → raise, so a test can never silently spend money;
    * ``replay`` → return the recorded fixture, or raise when it is missing (a silent
      fallback to the network is exactly the leak this guards against).
    """
    mode = llm_test_mode()
    if mode == 'live':
        return None
    if mode == 'disabled':
        raise AssertionError(
            f'LLM call forbidden (LLM_TEST_MODE=disabled): {kind}. '
            'Mark the test @pytest.mark.live_llm or provide a replay fixture.')
    data = _replay_fixture(kind, payload)
    if data is None:
        raise AssertionError(
            f'No replay fixture for {kind} [{_fixture_key(payload)}]. '
            'Record one with scripts/llm_record.py, or run with LLM_TEST_MODE=live.')
    return data


def _guard_client() -> None:
    mode = llm_test_mode()
    if mode != 'live':
        raise AssertionError(f'LLM chat client forbidden (LLM_TEST_MODE={mode}).')


def _client():
    _guard_client()
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
    replay = _resolve_call('extract', payload) if extract_structured is _REAL_EXTRACT else None
    if run is None:
        return replay if replay is not None else await extract_structured(payload)
    with run.step(step_name, input_summary=input_summary) as step:
        data = replay if replay is not None else await extract_structured(payload)
        step.output = json.dumps(data, ensure_ascii=False)
        return data


def _restrict(data: dict, wanted: set[str]) -> dict:
    """Erase the kinds Pass 1 did not name, keeping all five schema keys."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for kind in KIND_KEYS:
        if kind not in wanted:
            out[kind] = []
    return out


async def _detect_one(payload: str, step_name: str, input_summary: str) -> set[str] | None:
    """Pass 1: triage which kinds a batch contains, recorded as its own step.

    Detection only narrows the work, so a failure (missing model, provider
    error, malformed output) must never drop a batch: it degrades to ``None``
    and the caller extracts every kind, exactly as the single-pass pipeline did.
    """
    run = current_run()
    try:
        replay = _resolve_call('detect', payload) if detect_structured is _REAL_DETECT else None
        if run is None:
            data = replay if replay is not None else await detect_structured(payload)
            return wanted_kinds(data)
        with run.step(step_name, input_summary=input_summary) as step:
            data = replay if replay is not None else await detect_structured(payload)
            step.output = json.dumps(data, ensure_ascii=False)
            return wanted_kinds(data)
    except AssertionError:
        # Test-mode violation (disabled / missing fixture) must stay loud.
        raise
    except Exception:
        # Degradation, not failure: an unknown scope means "extract everything".
        return None


def _repair_payload(rejected: list) -> dict:
    """The payload the repair call is allowed to see: the rejected items only.

    Sending the whole envelope back would invite the model to rewrite claims that
    already compiled — losing good knowledge to fix bad knowledge. The repair is an
    item-level operation, so the repair prompt is item-level too.
    """
    out: dict[str, list] = {s: [] for s in SECTIONS}
    for result in rejected:
        section = result.item_type + 's' if result.item_type in ('event', 'idea', 'question') else result.item_type
        if section in out:
            out[section].append(result.input)
    return out


def _item_key(section: str, item) -> tuple:
    """Identity of an extracted item, for de-duplicating a retry against the first pass."""
    if not isinstance(item, dict):
        return (section, str(item))
    if section == 'entities':
        return ('entity', (item.get('name') or '').casefold())
    if section == 'claims':
        return ('claim', (item.get('subject') or '').casefold(), item.get('predicate'),
                (item.get('object') or '').casefold(), item.get('source_chunk'))
    return (section, item.get('source_chunk'),
            (item.get('description') or item.get('content') or '')[:80])


def _merge_retry(outcome, retry) -> tuple[dict, list]:
    """Join a batch's accepted items with the retry's, and finalise the failures.

    Items the retry fixed are accepted (and marked ``attempts=2`` so the retry is
    visible); items it did not fix keep their first-attempt error alongside the
    second one. Nothing is silently dropped — and nothing is stored twice: a repair
    that echoes an already-accepted item must not duplicate it.
    """
    merged = {s: list(outcome.raw_envelope[s]) for s in SECTIONS}
    for section in SECTIONS:
        seen = {_item_key(section, item) for item in merged[section]}
        for item in retry.raw_envelope[section]:
            key = _item_key(section, item)
            if key in seen:
                continue
            seen.add(key)
            merged[section].append(item)
    for result in retry.accepted:
        result.attempts = 2

    def _section_of(item_type: str) -> str:
        return item_type + 's' if item_type in ('event', 'idea', 'question', 'entity') else item_type

    # Which refusals is the retry answering? The repair payload carried the rejected
    # items only, grouped by section and in order, so the retry's items in a section
    # answer that section's refusals positionally. Matching on content would be wrong
    # here: a successful repair *changes* the item, which is the whole point.
    by_section: dict[str, list] = {}
    for failure in outcome.failures:
        by_section.setdefault(_section_of(failure.item_type), []).append(failure)

    final: list = []
    for section, priors in by_section.items():
        answers = [r for r in retry.results if _section_of(r.item_type) == section]
        for i, prior in enumerate(priors):
            answer = answers[i] if i < len(answers) else None
            if answer is None:
                # The repair never re-extracted it: the first refusal still stands.
                final.append(prior)
            elif answer.status == ACCEPTED:
                answer.attempts = 2          # fixed by the retry
            else:
                answer.attempts = 2
                answer.prior_errors = [{'attempt': 1, 'error_code': prior.error_code,
                                        'error_message': prior.error_message}]
                final.append(answer)
        # A repair that invents extra items is judged by the same rule as anything else.
        for extra in answers[len(priors):]:
            if extra.status != ACCEPTED:
                extra.attempts = 2
                final.append(extra)
    return merged, final


async def extract(chunks: list[dict], *, report: dict | None = None) -> dict:
    """Extract knowledge with the AgentScope extraction agent (task §12).

    Each batch runs in two passes. Pass 1 (``detect_structured``) cheaply
    decides which knowledge kinds the batch contains; only when it names at
    least one kind does Pass 2 (``extract_structured``) run, scoped to those
    kinds. A failed detection degrades to the original single-pass behaviour
    instead of losing the batch.

    Compilation is **item-level** (Step 11). A claim the ontology refuses no longer
    costs its batch: the rejected items alone are sent to one repair call, and if the
    repair does not fix them they are reported as failures while every accepted item
    of the same batch is returned. ``report``, when given, receives those failures.
    """
    results = []
    failures: list[dict] = []
    batch_size = max(1, int(runtime()['llm_batch_chunks']))
    batches = [chunks[i:i + batch_size] for i in range(0, len(chunks), batch_size)]
    run = current_run()
    for n, batch in enumerate(batches, 1):
        if run is not None and is_cancelled(run.id):
            raise RuntimeError('提取已被用户取消')
        payload = '\n\n'.join(f"[CHUNK {c['id']}]\n{c['content']}" for c in batch)
        chars = sum(len(c['content']) for c in batch)
        scope = f'batch {n}/{len(batches)} · {len(batch)} chunks · {chars} chars'
        # Pass 1: does this batch hold anything worth extracting?
        wanted = await _detect_one(payload, 'detect_batch', scope)
        if wanted is not None and not wanted:
            # Nothing named → skip Pass 2 entirely (no extraction call at all).
            results.append(_empty())
            continue
        # Pass 2: extract, scoped to the kinds Pass 1 named (or unscoped when
        # detection was unavailable).
        if wanted is None:
            scoped, extract_summary = payload, scope
        else:
            scoped = f'{payload}\n\n{scope_hint(wanted)}'
            extract_summary = f'{scope} · kinds: {", ".join(sorted(wanted))}'
        data = await _extract_one(scoped, 'extract_batch', extract_summary)
        if wanted is not None:
            data = _restrict(data, wanted)
        outcome = compile_extraction_items(data)
        if outcome.rejected:
            # One repair attempt (the project's existing limit), item-level: only the
            # refused items are re-extracted, and only their results are re-compiled.
            repair = _repair_instruction(_repair_payload(outcome.rejected),
                                         error=outcome.rejected[0].error_message)
            if wanted is not None:
                repair = f'{repair}\n\n{scope_hint(wanted)}'
            repaired = await _extract_one(
                repair, 'extract_repair',
                f'batch {n}/{len(batches)} · item-level retry · {len(outcome.rejected)} rejected')
            if wanted is not None:
                repaired = _restrict(repaired, wanted)
            merged, final_failures = _merge_retry(outcome, compile_extraction_items(repaired))
            results.append(merged)
            failures.extend(r.failure_view() for r in final_failures)
            continue
        results.append(outcome.raw_envelope)
    if report is not None:
        report['rejected_items'] = failures
        report['rejected_count'] = len(failures)
    if run is not None:
        # Carried on the run, not returned: the call stays ``extract(chunks)`` so every
        # existing caller — and every test double — keeps working unchanged.
        run.extraction_issues = failures
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
