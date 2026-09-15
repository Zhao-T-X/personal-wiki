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


class MergeRequest(BaseModel):
    """A *confirmed* entity merge: which row survives, which one is absorbed.

    Two ids rather than a direction word, because which way round it goes decides
    which name the surviving entity keeps — and that is the user's choice, not an
    inference the backend should make from creation order.
    """
    keep_id: str = Field(min_length=1)
    drop_id: str = Field(min_length=1)


class IntentRequest(BaseModel):
    """One sentence, and whatever the user is currently looking at.

    ``context_claim_id`` is what makes 「这个不对，应该是……」 work: the subject is
    already on screen, so the sentence does not have to name it — and the user must not
    have to retype what the app can see.
    """
    text: str = ''
    context_claim_id: str | None = None


class CurationDecisionRequest(BaseModel):
    """A human's decision about an entity pair — currently only "not the same".

    Two ids, not names: the decision has to survive a rename or a new alias, or the
    system would ask the same question again the next time a name changes.
    """
    entity_id_a: str = Field(min_length=1)
    entity_id_b: str = Field(min_length=1)
    reason: str = ''


class ObjectLinkRequest(BaseModel):
    """Backfill free-text objects onto entities that already exist.

    ``min_confidence`` gates how much the system is allowed to do: ``high`` means only
    exact name/alias matches, and that is also where it stops — a ``medium`` proposal
    carries candidates but deliberately no chosen target, so a batch tool has nothing
    it could apply for one. Those are confirmed one at a time, by a person, through
    ``ObjectLinkConfirmRequest``. There is no setting that lets either path create an
    entity — matching a literal to a new entity is a different (and much riskier)
    operation than linking it to one that already exists.
    """
    min_confidence: str = 'high'
    limit: int = 200


class ObjectLinkConfirmRequest(BaseModel):
    """A person saying which entity a piece of text refers to.

    The two ids *are* the decision. The suggestion tier reports candidates without a
    target precisely so that this choice has to come from a human, which means the
    target is taken as given: nothing is re-matched or re-ranked from the text here,
    because that would replace the one input the automatic path never had.

    The ontology still has the last word on the pairing, so the request can be refused.
    """
    claim_id: str = Field(min_length=1)
    entity_id: str = Field(min_length=1)


class SuppressionRequest(BaseModel):
    """Stop offering one maintenance suggestion.

    The claim *and* the literal identify the notice, and the literal is sent back as it
    was shown. The suppression key is derived from it, and the next scan compares that
    derived key — so an edited literal is correctly treated as a new question instead of
    staying dismissed under an old one.
    """
    claim_id: str = Field(min_length=1)
    object_text: str = Field(min_length=1)
    reason: str = ''


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
