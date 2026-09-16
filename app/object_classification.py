"""Claim Object / Mention classification — the semantic boundary for "what is this text".

A claim's object side is not automatically an entity. It is one of:

  entity   — a stable, nameable knowledge object that could be referenced long-term
             (a Person / Org / DTO / Table / technology …). Only these may enter
             Entity Resolution or Object Linking.
  literal  — a value: 8192, 3.14, true, 2026, "高风险", "每天", "仅在生产环境".
  concept  — a description of a mechanism/behaviour ("indexes raw document titles
             and bodies", "only the selected evidence pack is given to the LLM").
  unknown  — short phrases we cannot responsibly type. Never auto-promoted.

This module is deliberately pure and deterministic: no LLM, no database. It answers
one question — "what kind of thing is this text?" — so the persistence, linking and
review paths can agree on a single vocabulary instead of each guessing.

The narrow, safe rule the rest of the system relies on: **only ``entity`` enters
Entity Resolution / Object Linking.** Everything else keeps its text value.
"""
from __future__ import annotations

import re

ENTITY = 'entity'
LITERAL = 'literal'
CONCEPT = 'concept'
UNKNOWN = 'unknown'

# --- literal detection -------------------------------------------------------

_BOOL = {'true', 'false', 'yes', 'no', '是', '否', 'on', 'off'}
# A bare number, optionally with a unit / percent.
_NUMBER_RE = re.compile(
    r'^\s*[+-]?\d+(?:[.,]\d+)?\s*'
    r'(?:%|％|tokens?|chars?|words?|kb|mb|gb|tb|ms|s|sec|secs|seconds?|minutes?|mins?|'
    r'hours?|days?|weeks?|months?|years?|px|dpi|条|个|次|天|小时|分钟|秒|字节|字符|'
    r'行|页|字|元|人|%?)\s*$', re.I)
_DATE_RE = re.compile(r'^\s*\d{4}\s*([-/年]\s*\d{1,2}\s*)?([-/月]\s*\d{1,2}\s*)?[日号]?\s*$')
# Short value/qualifier phrases: risk levels, frequencies, conditions.
_VALUE_PHRASES = {
    'high', 'low', 'medium', 'high risk', 'low risk', 'medium risk',
    'daily', 'weekly', 'monthly', 'yearly', 'hourly', 'always', 'never', 'sometimes',
    '高风险', '中风险', '低风险', '高', '中', '低', '每天', '每日', '每周', '每月', '每年',
    '总是', '从不', '有时', '仅在生产环境', '生产环境', '测试环境', '开发环境', '线上', '线下',
}

# --- technical-symbol detection (entity-ish, qualified-name identity) --------

_TECHNICAL_RE = [
    re.compile(r'[a-z]+[A-Z][A-Za-z0-9]*'),            # camelCase / PascalCase with lowercase prefix
    re.compile(r'[A-Z]{2,}[0-9]+'),                      # FTS5, HTTP2
    re.compile(r'^[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*$'),  # PascalCase multi-word, e.g. TaskPacket
    re.compile(r'[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+'),   # snake_case identifier
    re.compile(r'[A-Za-z0-9]+\.[A-Za-z0-9.]+'),           # dotted qualified name
    re.compile(r'^[A-Z]{2,6}$'),                          # short all-caps acronym (RAG, DTO, API)
]

# --- descriptive (concept) detection -----------------------------------------

_CONCEPT_MARKERS = (
    '提供', '使用', '支持', '需要', '可以', '把', '将', '用于', '负责', '表示', '包括',
    '包含', '实现', '导致', '使得', '机制', '方式', '过程', '能力', '行为', '规则', '策略',
    '模式', '情况', '场景', '流程', '步骤', '条件', '并且', '以及', '然后', '为了',
)
_SENTENCE_PUNCT = re.compile(r'[。！？；;!?]')


def _is_literal(text: str) -> bool:
    low = text.strip().casefold()
    if low in _BOOL or low in _VALUE_PHRASES:
        return True
    if _NUMBER_RE.match(text) or _DATE_RE.match(text):
        return True
    return False


def _looks_technical(text: str) -> bool:
    t = text.strip()
    # A technical symbol is a single token (no sentence structure).
    if len(t.split()) > 3 or _SENTENCE_PUNCT.search(t):
        return False
    return any(rx.search(t) for rx in _TECHNICAL_RE)


def _is_descriptive(text: str) -> bool:
    t = text.strip()
    if len(t) > 40:
        return True
    if _SENTENCE_PUNCT.search(t):
        return True
    # A longer phrase carrying a mechanism/behaviour verb is a description, not a name.
    if len(t) >= 8 and any(m in t for m in _CONCEPT_MARKERS):
        return True
    # English connective structure ("... of ...", "... the ...") with several words.
    words = t.split()
    if len(words) >= 5:
        return True
    return False


_CJK = re.compile(r'[\u4e00-\u9fff]')
_PATH = re.compile(r'[\\/]|\.(md|py|js|ts|tsx|jsx|json|txt|html?|csv|yaml|yml|toml|sql|sh|cfg|ini|log)$', re.I)


def _looks_like_path(t: str) -> bool:
    return bool(_PATH.search(t)) or t.lower().startswith(('http://', 'https://'))


def _name_like(t: str) -> bool:
    """A short phrase that reads as a *name* (proper noun / org / concept label).

    Latin: a capitalised token in a short phrase ("Tim Cook", "OpenAI", "Progressive
    Disclosure"). CJK: a short phrase carrying no mechanism/verb marker ("苹果公司").
    """
    tokens = t.split()
    if tokens and len(tokens) <= 4 and any(tok[:1].isupper() for tok in tokens):
        return True
    if _CJK.search(t) and len(t) <= 20 and not any(m in t for m in _CONCEPT_MARKERS):
        return True
    return False


def classify_object(text: str, *, known_entity: bool = False) -> tuple[str, str]:
    """Return ``(class, reason)`` for a claim-object / mention text.

    ``known_entity`` is True when the text already resolved to a declared entity; that
    always wins, because identity is established rather than guessed.
    """
    t = (text or '').strip()
    if not t:
        return (UNKNOWN, 'empty')
    if known_entity:
        return (ENTITY, 'resolved to an existing/declared entity')
    if _looks_like_path(t):
        return (UNKNOWN, 'source artefact (path/url)')
    if _is_literal(t):
        return (LITERAL, 'is a value')
    if _looks_technical(t):
        return (ENTITY, 'looks like a technical symbol')
    if _is_descriptive(t):
        return (CONCEPT, 'descriptive phrase')
    if _name_like(t):
        return (ENTITY, 'name-like mention')
    return (UNKNOWN, 'unclassified short phrase')


def is_entity_like(text: str) -> bool:
    """The single predicate Object Linking / Entity Resolution consult."""
    return classify_object(text)[0] == ENTITY
