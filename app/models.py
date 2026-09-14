from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_type: str = 'note'
    source_uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AskRequest(BaseModel):
    question: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=20)


class CitationValidateRequest(BaseModel):
    """Validate a QA answer against its citations (Citation Validation)."""
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    citations: list[dict] = Field(default_factory=list)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)
    semantic: bool = True


class StatusUpdate(BaseModel):
    status: str


class EntityUpdate(BaseModel):
    description: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    type: str | None = None
    name: str | None = None
    aliases: list[str] | None = None


class EntityCreate(BaseModel):
    name: str = Field(min_length=1)
    type: str = 'Resource'
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)


class IdeaCreate(BaseModel):
    content: str = Field(min_length=1)
    status: str = 'candidate'
    source_document_id: str | None = None


class QuestionCreate(BaseModel):
    content: str = Field(min_length=1)
    status: str = 'open'
    source_document_id: str | None = None


class EventCreate(BaseModel):
    event_type: str = 'other'
    description: str = Field(min_length=1)
    status: str = 'unknown'
    location: str | None = None
    time: dict[str, Any] = Field(default_factory=dict)
    participants: list[str] = Field(default_factory=list)
    source_document_id: str | None = None


class ResearchCreate(BaseModel):
    question_text: str = Field(min_length=1)
    question_id: str | None = None


class CorrectionRequest(BaseModel):
    """A one-sentence correction to plan (no writes)."""
    text: str = Field(min_length=1)


class CorrectionApplyRequest(BaseModel):
    """A confirmed correction to execute through the CORRECT operation."""
    text: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    object: str = ''
    polarity: str = 'positive'
    confidence: float | None = None
    relationship: str | None = None
    related_claim_id: str | None = None
    apply_supersede: bool = False
