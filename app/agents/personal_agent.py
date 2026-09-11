from .base import build_agent, run_agent, DEFAULT_TOOLS
from ..prompt_profiles import compose_prompt

def build(history_summary: str | None = None, history: list[dict] | None = None):
    return build_agent('PersonalAgent', compose_prompt(
        'personal', tools=DEFAULT_TOOLS,
        history_summary=history_summary, history=history))

async def ask(message: str, *, history_summary: str | None = None,
              history: list[dict] | None = None) -> str:
    return await run_agent(build(history_summary, history), message)
