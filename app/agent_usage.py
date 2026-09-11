"""Provider-reported token usage for agent replies.

AgentScope attaches `usage` (input_tokens/output_tokens) to the messages it stores
in the agent context during a reply, so the real cost of a ReAct reply - every
model call and tool round, not just the last one - can be summed from the context
diff.

`app/llm.py` creates the step record while `app/agents/extraction_agent.py` owns
the reply, so usage travels back through a contextvar channel instead of changing
`extract_structured`'s return type (existing callers and test fakes depend on a
plain dict).
"""
from __future__ import annotations

import contextvars

_pending: contextvars.ContextVar[list[dict] | None] = contextvars.ContextVar('agent_usage', default=None)


def context_length(agent) -> int | None:
    """Size of the agent context before a reply, or None when not inspectable."""
    try:
        return len(agent.state.context)
    except Exception:
        return None


def collect(agent, result, context_index_before: int | None) -> dict:
    """Sum provider usage for one reply.

    Prefers the messages appended to the agent context during the reply (that
    covers every model call in the ReAct loop); falls back to the returned
    message when the context is not inspectable.
    """
    messages = []
    try:
        if context_index_before is not None:
            messages = list(agent.state.context)[context_index_before:]
    except Exception:
        messages = []
    if not messages and result is not None:
        messages = [result]

    prompt_tokens = 0
    completion_tokens = 0
    for message in messages:
        usage = getattr(message, 'usage', None)
        if usage is None:
            continue
        prompt_tokens += int(getattr(usage, 'input_tokens', 0) or 0)
        completion_tokens += int(getattr(usage, 'output_tokens', 0) or 0)

    return {
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
        'usage_source': 'provider' if (prompt_tokens or completion_tokens) else 'unavailable',
    }


def record(usage: dict | None) -> None:
    """Hand a usage dict to whoever records the current step."""
    _pending.set([usage] if usage else [])


def take() -> dict | None:
    """Read and clear the usage recorded for the current step."""
    pending = _pending.get()
    _pending.set(None)
    return pending[0] if pending else None
