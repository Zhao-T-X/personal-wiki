"""Repository layer: the only place that speaks SQL (docs/adr/ADR-004).

Business code depends on these classes, never on ``app.db.connect`` directly.
Every repository accepts an optional ``conn`` so it can join a caller's
transaction (unit-of-work) or own its own connection.
"""
from .base import Repository
from .catalog_repo import CatalogRepository
from .claim_repo import ClaimRepository
from .curation_repo import CurationRepository
from .document_repo import DocumentRepository
from .entity_repo import EntityRepository
from .event_repo import EventRepository
from .evidence_repo import EvidenceRepository
from .idea_repo import IdeaRepository
from .operation_repo import OperationRepository
from .question_repo import QuestionRepository
from .relation_repo import RelationRepository
from .research_repo import ResearchRepository
from .run_repo import RunRepository
from .suppression_repo import SuppressionRepository

__all__ = [
    'Repository',
    'CatalogRepository',
    'ClaimRepository',
    'CurationRepository',
    'DocumentRepository',
    'EntityRepository',
    'EventRepository',
    'EvidenceRepository',
    'IdeaRepository',
    'OperationRepository',
    'QuestionRepository',
    'RelationRepository',
    'ResearchRepository',
    'RunRepository',
    'SuppressionRepository',
]
