from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / 'schemas'

ENTITY_TYPES = {
    'Person', 'Organization', 'Product', 'Software', 'Technology', 'Method',
    'Concept', 'Theory', 'Dataset', 'Model', 'Standard', 'Protocol',
    'Resource', 'Location',
}
LEGACY_ENTITY_TYPES = {'person':'Person','organization':'Organization','place':'Location','concept':'Concept','project':'Resource'}


def _load_registry(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding='utf-8'))


RELATION_REGISTRY = _load_registry('relation-predicate-registry.json')
CLAIM_REGISTRY = _load_registry('claim-predicate-registry.json')
NORMALIZATION_RULES = _load_registry('relation-normalization-rules.json')

RELATION_TYPES = {x['predicate'] for x in RELATION_REGISTRY['relation_predicates']}
CLAIM_PREDICATES = set(CLAIM_REGISTRY['claim_predicates'])
KNOWLEDGE_STATUSES = {'draft','candidate','verified','rejected','archived','superseded'}
IDEA_STATUSES = {'candidate','accepted','implemented','rejected','archived'}
QUESTION_STATUSES = {'open','answered','partially_answered','resolved','rejected','archived'}
CLAIM_TYPES = {'factual','definitional','causal','comparative','evaluative','predictive','normative','hypothetical'}
CLAIM_TYPE_ALIASES = {
    'fact': 'factual',
    'definition': 'definitional',
    'cause': 'causal',
    'causation': 'causal',
    'causality': 'causal',
    'comparison': 'comparative',
    'evaluation': 'evaluative',
    'prediction': 'predictive',
    'norm': 'normative',
    'hypothesis': 'hypothetical',
}
POLARITIES = {'positive','negative'}
MODALITIES = {'asserted','possible','probable','capable','necessary','recommended'}
MODALITY_ALIASES = {
    'actual': 'asserted',
    'definite': 'asserted',
    'certain': 'asserted',
    'maybe': 'possible',
    'likely': 'probable',
    'probably': 'probable',
    'can': 'capable',
    'able': 'capable',
    'must': 'necessary',
    'required': 'necessary',
    'should': 'recommended',
}
POLARITY_ALIASES = {
    'affirmative': 'positive',
    'true': 'positive',
    'yes': 'positive',
    'negated': 'negative',
    'false': 'negative',
    'no': 'negative',
}
EVENT_TYPES = {'creation','development','release','publication','deployment','acquisition','merger','migration','training','evaluation','experiment','update','decision','announcement','meeting','failure','incident','other'}
EVENT_STATUSES = {'planned','ongoing','completed','cancelled','failed','unknown'}
QUESTION_TYPES = {'knowledge','research','design','implementation','evaluation'}
EVENT_TIME_PRECISIONS = {'exact','day','month','year','range','relative','unknown'}


def normalize_name(value: str) -> str:
    return re.sub(r'\s+', ' ', str(value).strip()).casefold()


def normalize_predicate(value: str) -> str:
    value = re.sub(r'[^a-zA-Z0-9_]+', '_', str(value).strip().lower())
    return re.sub(r'_+', '_', value).strip('_')


def canonical_entity_type(value: str) -> str:
    raw = str(value).strip()
    if raw in ENTITY_TYPES:
        return raw
    if raw.casefold() in LEGACY_ENTITY_TYPES:
        return LEGACY_ENTITY_TYPES[raw.casefold()]
    raise ValueError(f'Unsupported entity type: {value}')


def canonical_claim_type(value: str) -> str:
    raw = str(value).strip().casefold()
    if raw in CLAIM_TYPES:
        return raw
    if raw in CLAIM_TYPE_ALIASES:
        return CLAIM_TYPE_ALIASES[raw]
    raise ValueError(f'Unsupported claim type: {value}')


def canonical_polarity(value: str) -> str:
    raw = str(value).strip().casefold()
    if raw in POLARITIES:
        return raw
    if raw in POLARITY_ALIASES:
        return POLARITY_ALIASES[raw]
    raise ValueError(f'Unsupported polarity: {value}')


def canonical_modality(value: str) -> str:
    raw = str(value).strip().casefold()
    if raw in MODALITIES:
        return raw
    if raw in MODALITY_ALIASES:
        return MODALITY_ALIASES[raw]
    raise ValueError(f'Unsupported modality: {value}')


def relation_spec(predicate: str) -> dict[str, Any] | None:
    p = normalize_predicate(predicate)
    return next((x for x in RELATION_REGISTRY['relation_predicates'] if x['predicate'] == p), None)


def claim_predicate_allowed(predicate: str) -> bool:
    return normalize_predicate(predicate) in CLAIM_PREDICATES


# Predicate semantics are ontology data, not Python literals (ADR-011; task §31):
# "does this predicate hold one value at a time" is a property of the relation,
# so it lives in the registry and is read from there. Python must not carry a
# second, editable-in-code copy of ontology knowledge.
@dataclass(frozen=True)
class PredicateSpec:
    """Registry-declared semantics of one claim predicate."""

    predicate: str
    functional: bool = False
    temporal: bool = False
    evolution: str | None = None


def claim_predicate_spec(predicate: str) -> PredicateSpec | None:
    """The registry spec for ``predicate``, or ``None`` if it is not registered.

    ``None`` is meaningful: it is the same answer the resolver gives for an
    unregistered candidate, and it is never a license to invent semantics.
    """
    normalized = normalize_predicate(predicate)
    if normalized not in CLAIM_PREDICATES:
        return None
    meta = (CLAIM_REGISTRY.get('predicate_metadata') or {}).get(normalized) or {}
    return PredicateSpec(
        predicate=normalized,
        functional=bool(meta.get('functional')),
        temporal=bool(meta.get('temporal')),
        evolution=meta.get('evolution'),
    )


def functional_claim_predicates() -> frozenset[str]:
    """Registered predicates whose subject holds one value at a time."""
    metadata = CLAIM_REGISTRY.get('predicate_metadata') or {}
    return frozenset(
        p for p in CLAIM_PREDICATES if bool((metadata.get(p) or {}).get('functional')))


# Registry Subset matcher (Context Runtime P2c, spec §5): the full registry never
# enters a prompt - deterministic matching reduces a free-text or out-of-vocabulary
# predicate to the 2-5 registered candidates worth considering. Pure name+synonym
# scoring, no LLM, no external data.
_PREDICATE_HINTS: dict[str, tuple[str, ...]] = {
    'is': ('is a', '是一个', '是一种'),
    'defined_as': ('defined as', 'definition', '定义'),
    'classified_as': ('classified', 'category', '属于', '归类', '分类'),
    'contains': ('contain', '包含', '含有'),
    'includes': ('include', '包含', '包括'),
    'consists_of': ('consist', '组成', '构成'),
    'uses': ('use', 'utilize', '使用', '采用'),
    'retrieves': ('retrieve', '检索'),
    'accesses': ('access', '访问'),
    'provides': ('provide', 'offer', '提供'),
    'receives': ('receive', '接收'),
    'generates': ('generate', '生成'),
    'produces': ('produce', '产生'),
    'creates': ('create', '创建', '新建'),
    'extracts': ('extract', '提取', '抽取'),
    'transforms': ('transform', 'convert', '转换'),
    'supports': ('support', '支持', '支撑'),
    'enables': ('enable', 'allow', '使得', '支持'),
    'allows': ('allow', 'permit', '允许'),
    'improves': ('improve', 'enhance', 'optimize', 'optimise', 'better', '提高', '提升', '改进', '改善', '增强', '优化'),
    'reduces': ('reduce', 'cut', '减少', '降低', '缩减'),
    'increases': ('increase', 'grow', 'raise', '增加', '增长', '上升'),
    'decreases': ('decrease', 'lower', '下降', '减少'),
    'prevents': ('prevent', 'avoid', '阻止', '防止', '避免'),
    'causes': ('cause', '导致', '引起', '造成'),
    'leads_to': ('lead to', 'result in', '导致', '引起'),
    'depends_on': ('depend', '依赖', '取决于'),
    'requires': ('require', 'need', '需要', '要求'),
    'implements': ('implement', '实现', '落地'),
    'based_on': ('based on', '基于'),
    'derived_from': ('derived', 'derive', '源自', '推导', '派生'),
    'extends': ('extend', '扩展', '延伸'),
    'trained_on': ('trained on', 'training data', '训练'),
    'evaluated_on': ('evaluated on', '评估于'),
    'tested_on': ('tested', '测试'),
    'studies': ('study', '研究'),
    'investigates': ('investigate', '调查', '探究'),
    'evaluates': ('evaluate', '评估', '评价'),
    'analyzes': ('analyze', 'analyse', '分析'),
    'compares_with': ('compare', '对比', '比较'),
    'better_than': ('better than', 'outperform', '优于', '胜过'),
    'worse_than': ('worse than', '劣于', '不如'),
    'designed_for': ('designed', 'design for', '面向', '专为'),
    'used_for': ('used for', '用于', '用来'),
    'applied_to': ('applied', 'apply', '应用于', '应用在'),
    'addresses': ('address', 'solve', '解决', '应对'),
}


def match_claim_predicates(text: str, limit: int = 5) -> list[str]:
    """The 2-5 registered claim predicates most consistent with ``text``.

    Deterministic scoring: hint-phrase hits (English + Chinese synonyms) per
    registry predicate, ties broken by registry order. Returns [] when nothing
    matches - callers must treat that as "no suggestion", never as a license to
    invent predicates.
    """
    if not text:
        return []
    hay = str(text).casefold()
    scored: list[tuple[int, int, str]] = []
    for order, predicate in enumerate(CLAIM_REGISTRY['claim_predicates']):
        hits = sum(1 for hint in _PREDICATE_HINTS.get(predicate, ()) if hint in hay)
        # Bare predicate-name mention (e.g. the LLM wrote "optimises" and the
        # hint list carries "optimize") also counts, via substring of the stem.
        if not hits:
            tokens = re.split(r'[^a-zA-Z]+', predicate.replace('_', ' '))
            hits = sum(1 for t in tokens if len(t) > 3 and t in hay)
        if hits:
            scored.append((hits, -order, predicate))
    scored.sort(reverse=True)
    return [p for _, _, p in scored[:max(1, limit)]]


def _types_allowed(actual: set[str], allowed: list[str]) -> bool:
    return '*' in allowed or bool(actual.intersection(allowed))


def relation_types_allowed(source_types: list[str], predicate: str, target_types: list[str]) -> bool:
    spec = relation_spec(predicate)
    if not spec:
        return False
    src = {canonical_entity_type(x) for x in source_types}
    tgt = {canonical_entity_type(x) for x in target_types}
    return _types_allowed(src, spec['source_types']) and _types_allowed(tgt, spec['target_types'])


def relation_endpoint_allowed(types: list[str], predicate: str, *, target: bool = False) -> bool:
    """Whether one *declared* endpoint's types are legal for ``predicate``.

    The per-endpoint form of :func:`relation_types_allowed`, for callers that may
    only know one side: an empty ``types`` list means the endpoint's type is
    *unknown*, which imposes no constraint. Absence of a declaration is not a
    violation — never pass ``['*']`` as a *declared* type, that is a spec
    wildcard, not an entity type (ADR-011).
    """
    spec = relation_spec(predicate)
    if not spec:
        return False
    if not types:
        return True
    allowed = spec['target_types'] if target else spec['source_types']
    return _types_allowed({canonical_entity_type(t) for t in types}, allowed)


def inverse_label(predicate: str) -> str | None:
    spec = relation_spec(predicate)
    return spec['inverse_label'] if spec else None


def registry_version() -> str:
    """Content version over every registry/schema file a compiled prompt may
    depend on (Context Cache invalidation, spec §10). Any edit to the
    vocabularies or the extraction schema changes this fingerprint, which
    changes every cache key - invalidation by construction.
    """
    digest = hashlib.sha1()
    for path in sorted(SCHEMA_DIR.glob('*.json')):
        digest.update(path.name.encode('utf-8'))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]
