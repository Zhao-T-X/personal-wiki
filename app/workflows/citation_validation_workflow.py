"""Citation Validation workflow.

Wraps :func:`app.domain.citation_validation.validate_citations` with the only
LLM step it needs — a *grounding* judgement. The model proposes; it never
touches the database. If the model is unavailable the workflow still returns a
report (the three deterministic dimensions) with a ``llm_unavailable`` flag.
"""
from __future__ import annotations

import asyncio
import json

from ..domain.citation_validation import CitationReport, validate_citations


def _build_llm():
    """Closure that calls the configured chat model and returns its text."""
    from ..config import runtime
    from ..llm import _client

    def _call(prompt: str) -> str:
        client = _client()
        response = client.chat.completions.create(
            model=runtime()['openai_model'], temperature=0.0,
            response_format={'type': 'json_object'},
            messages=[{'role': 'system',
                        'content': '你是一名严谨的引用校验器，只输出 JSON。'},
                      {'role': 'user', 'content': prompt}])
        return response.choices[0].message.content or ''
    return _call


async def validate_citations_async(question: str, answer: str, citations: list[dict],
                                   *, conn=None) -> CitationReport:
    llm = _build_llm()
    return await asyncio.to_thread(validate_citations, question, answer, citations,
                                   llm=llm, conn=conn)


def validate_citations_sync(question: str, answer: str, citations: list[dict],
                            *, conn=None) -> CitationReport:
    """Synchronous entry point for the API (runs the model call in a thread)."""
    try:
        return asyncio.run(validate_citations_async(question, answer, citations, conn=conn))
    except RuntimeError:
        # An event loop is already running (e.g. interactive/embedded) — fall back.
        llm = _build_llm()
        return validate_citations(question, answer, citations, llm=llm, conn=conn)
