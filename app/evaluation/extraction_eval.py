"""Extraction-quality evaluation over a hand-checked golden set (task §16/§17).

Flow:

    golden.json          ExtractionCase (source_text, model_draft, expected_*)
      -> compile_draft   the real Knowledge Compilation Pipeline scores every
                         claim of the draft; entities are canonicalised through
                         ``canonical_entity_type``; nothing is ever raised
      -> evaluate_case   per-case precision/recall/evidence/ontology metrics
      -> evaluate_dataset micro-averaged report over the whole set

Design choices that matter for reproducibility:

* The pipeline decides what is knowable - the harness never invents a
  predicate or "fixes" a draft. An unresolved candidate stays unresolved and
  therefore contributes to neither the predicted claim set nor a violation.
* Matching is deterministic and string-only: names go through ``normalize_name``
  (whitespace-collapsed, case-folded); predicates go through
  ``normalize_predicate``; entity types are compared as sets.
* Micro-averaging (sum tp/fp/fn, then divide) is used at dataset level so a
  case with an empty class never forces a meaningless ``0.0`` into the mean.

Pure and offline: no FastAPI, no AgentScope, no sqlite3, no LLM (ADR-011).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..domain.compiler import COMPILED, KnowledgeCompiler
from ..ontology import (CLAIM_PREDICATES, canonical_entity_type, normalize_name,
                        normalize_predicate)
from .metrics import precision, recall

# Keys of the six reported metrics, kept in one place so per-case and aggregate
# reports can never drift apart.
METRIC_KEYS = (
    'entity_precision', 'entity_recall',
    'claim_precision', 'claim_recall',
    'evidence_accuracy', 'ontology_violation_rate',
)


@dataclass
class ExtractionCase:
    """One golden sample: the source text and the draft a model produced for it."""

    id: str
    source_text: str
    model_draft: dict
    expected_entities: list[dict] = field(default_factory=list)
    expected_claims: list[dict] = field(default_factory=list)


@dataclass
class CompiledDraft:
    """The draft after the pipeline: what survived, and what was refused."""

    raw_claims: list[dict] = field(default_factory=list)
    results: list[Any] = field(default_factory=list)  # CompileResult | None
    entities: dict[str, set[str]] = field(default_factory=dict)
    entity_type_violations: int = 0

    @property
    def compiled_pairs(self) -> list[tuple[dict, Any]]:
        """(raw_claim, CompileResult) for every claim the pipeline compiled."""
        pairs: list[tuple[dict, Any]] = []
        for raw, result in zip(self.raw_claims, self.results):
            if result is not None and result.status == COMPILED and result.claim is not None:
                pairs.append((raw, result))
        return pairs

    @property
    def compiled_claims(self) -> list[Any]:
        return [result.claim for _, result in self.compiled_pairs]


@dataclass
class CaseResult:
    """Per-case scores plus the raw counts behind them."""

    id: str
    entity_precision: float
    entity_recall: float
    claim_precision: float
    claim_recall: float
    evidence_accuracy: float
    ontology_violation_rate: float
    entity_tp: int = 0
    entity_fp: int = 0
    entity_fn: int = 0
    claim_tp: int = 0
    claim_fp: int = 0
    claim_fn: int = 0
    compiled_claims: int = 0
    evidence_hits: int = 0
    ontology_violations: int = 0
    entity_type_violations: int = 0

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            **{key: getattr(self, key) for key in METRIC_KEYS},
            'entity_tp': self.entity_tp,
            'entity_fp': self.entity_fp,
            'entity_fn': self.entity_fn,
            'claim_tp': self.claim_tp,
            'claim_fp': self.claim_fp,
            'claim_fn': self.claim_fn,
            'compiled_claims': self.compiled_claims,
            'evidence_hits': self.evidence_hits,
            'ontology_violations': self.ontology_violations,
            'entity_type_violations': self.entity_type_violations,
        }


# --- loading -----------------------------------------------------------------

def load_cases(path: str | Path) -> list[ExtractionCase]:
    """Load ``{"cases": [...]}`` from ``path`` into ``ExtractionCase`` objects."""
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    cases: list[ExtractionCase] = []
    for item in raw.get('cases', []) or []:
        cases.append(ExtractionCase(
            id=str(item.get('id') or ''),
            source_text=str(item.get('source_text') or ''),
            model_draft=item.get('model_draft') or {},
            expected_entities=list(item.get('expected_entities') or []),
            expected_claims=list(item.get('expected_claims') or []),
        ))
    return cases


def _claim_key(subject: Any, predicate: Any, obj: Any) -> tuple[str, str, str]:
    return (
        normalize_name(subject or ''),
        normalize_predicate(predicate or ''),
        normalize_name(obj or ''),
    )


# --- compile -----------------------------------------------------------------

def compile_draft(case: ExtractionCase, *,
                  compiler: KnowledgeCompiler | None = None) -> CompiledDraft:
    """Run every draft claim through the real compiler. Never raises.

    Entities are canonicalised with ``canonical_entity_type``. An unregistered
    type is not an error: it is counted in ``entity_type_violations`` and the
    entity is dropped from the predicted set (it has no ontology grounding).
    A draft claim whose compilation fails for any reason is recorded as
    ``None`` and simply is not part of the predicted set.
    """
    compiler = compiler or KnowledgeCompiler()

    entity_type_violations = 0
    index: dict[str, dict] = {}
    entities: dict[str, set[str]] = {}
    draft_entities = case.model_draft.get('entities') if isinstance(case.model_draft, dict) else None
    for entity in draft_entities or []:
        if not isinstance(entity, dict):
            continue
        types: set[str] = set()
        for declared in entity.get('types') or []:
            try:
                types.add(canonical_entity_type(declared))
            except ValueError:
                entity_type_violations += 1
        if not types:
            continue
        key = normalize_name(entity.get('name') or '')
        if not key:
            continue
        entities[key] = types
        index[key] = {'types': sorted(types)}

    raw_claims: list[dict] = []
    results: list[Any] = []
    draft_claims = case.model_draft.get('claims') if isinstance(case.model_draft, dict) else None
    for raw in draft_claims or []:
        if not isinstance(raw, dict):
            raw = {}
        raw_claims.append(raw)
        try:
            results.append(compiler.compile_extraction_claim(raw, entities=index))
        except Exception:  # pragma: no cover - the compiler is documented not to raise
            results.append(None)

    return CompiledDraft(
        raw_claims=raw_claims,
        results=results,
        entities=entities,
        entity_type_violations=entity_type_violations,
    )


# --- scoring -----------------------------------------------------------------

def _entity_counts(predicted: dict[str, set[str]],
                   expected_list: list[dict]) -> tuple[int, int, int]:
    expected: dict[str, set[str]] = {}
    for item in expected_list or []:
        if not isinstance(item, dict):
            continue
        key = normalize_name(item.get('name') or '')
        expected[key] = {str(t).strip() for t in (item.get('types') or []) if str(t).strip()}

    matched = 0
    for key, predicted_types in predicted.items():
        expected_types = expected.get(key)
        if expected_types is not None and expected_types <= predicted_types:
            matched += 1
    return matched, len(predicted) - matched, len(expected) - matched


def _claim_counts(predicted: set[tuple[str, str, str]],
                  expected_list: list[dict]) -> tuple[int, int, int]:
    expected = {
        _claim_key(item.get('subject'), item.get('predicate'), item.get('object'))
        for item in expected_list or [] if isinstance(item, dict)
    }
    matched = len(predicted & expected)
    return matched, len(predicted) - matched, len(expected) - matched


def _evidence_counts(case: ExtractionCase, draft: CompiledDraft) -> tuple[int, int]:
    haystack = normalize_name(case.source_text)
    hits = 0
    pairs = draft.compiled_pairs
    for raw, _result in pairs:
        quote = normalize_name(raw.get('evidence_quote') or '')
        if quote and quote in haystack:
            hits += 1
    return hits, len(pairs)


def _ontology_violation_counts(draft: CompiledDraft) -> tuple[int, int]:
    pairs = draft.compiled_pairs
    violations = sum(1 for _raw, result in pairs
                     if result.claim.predicate not in CLAIM_PREDICATES)
    return violations, len(pairs)


def evaluate_case(case: ExtractionCase, *,
                  compiler: KnowledgeCompiler | None = None) -> CaseResult:
    """Score one case against its expectations."""
    draft = compile_draft(case, compiler=compiler)

    entity_tp, entity_fp, entity_fn = _entity_counts(draft.entities, case.expected_entities)

    predicted_claims = {
        _claim_key(result.claim.subject, result.claim.predicate, result.claim.object)
        for _raw, result in draft.compiled_pairs
    }
    claim_tp, claim_fp, claim_fn = _claim_counts(predicted_claims, case.expected_claims)

    evidence_hits, compiled_total = _evidence_counts(case, draft)
    ontology_violations, _ = _ontology_violation_counts(draft)

    def ratio(numerator: int, denominator: int, *, empty: float) -> float:
        return numerator / denominator if denominator else empty

    return CaseResult(
        id=case.id,
        entity_precision=precision(entity_tp, entity_fp),
        entity_recall=recall(entity_tp, entity_fn),
        claim_precision=precision(claim_tp, claim_fp),
        claim_recall=recall(claim_tp, claim_fn),
        evidence_accuracy=ratio(evidence_hits, compiled_total, empty=1.0),
        ontology_violation_rate=ratio(ontology_violations, compiled_total, empty=0.0),
        entity_tp=entity_tp,
        entity_fp=entity_fp,
        entity_fn=entity_fn,
        claim_tp=claim_tp,
        claim_fp=claim_fp,
        claim_fn=claim_fn,
        compiled_claims=compiled_total,
        evidence_hits=evidence_hits,
        ontology_violations=ontology_violations,
        entity_type_violations=draft.entity_type_violations,
    )


def evaluate_dataset(cases: list[ExtractionCase], *,
                     compiler: KnowledgeCompiler | None = None) -> dict:
    """Micro-averaged report over a whole golden set.

    Totals are summed first and metrics derived from the sums, so that cases
    with an empty class do not distort the aggregate. The per-case results are
    returned under ``cases`` and their JSON form under ``case_details``.
    """
    compiler = compiler or KnowledgeCompiler()
    results = [evaluate_case(case, compiler=compiler) for case in cases]

    totals = {
        'entity_tp': sum(r.entity_tp for r in results),
        'entity_fp': sum(r.entity_fp for r in results),
        'entity_fn': sum(r.entity_fn for r in results),
        'claim_tp': sum(r.claim_tp for r in results),
        'claim_fp': sum(r.claim_fp for r in results),
        'claim_fn': sum(r.claim_fn for r in results),
        'compiled_claims': sum(r.compiled_claims for r in results),
        'evidence_hits': sum(r.evidence_hits for r in results),
        'ontology_violations': sum(r.ontology_violations for r in results),
        'entity_type_violations': sum(r.entity_type_violations for r in results),
    }

    def ratio(numerator: int, denominator: int, *, empty: float) -> float:
        return numerator / denominator if denominator else empty

    report = {
        'entity_precision': precision(totals['entity_tp'], totals['entity_fp']),
        'entity_recall': recall(totals['entity_tp'], totals['entity_fn']),
        'claim_precision': precision(totals['claim_tp'], totals['claim_fp']),
        'claim_recall': recall(totals['claim_tp'], totals['claim_fn']),
        'evidence_accuracy': ratio(totals['evidence_hits'], totals['compiled_claims'], empty=1.0),
        'ontology_violation_rate': ratio(totals['ontology_violations'], totals['compiled_claims'], empty=0.0),
        'totals': totals,
        'case_count': len(results),
        'cases': results,
        'case_details': [r.to_dict() for r in results],
    }
    return report
