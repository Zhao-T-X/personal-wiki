"""Domain layer: business rules. No FastAPI, no AgentScope, no SQL
(see docs/adr/ADR-002 and docs/adr/ADR-009).

Four things live here:
- ``claim_state`` — the single definition of "current knowledge";
- ``operations`` — the only sanctioned way to change knowledge;
- ``ontology_policy`` — the closed-ontology rule, stated once for the system;
- ``predicate_resolver`` / ``compiler`` — the Knowledge Compilation Pipeline,
  the only door from an LLM draft to canonical knowledge.
"""
from .claim_state import SUPERSEDED, current, history, is_current, resolve
from .compiler import (COMPILED, REJECTED, UNRESOLVED, CanonicalClaim, ClaimDraft,
                       CompileResult, KnowledgeCompiler)
from .ontology_policy import (ONTOLOGY_MUTATION_POLICY, ONTOLOGY_WRITE_SURFACE,
                              OntologyViolation, is_registered, ontology_kinds,
                              ontology_values, policy_text, require_registered)
from .operations import (KINDS, OperationError, OperationRequest, OperationResult,
                         register_operation, registered_operations, run)
from .predicate_resolver import (AMBIGUOUS, RESOLVED, TEMPORAL_SIGNALS,
                                 PredicateResolution, resolve_predicate,
                                 split_temporal_signal)
from .quality_gate import (ACCEPT, REJECT, REVIEW, QualityAssessment, QualitySignals,
                           evaluate as evaluate_quality)
from .query_router import (EVIDENCE_SYNTHESIS, FACT_LOOKUP, RESEARCH, ROUTES,
                           STRUCTURED_REASONING, QueryPlan, QuerySignals, classify)
from .refusal import (INSUFFICIENT_EVIDENCE, NON_REFUSAL, REFUSAL, UNKNOWN,
                      RefusalDecision, RefusalKind, classify_refusal)

__all__ = [
    'SUPERSEDED', 'current', 'history', 'is_current', 'resolve',
    'KINDS', 'OperationError', 'OperationRequest', 'OperationResult',
    'register_operation', 'registered_operations', 'run',
    'ONTOLOGY_MUTATION_POLICY', 'ONTOLOGY_WRITE_SURFACE', 'OntologyViolation',
    'is_registered', 'ontology_kinds', 'ontology_values', 'policy_text',
    'require_registered',
    'COMPILED', 'REJECTED', 'UNRESOLVED', 'CanonicalClaim', 'ClaimDraft',
    'CompileResult', 'KnowledgeCompiler',
    'AMBIGUOUS', 'RESOLVED', 'TEMPORAL_SIGNALS', 'PredicateResolution',
    'resolve_predicate', 'split_temporal_signal',
    'ACCEPT', 'REVIEW', 'REJECT', 'QualityAssessment', 'QualitySignals',
    'evaluate_quality',
    'FACT_LOOKUP', 'STRUCTURED_REASONING', 'EVIDENCE_SYNTHESIS', 'RESEARCH', 'ROUTES',
    'QueryPlan', 'QuerySignals', 'classify',
    'REFUSAL', 'NON_REFUSAL', 'INSUFFICIENT_EVIDENCE', 'UNKNOWN', 'RefusalDecision',
    'RefusalKind', 'classify_refusal',
]
