"""The agent-driven knowledge extraction pipeline.

Extraction is not a raw chat completion any more: it runs on an AgentScope
``Agent`` that owns the reasoning-acting loop, can open the Knowledge
Extraction skill, can load registries on demand through the skill tools, and
must return its answer through the structured-output contract defined below.

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
from ..prompt_profiles import compose_prompt
from ..tools.skill_tools import list_skills, read_skill_reference

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


# References are no longer inlined into every extraction call. The closed
# vocabularies already ship as the structured-output contract (Pydantic Literal
# enums, which the provider receives as a machine schema), so inlining
# entity-types.md + claim-predicates.md duplicated ~5.8 KB of context per call.
# The agent pulls a reference through `read_skill_reference` only when it needs
# the detailed rules. Rollback: list the file names here again to re-inline them.
EXTRACTION_REFERENCES: list[str] = []

# The extraction agent only needs skill access. It must not call knowledge-base
# query tools while extracting a document.
_SKILL_TOOLS = [list_skills, read_skill_reference]


def build_extraction_agent() -> Agent:
    """Create the extraction agent with skill access and the v2 contract."""
    return build_agent(
        "ExtractionAgent",
        compose_prompt("extractor", references=EXTRACTION_REFERENCES, tools=_SKILL_TOOLS),
        tools=_SKILL_TOOLS,
    )


async def extract_structured(payload: str) -> dict:
    """Run one extraction pass and return the structured output as a dict.

    The agent may inspect the Knowledge Extraction skill and load further
    reference documents before it commits to the required structured output.
    """
    agent = build_extraction_agent()
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
