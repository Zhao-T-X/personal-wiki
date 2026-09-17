from __future__ import annotations
import json
from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIChatModel
from agentscope.message import UserMsg
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.skill import LocalSkillLoader
from agentscope.tool import FunctionTool, Toolkit

from ..config import runtime
from ..skills import SKILLS_DIR
from ..tools.knowledge_tools import (get_entities, get_entity, get_entity_graph,
                                     search_knowledge)
from ..tools.skill_tools import list_skills, read_skill_reference

DEFAULT_TOOLS = [
    search_knowledge,
    get_entities,
    get_entity,
    get_entity_graph,
    list_skills,
    read_skill_reference,
]

# LLM-Wiki tools are read-only, so they never require interactive confirmation.
_ALLOW = PermissionDecision(
    behavior=PermissionBehavior.ALLOW,
    message="LLM-Wiki read-only tools are always allowed.",
)


def build_toolkit(tools=None, *, skills: bool = True) -> Toolkit:
    """Create a Toolkit with the LLM-Wiki tools and (optionally) the local Agent Skills.

    Skills are registered through AgentScope's native skill loader, so the
    agent receives the skill catalogue in its system prompt and can open a
    skill contract with the built-in skill viewer tool.

    ``skills=False`` registers no loader at all. That removes two things the loader
    brings as a pair: the ``Skill`` viewer tool and the ``<agent-skills>`` catalogue
    block AgentScope appends to the system prompt. They must go together — the block
    instructs the model to use the ``Skill`` tool, so keeping the text without the tool
    would advertise something that no longer exists. The extraction agent opts out
    (Step 14): its skill contract is already inlined by the Context Runtime, and the
    only registry tool its contract actually calls is ``read_skill_reference``.
    """
    # NOTE: an explicit empty list means "no custom tools", so only None
    # falls back to the default tool set.
    functions = DEFAULT_TOOLS if tools is None else list(tools)
    return Toolkit(
        tools=[FunctionTool(fn, permission=_ALLOW) for fn in functions],
        skills_or_loaders=([LocalSkillLoader(directory=str(SKILLS_DIR), scan_subdir=True)]
                           if skills else []),
    )


def build_agent(name: str, sys_prompt: str, tools=None, *, skills: bool = True) -> Agent:
    cfg = runtime()
    if not cfg.get("openai_api_key"):
        raise RuntimeError("LLM API key is not configured")

    toolkit = build_toolkit(tools, skills=skills)

    credential = OpenAICredential(
        api_key=cfg["openai_api_key"],
        base_url=cfg["openai_base_url"],
    )
    model = OpenAIChatModel(
        credential=credential,
        model=cfg["openai_model"],
        stream=False,
    )

    # AgentScope 2.0 uses the unified Agent abstraction. It contains the
    # ReAct reasoning/acting loop and replaces the older ReActAgent wiring.
    return Agent(
        name=name,
        system_prompt=sys_prompt,
        model=model,
        toolkit=toolkit,
    )


async def run_agent(agent: Agent, message: str) -> str:
    from ..agent_usage import collect, context_length
    from ..runlog import record_run
    with record_run('agent', agent_role=agent.name) as run:
        with run.step('agent_reply', input_summary=message[:200]) as step:
            context_before = context_length(agent)
            result = await agent.reply(UserMsg(name="user", content=message))
            step.set_usage(collect(agent, result, context_before))
            text = None
            if hasattr(result, "get_text_content"):
                text = result.get_text_content()
            if not text:
                content = getattr(result, "content", result)
                text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
            step.output = text
            run.summary = {'question': message[:200], 'answer_chars': len(text)}
            return text
