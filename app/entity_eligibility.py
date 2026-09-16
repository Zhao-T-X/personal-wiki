"""Deterministic Entity Eligibility — the gate between LLM extraction and persistence.

Why this exists (task: stop the extraction pipeline from turning every noun the
model saw into a permanent knowledge subject):

* a file path (``docs/foo/bar.md``) is not a subject you can ask about;
* a relation / schema field name (``relations``, ``claim_relations``, ``columns``)
  is a structural artefact, not a thing in the world;
* a pure modifier (``现在``, ``主要``, ``一般``) carries no independent identity;
* a technical symbol (``GomsInventoryPagingRequestDTO``) is only worth keeping if
  some claim actually uses it — otherwise it is an orphaned mention.

This is the single, dictionary-free place that decides KEEP / DROP / REVIEW for a
*candidate* entity coming out of extraction. It is deliberately deterministic: no
LLM call. The only fuzzy step is "does any claim reference this entity?", which is a
set membership test over the extraction envelope we already hold.

Verdicts:
  KEEP  — persist normally (it has claim support / clear identity).
  DROP  — never an entity; do not persist at all.
  REVIEW— plausible but unproven (no claim support); persist as a *candidate*
          (status='candidate') so it is quarantined out of the verified pool and
          surfaces for human review instead of silently becoming knowledge.

The gate is opt-out for experiments only (``extraction_eligibility_enabled`` in
config). In production it is always on.
"""
from __future__ import annotations

import re

from .config import runtime
from .ontology import canonical_entity_type
from .normalization import normalize_name
from .object_classification import classify_object, LITERAL, CONCEPT

_CLASSIFICATION_GATE = 'object_classification_enabled'

# Types whose members are "technical objects": by default they need an explicit
# claim to earn a place in the knowledge pool (qualified_name identity policy).
_TECHNICAL_TYPES = {'Technology', 'Software', 'Dataset', 'Model', 'Standard', 'Protocol', 'Resource'}

# Names that are structural artefacts, never knowledge subjects.
_RELATION_NAMES = {
    'relations', 'relation', 'relationships', 'foreign_keys', 'indexes', 'columns',
    'fields', 'attributes', 'properties', 'schema', 'metadata', 'params', 'parameters',
    'arguments', 'args', 'config', 'configuration', 'settings', 'spec', 'specs',
    'specifications', 'return', 'returns', 'request', 'response', 'input', 'output',
    'event', 'events', 'log', 'logs', 'logger', 'history', 'record', 'records',
    'exception', 'error', 'state', 'status', 'result', 'results', 'query', 'queries',
    'mapping', 'mappings', 'enum', 'enums', 'constant', 'constants',
}
# Suffix patterns that mark a name as a relation/field dump rather than a subject.
_RELATION_SUFFIX = re.compile(
    r'(_relations|_relationship|_relationships|_fields|_columns|_attributes|_params'
    r'|_arguments|_config|_logs|_events|_indexes|_keys|_mapping|_mappings|_specs)$', re.I)

# A path-like or extension-bearing token is a file, not a subject.
_FILE_PATH = re.compile(r'(^|[\s/\\])([\w.\-]+/){1,}[\w.\-]+|(\.(md|py|js|ts|tsx|jsx|json|txt|html?|csv|yaml|yml|toml|sql|sh|cfg|ini|log|docx?|pdf))$', re.I)
_DIR_FRAGMENT = re.compile(r'(^|/|\\)(docs|src|app|tests|test|lib|libs|bin|etc|var|usr|home|build|dist|node_modules|packages|internal|pkg)(/|\\|$)', re.I)

# Plain modifiers / function words: they carry no stable identity on their own.
# Conservative on purpose — only words that can never be a subject.
_MODIFIER_WORDS = {
    'now', 'current', 'current', 'previous', 'later', 'here', 'there', 'this', 'that',
    'main', 'important', 'general', 'usually', 'possibly', 'should', 'can', 'for example',
    'such as', 'some', 'certain', 'other', 'another', 'also', 'therefore', 'so', 'but',
    'however', 'if', 'although', 'because', 'according to', 'through', 'for', 'about',
    'and', 'or', 'as well as',
    '现在', '当前', '此前', '之前', '之后', '后来', '这里', '那里', '这个', '那个', '主要',
    '重要', '一般', '通常', '可能', '应该', '可以', '例如', '比如', '一些', '某些', '其他',
    '另外', '同时', '因此', '所以', '但是', '然而', '如果', '虽然', '由于', '根据', '通过',
    '对于', '关于', '以及', '并且', '或者', '我们', '他们', '一个', '一种', '这些', '那些',
}


def _is_pure_modifier(name: str) -> bool:
    n = name.strip().casefold()
    if n in _MODIFIER_WORDS:
        return True
    # A short run of nothing but modifier words / punctuation is not a subject.
    parts = re.split(r'[\s,，、；;]+', n)
    return bool(parts) and all(p in _MODIFIER_WORDS for p in parts if p)


def _looks_like_file_path(name: str) -> bool:
    n = name.strip()
    if _FILE_PATH.search(n):
        return True
    if _DIR_FRAGMENT.search(n):
        return True
    # "docs/.../specs" style with no extension but clear path separators.
    if '/' in n and len(n) > 3 and not n.endswith('/'):
        segments = [s for s in n.split('/') if s]
        return bool(segments) and any(s in {'docs', 'src', 'app', 'tests', 'lib', 'specs', 'schemas'} for s in segments)
    return False


def _looks_like_relation_name(name: str) -> bool:
    n = name.strip().casefold()
    if n in _RELATION_NAMES:
        return True
    if _RELATION_SUFFIX.search(n):
        return True
    # "X 关系" / "X 字段" style Chinese structural names.
    if re.search(r'(关系|字段|属性|配置|参数|索引|日志|事件|状态|规格)$', name.strip()):
        return True
    return False


def is_plausible_subject(name: str) -> bool:
    """Used by the claim-subject fallback: should an unknown claim subject become a
    Resource entity, or be left as an unresolved candidate?

    File paths, relation/field names and pure modifiers are NOT plausible subjects,
    so the fallback must not auto-create a Resource for them. Gated by the same flag
    as :func:`entity_eligibility`: with eligibility disabled (Before run of an A/B),
    every subject is plausible and the old "always create a Resource" behavior holds.
    """
    if not runtime().get('extraction_eligibility_enabled', True):
        return True
    n = (name or '').strip()
    if not n or len(n) > 120:
        return False
    if _looks_like_file_path(n):
        return False
    if _looks_like_relation_name(n):
        return False
    if _is_pure_modifier(n):
        return False
    # A value ("8192", "每天") or a description is not a subject to invent an entity for.
    if runtime().get('object_classification_enabled', True):
        if classify_object(n)[0] in (LITERAL, CONCEPT):
            return False
    return True


def entity_eligibility(name: str, types: list[str] | None, *, supported: bool) -> tuple[str, str | None]:
    """Decide KEEP / DROP / REVIEW for one candidate entity.

    ``supported`` is True when some claim in the same envelope names this entity
    (by name or alias) as its subject or object — i.e. it actually participates in
    a fact, which is the bar for becoming knowledge.
    """
    if not runtime().get('extraction_eligibility_enabled', True):
        return ('KEEP', None)

    n = (name or '').strip()
    if not n:
        return ('DROP', '名称为空')

    if _looks_like_file_path(n):
        return ('DROP', '文件路径不是知识主体')
    if _looks_like_relation_name(n):
        return ('DROP', '关系/字段名不是知识主体')
    if _is_pure_modifier(n):
        return ('DROP', '普通修饰词不是实体')
    if runtime().get(_CLASSIFICATION_GATE, True) and classify_object(n)[0] == LITERAL:
        # A value ("8192", "每天") is never a knowledge subject.
        return ('DROP', '字面量值不是实体')

    if not supported:
        canon = {canonical_entity_type(t) for t in (types or [])}
        if canon & _TECHNICAL_TYPES:
            return ('REVIEW', '技术对象无 Claim 支撑，需人工确认')
        return ('REVIEW', '无 Claim 支撑，需人工确认')
    return ('KEEP', None)
