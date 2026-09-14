"""Domain layer: business rules. No FastAPI, no AgentScope, no SQL
(see docs/adr/ADR-002 and docs/adr/ADR-009).

Two things live here:
- ``claim_state`` — the single definition of "current knowledge";
- ``operations`` — the only sanctioned way to change knowledge.
"""
from .claim_state import SUPERSEDED, current, history, is_current, resolve
from .operations import (KINDS, OperationError, OperationRequest, OperationResult,
                         register_operation, registered_operations, run)

__all__ = [
    'SUPERSEDED', 'current', 'history', 'is_current', 'resolve',
    'KINDS', 'OperationError', 'OperationRequest', 'OperationResult',
    'register_operation', 'registered_operations', 'run',
]
