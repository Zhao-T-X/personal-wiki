"""The agent-driven knowledge extraction pipeline.

Extraction is not a raw chat completion any more: it runs on an AgentScope
``Agent`` that owns the reasoning-acting loop, carries the Knowledge Extraction
skill contract in its prompt, can load a registry reference on demand through
``read_skill_reference``, and must return its answer through the
structured-output contract defined below.

The Pydantic models mirror ``schemas/extraction.schema.json``. Because
AgentScope delivers the schema through tool calling, the closed vocabularies
(``claim_type``, ``polarity``, ``modality``, entity/event/question types) are
enforced at the protocol level instead of relying on prompt wording alone.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from agentscope.agent import Agent
from agentscope.message import UserMsg

from .base import build_agent
from ..config import runtime
from ..context.extraction_context import MODE_PREFETCH, plan_extraction_context
from ..prompt_profiles import compose_prompt
from ..runlog import current_run
from ..tools.skill_tools import read_skill_reference

EntityType = Literal[
    "Person",
    "Organization",
    "Product",
    "Software",
    "Technology",
    "Method",
    "Concept",
    "Theory",
    "Dataset",
    "Model",
    "Standard",
    "Protocol",
    "Resource",
    "Location",
]
ClaimType = Literal[
    "factual",
    "definitional",
    "causal",
    "comparative",
    "evaluative",
    "predictive",
    "normative",
    "hypothetical",
]
Polarity = Literal["positive", "negative"]
ObjectKind = Literal["entity", "concept", "literal", "unknown"]
TemporalSignal = Literal[
    "new",
    "current",
    "former",
    "previous",
    "prior",
    "existing",
    "next",
    "future",
    "upcoming",
    "incoming",
    "outgoing",
    "successor",
    "predecessor",
    "past",
    "interim",
    "acting",
    "designated",
]
Modality = Literal[
    "asserted",
    "possible",
    "probable",
    "capable",
    "necessary",
    "recommended",
]
EventType = Literal[
    "creation",
    "development",
    "release",
    "publication",
    "deployment",
    "acquisition",
    "merger",
    "migration",
    "training",
    "evaluation",
    "experiment",
    "update",
    "decision",
    "announcement",
    "meeting",
    "failure",
    "incident",
    "other",
]
EventStatus = Literal[
    "planned",
    "ongoing",
    "completed",
    "cancelled",
    "failed",
    "unknown",
]
TimePrecision = Literal[
    "exact",
    "day",
    "month",
    "year",
    "range",
    "relative",
    "unknown",
]
IdeaStatus = Literal[
    "candidate",
    "accepted",
    "implemented",
    "rejected",
    "archived",
]
QuestionType = Literal[
    "knowledge",
    "research",
    "design",
    "implementation",
    "evaluation",
]
QuestionStatus = Literal[
    "open",
    "answered",
    "partially_answered",
    "resolved",
    "rejected",
    "archived",
]


class _Strict(BaseModel):
    """Base model that rejects unknown fields, matching the JSON Schema."""

    model_config = ConfigDict(extra="forbid")


class Entity(_Strict):
    name: str = Field(min_length=1)
    types: list[EntityType] = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    properties: dict = Field(default_factory=dict)


class Claim(_Strict):
    subject: str = Field(min_length=1)
    predicate: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    object: Optional[str] = None
    # The LLM's own verdict on what the object *is* (Step 10). Code validates the value
    # and guards the unambiguous cases; it never re-guesses this from keywords.
    object_kind: Optional[ObjectKind] = None
    # Temporal/evolution meaning ("new", "former", "current") is carried here,
    # never inside the predicate name (ONTOLOGY MUTATION POLICY; ADR-011).
    temporal_signal: Optional[TemporalSignal] = None
    claim_type: ClaimType
    polarity: Polarity
    modality: Modality
    content: Optional[str] = None
    context: dict = Field(default_factory=dict)
    confidence: float = Field(ge=0, le=1)
    source_chunk: str = Field(min_length=1)
    evidence_quote: str = Field(min_length=1)


class EventTime(_Strict):
    start: Optional[str] = None
    end: Optional[str] = None
    precision: TimePrecision = "unknown"


class Event(_Strict):
    event_type: EventType
    description: str = Field(min_length=1)
    participants: list[str] = Field(default_factory=list)
    time: EventTime = Field(default_factory=EventTime)
    location: Optional[str] = None
    status: EventStatus = "unknown"
    confidence: float = Field(ge=0, le=1)
    source_chunk: str = Field(min_length=1)
    evidence_quote: str = Field(min_length=1)


class Idea(_Strict):
    content: str = Field(min_length=1)
    status: IdeaStatus = "candidate"
    confidence: float = Field(ge=0, le=1)
    source_chunk: str = Field(min_length=1)
    evidence_quote: str = Field(min_length=1)


class Question(_Strict):
    content: str = Field(min_length=1)
    question_type: QuestionType = "knowledge"
    status: QuestionStatus = "open"
    confidence: float = Field(ge=0, le=1)
    source_chunk: str = Field(min_length=1)
    evidence_quote: str = Field(min_length=1)


class Extraction(BaseModel):
    """The structured extraction result the agent must produce.

    Relations are intentionally absent: they are derived deterministically
    from validated Claims by the application.
    """

    entities: list[Entity] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    ideas: list[Idea] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)


class Detection(BaseModel):
    """Pass 1 output: which knowledge kinds a chunk batch is worth extracting.

    All five flags default to False, so a model that omits one is read as
    "nothing of that kind here" instead of failing the whole pass.
    """

    entities: bool = False
    claims: bool = False
    events: bool = False
    ideas: bool = False
    questions: bool = False


# Canonical order of the five knowledge kinds; shared with the schema contract.
KIND_KEYS: tuple[str, ...] = ("entities", "claims", "events", "ideas", "questions")


def wanted_kinds(detection: dict | Detection | None) -> set[str]:
    """The kinds Pass 1 judged present (True); every other kind is skipped."""
    if detection is None:
        return set()
    if isinstance(detection, BaseModel):
        detection = detection.model_dump()
    if not isinstance(detection, dict):
        return set()
    return {key for key in KIND_KEYS if bool(detection.get(key))}


# Which skill references ship inside every extraction prompt.
#
# This list used to be empty: the closed vocabularies were believed to ship as the
# structured-output schema. They do not — the schema constrains *shapes* (entity types,
# object_kind, claim_type are Literal enums) but `predicate` is a free-form pattern the
# compiler resolves afterwards. The live semantic smoke showed the consequence: asked for
# a CEO statement the model answered `is`, because `is` was the only predicate it could
# see. It cannot select from a vocabulary it was never given.
#
# claim-predicates.md is therefore inlined (~0.45k tokens). entity-types.md (1.4k) and
# extraction-v2.md (1.8k) stay on demand: their rules are either already enforced by the
# schema or summarised in SKILL.md, and inlining both would triple the prompt for no
# measured gain. Rollback: set this list back to [] and watch Predicate Semantic
# Accuracy in `pytest -m smoke`.
EXTRACTION_REFERENCES: list[str] = ['claim-predicates.md']

# The extraction agent's ENTIRE tool surface.
#
# It only needs registry access. It must not call knowledge-base query tools while
# extracting a document — retrieving what we already know would let the model answer
# from memory instead of from the source.
#
# Step 14 removed two tools from this list. `Skill` (AgentScope's skill viewer, added
# by the skill loader) and `list_skills` were sent on every request of every extraction
# step, and the captured real requests show the model never called either one: the only
# registry access the extraction contract uses is `read_skill_reference`. Dropping the
# loader also drops the `<agent-skills>` catalogue AgentScope appends to the system
# prompt — text that advertised the removed tool and shipped an absolute local skill
# path to the provider on every call.
#
# What stays, and why:
#   read_skill_reference     the model's only way to pull claim-predicates.md /
#                            entity-types.md on demand. The live semantic runs show this
#                            access is what makes `has_ceo` and `concept` come out right.
#   GenerateStructuredOutput not listed here: AgentScope adds it from the structured
#                            schema passed to `reply()`. It is the extraction output
#                            contract and cannot be removed (Step 12 §4).
EXTRACTION_TOOLS = [read_skill_reference]

# Prefetch registers no function tool at all (Step 15). That is not a subtle preference —
# it is what makes "one provider call" structural rather than hoped for: with nothing
# callable but `GenerateStructuredOutput` (added by AgentScope from the schema), the model
# has no way to open a second round. It also removes the last chance of a silent fallback
# (§19): there is no `read_skill_reference` for the prefetch path to fall back *to*.
PREFETCH_TOOLS: list = []


def extraction_context_mode() -> str:
    """``agentic`` (default) or ``prefetch`` — see ``app/context/extraction_context.py``."""
    return str(runtime().get('extraction_context_mode') or 'agentic').strip().lower()


def extraction_prompt_inputs(payload: str = '', *, mode: str | None = None) -> dict:
    """The exact context inputs ``build_extraction_agent`` uses for one payload.

    Exposed rather than inlined so the cost tooling measures the composition the
    production path actually assembles. Re-deriving it in a script is how a report ends
    up describing a prompt no call ever sends.
    """
    resolved = (mode or extraction_context_mode()).strip().lower()
    if resolved == MODE_PREFETCH:
        plan = plan_extraction_context(payload)
        return {'mode': resolved, 'references': [], 'tools': PREFETCH_TOOLS,
                'extra_items': plan.items(), 'plan': plan}
    return {'mode': resolved, 'references': EXTRACTION_REFERENCES, 'tools': EXTRACTION_TOOLS,
            'extra_items': None, 'plan': None}


def _agent_for(inputs: dict) -> Agent:
    """Build the extraction agent from a prepared input set (one source of truth)."""
    return build_agent(
        "ExtractionAgent",
        compose_prompt("extractor", references=inputs['references'], tools=inputs['tools'],
                       extra_items=inputs['extra_items']),
        tools=inputs['tools'],
        skills=False,
    )


def build_extraction_agent(payload: str = '', *, mode: str | None = None) -> Agent:
    """Create the extraction agent for one payload, in the configured context mode.

    ``agentic`` — the agent fetches registries itself through ``read_skill_reference``
    (one reference round, two provider calls per step).

    ``prefetch`` — the Context Planner selects the minimal semantic context for *this*
    payload up front, and the agent answers in one call. The references are still
    delivered; they are just not re-delivered at full price.

    No skill loader in either mode: the Context Runtime already puts the skill contract in
    this prompt, and nothing here opens a skill through the viewer tool.
    """
    return _agent_for(extraction_prompt_inputs(payload, mode=mode))


async def extract_structured(payload: str, *, mode: str | None = None) -> dict:
    """Run one extraction pass and return the structured output as a dict.

    In ``agentic`` mode the agent may load further reference documents before it commits
    to the required structured output (a second provider call). In ``prefetch`` mode the
    context it needs is already in the prompt, so the same call returns the output
    directly. ``mode`` overrides the configured one for one call, which is what the A/B
    harness uses; production reads ``extraction_context_mode``.

    Which context the planner chose rides on the run, not in the return value: the call
    stays ``extract_structured(payload) -> dict`` for every existing caller, while a
    regression run can still report what each case was actually given.
    """
    inputs = extraction_prompt_inputs(payload, mode=mode)
    run = current_run()
    if run is not None and inputs['plan'] is not None:
        run.extraction_context_plan = inputs['plan'].to_dict()
    agent = _agent_for(inputs)
    from ..agent_usage import collect, context_length, record
    context_before = context_length(agent)
    message = await agent.reply(
        UserMsg(name="user", content=payload),
        structured_schema=Extraction,
    )
    # The step record is owned by app/llm.py, so hand the provider usage over.
    record(collect(agent, message, context_before))
    data = getattr(message, "structured_output", None)
    if not isinstance(data, dict):
        raise RuntimeError("Extraction agent did not return structured output")
    return data


# Pass 1 is deliberately tiny: no tools, no skill catalogue, no references. It
# only triages which of the five kinds a batch contains, so paying for the full
# extraction prompt (and a reasoning-acting loop) on a chunk that holds nothing
# worth extracting is pure waste (task §12).
DETECTION_SYSTEM_PROMPT = (
    "You are a fast triage classifier for a personal knowledge base. "
    "Read the text chunks and report which knowledge kinds they contain.\n"
    "- entities: named people, organizations, products, software, technologies, "
    "methods, concepts, theories, datasets, models, standards, protocols, "
    "resources or locations.\n"
    "- claims: asserted facts, definitions, causes, comparisons, evaluations or "
    "predictions about something.\n"
    "- events: dated or sequential occurrences (creation, release, deployment, "
    "migration, meeting, failure, ...).\n"
    "- ideas: proposals, suggestions or design thoughts that are not yet facts.\n"
    "- questions: explicit open questions the text asks.\n"
    "Set a kind to true only when the text really contains it, otherwise false. "
    "Do not extract the content itself."
)


def build_detection_agent() -> Agent:
    """Create the minimal Pass 1 agent: no tools, no skills, a short prompt."""
    cfg = runtime()
    if not cfg.get("openai_api_key"):
        raise RuntimeError("LLM API key is not configured")
    from agentscope.credential import OpenAICredential
    from agentscope.model import OpenAIChatModel

    credential = OpenAICredential(
        api_key=cfg["openai_api_key"],
        base_url=cfg["openai_base_url"],
    )
    model = OpenAIChatModel(
        credential=credential,
        model=cfg["openai_model"],
        stream=False,
    )
    # No toolkit argument: Agent falls back to an empty Toolkit, so neither the
    # knowledge-base tools nor the skill catalogue reach the model call.
    return Agent(
        name="DetectionAgent",
        system_prompt=DETECTION_SYSTEM_PROMPT,
        model=model,
    )


async def detect_structured(payload: str) -> dict:
    """Pass 1: return ``{kind: bool}`` for the five knowledge kinds.

    Runs on a minimal agent with no tools and no skill references. Callers must
    treat a failure as "unknown" and fall back to a full extraction rather than
    dropping the batch (see ``app/llm.py::_detect_one``).
    """
    agent = build_detection_agent()
    from ..agent_usage import collect, context_length, record
    context_before = context_length(agent)
    message = await agent.reply(
        UserMsg(name="user", content=payload),
        structured_schema=Detection,
    )
    # The step record is owned by app/llm.py, so hand the provider usage over.
    record(collect(agent, message, context_before))
    data = getattr(message, "structured_output", None)
    if not isinstance(data, dict):
        raise RuntimeError("Detection agent did not return structured output")
    return {key: bool(data.get(key, False)) for key in KIND_KEYS}


def scope_hint(wanted: set[str]) -> str:
    """Instruction appended to a Pass 2 payload to limit what gets extracted."""
    kinds = ", ".join(key for key in KIND_KEYS if key in wanted)
    return (
        "[PASS 2 SCOPE]\n"
        f"A lightweight pre-scan found only these knowledge kinds in this batch: {kinds}.\n"
        "Extract ONLY those kinds and return every other array as an empty list."
    )
