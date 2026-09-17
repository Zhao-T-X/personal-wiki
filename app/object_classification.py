"""Claim Object kind — deterministic guardrails + the LLM's semantic verdict (Step 10).

Division of labour (the whole point of this module):

* **The LLM decides the semantics.** The extraction contract now carries
  ``object_kind`` (``entity | concept | literal | unknown``) — the model, which read the
  sentence, says what the object *is*. Code does not re-guess that from keywords.
* **Deterministic code guards the unambiguous cases.** A number, boolean, date, URL,
  e-mail, file path, or a technical identifier is recognised by structure, never by
  "it contains the word …". Those guardrails always win, because structure is proof.
* **A slash is not structure.** ``slash presence ≠ path`` (Step 12). ``A / B``,
  ``客户端/服务端架构`` and ``LOAD / SUMMARIZE / …`` are prose; only a single ASCII
  path-shaped token, a URL, a Windows path or a path prefix is a source artefact.
* **Keyword lists are hints only.** ``concept_hint`` may note that a phrase *looks*
  descriptive — it is a **candidate-generation hint** used nowhere as a verdict.

The narrow rule the rest of the system relies on: **only ``entity`` enters Entity
Resolution / Object Linking.** No keyword makes anything an ``entity``; only the LLM's
``object_kind`` (or a deterministic technical identifier) does.

Pure and deterministic: no LLM, no database.
"""
from __future__ import annotations

import re

ENTITY = 'entity'
LITERAL = 'literal'
CONCEPT = 'concept'
UNKNOWN = 'unknown'

# The values the extraction contract may declare and code will accept.
LEGAL_OBJECT_KINDS = frozenset({ENTITY, LITERAL, CONCEPT, UNKNOWN})

# --- deterministic: literals -------------------------------------------------

_BOOL = {'true', 'false', 'yes', 'no', '是', '否', 'on', 'off'}
_NUMBER_RE = re.compile(
    r'^\s*[+-]?\d+(?:[.,]\d+)?\s*'
    r'(?:%|％|tokens?|chars?|words?|kb|mb|gb|tb|ms|s|sec|secs|seconds?|minutes?|mins?|'
    r'hours?|days?|weeks?|months?|years?|px|dpi|条|个|次|天|小时|分钟|秒|字节|字符|'
    r'行|页|字|元|人|%?)\s*$', re.I)
_DATE_RE = re.compile(r'^\s*\d{4}\s*([-/年]\s*\d{1,2}\s*)?([-/月]\s*\d{1,2}\s*)?[日号]?\s*$')
# A small, *registered* set of value phrases (risk levels, frequencies, environments).
# This is closed vocabulary, not a "guess from words" rule.
_REGISTERED_LITERALS = {
    'high', 'low', 'medium', 'high risk', 'low risk', 'medium risk',
    'daily', 'weekly', 'monthly', 'yearly', 'hourly', 'always', 'never', 'sometimes',
    '高风险', '中风险', '低风险', '高', '中', '低', '每天', '每日', '每周', '每月', '每年',
    '总是', '从不', '有时', '仅在生产环境', '生产环境', '测试环境', '开发环境', '线上', '线下',
}

# --- deterministic: source artefacts and identifiers --------------------------
#
# A source artefact is recognised by *structure*, never by the presence of a slash.
# "Static Context / Dynamic Context" and "客户端/服务端架构" are prose that happens to
# contain "/": only a single ASCII path-shaped token (or a URL / Windows path / path
# prefix) proves a path. A slash on its own proves nothing (Step 12).

_URL_RE = re.compile(r'^(?:[a-z][a-z0-9+.\-]*://|mailto:)\S+$', re.I)
_WINDOWS_PATH_RE = re.compile(r'^[A-Za-z]:[\\/]')
# `/home/x`, `./x`, `../x`, `~/x` — a path prefix is structural proof.
_PATH_PREFIX_RE = re.compile(r'^(?:[\\/]|\.{1,2}[\\/]|~[\\/])')
# A trailing extension is what makes a bare token a *file* rather than a word.
_FILE_EXT_RE = re.compile(r'\.[A-Za-z0-9]{1,8}$')
# One path segment: ASCII, no spaces, no CJK. Anything else is prose, not a path.
_PATH_SEGMENT_RE = re.compile(r'^[A-Za-z0-9._~@$%+=:,\-\[\](){}#&;!*\'"]+$')
_SEPARATOR_RE = re.compile(r'[\\/]')
_WHITESPACE_RE = re.compile(r'\s')
_CJK_RE = re.compile(r'[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]')

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_TECHNICAL_RE = [
    re.compile(r'[a-z]+[A-Z][A-Za-z0-9]*'),               # camelCase / PascalCase
    re.compile(r'[A-Z]{2,}[0-9]+'),                         # FTS5, HTTP2
    re.compile(r'^[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*$'),    # TaskPacket
    re.compile(r'[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+'),      # snake_case identifier
    re.compile(r'[A-Za-z0-9]+\.[A-Za-z0-9.]+'),             # dotted qualified name
    re.compile(r'^[A-Z]{2,6}$'),                            # short acronym (RAG, DTO)
]
_SENTENCE_PUNCT = re.compile(r'[。！？；;!?]')

# --- candidate hint (NOT a verdict) ------------------------------------------
#
# A phrase that carries a mechanism/behaviour word *tends* to be a description. This is
# offered as a hint to a caller that wants to sort candidates; it never becomes an
# object class on its own — the LLM's object_kind owns that decision.
_CONCEPT_HINT_MARKERS = (
    '提供', '使用', '支持', '需要', '可以', '把', '将', '用于', '负责', '表示', '包括',
    '包含', '实现', '导致', '使得', '机制', '方式', '过程', '能力', '行为', '规则', '策略',
    '模式', '情况', '场景', '流程', '步骤', '条件', '并且', '以及', '然后', '为了',
)


def is_registered_literal(text: str) -> bool:
    low = (text or '').strip().casefold()
    return low in _BOOL or low in _REGISTERED_LITERALS


def is_value(text: str) -> bool:
    """Deterministic literal: a number/date/boolean/registered value phrase."""
    return bool(_NUMBER_RE.match(text or '') or _DATE_RE.match(text or '')) or is_registered_literal(text)


def _looks_like_path_token(text: str) -> bool:
    """Structural test: is this ONE token shaped like a path, not prose with slashes?

    Proof, in order of strength:

    * a URL or ``mailto:`` — matched on the whole token;
    * a Windows path (``C:\\work\\x.py``) or a path prefix (``/x``, ``./x``, ``../x``, ``~/x``);
    * a single ASCII token whose segments are all path-shaped and which either carries a
      file extension or nests at least two levels deep.

    What is deliberately **not** proof: whitespace (``A / B`` is prose), CJK characters
    (``客户端/服务端`` is prose), and a slash on its own. Those fall through to the LLM's
    ``object_kind``, which is the only thing allowed to make a semantic call.
    """
    t = (text or '').strip()
    if not t or _WHITESPACE_RE.search(t) or _CJK_RE.search(t):
        return False
    # ``path::symbol`` is a path plus a qualifier: judge the path part.
    core = t.split('::', 1)[0].strip()
    if core != t:
        return _looks_like_path_token(core)
    if _URL_RE.match(t) or _WINDOWS_PATH_RE.match(t) or _PATH_PREFIX_RE.match(t):
        return True
    if not _SEPARATOR_RE.search(t):
        # A bare filename token (``runtime.md``) is a source artefact; a bare word is not.
        return bool(_FILE_EXT_RE.search(t))
    segments = [s for s in re.split(r'[\\/]+', t) if s]
    if len(segments) < 2 or not all(_PATH_SEGMENT_RE.match(s) for s in segments):
        return False
    return len(segments) >= 3 or bool(_FILE_EXT_RE.search(segments[-1]))


def is_source_artefact(text: str) -> bool:
    """Deterministic source artefact: file path, directory fragment, URL or e-mail."""
    t = (text or '').strip()
    if not t:
        return False
    return bool(_EMAIL_RE.match(t)) or _looks_like_path_token(t)


def is_technical_identifier(text: str) -> bool:
    """Deterministic technical symbol (single token, no sentence structure)."""
    t = (text or '').strip()
    if not t or len(t.split()) > 3 or _SENTENCE_PUNCT.search(t):
        return False
    return any(rx.search(t) for rx in _TECHNICAL_RE)


def concept_hint(text: str) -> bool:
    """Candidate hint only: the phrase *looks* descriptive. Never a verdict."""
    t = (text or '').strip()
    if not t:
        return False
    if _SENTENCE_PUNCT.search(t) or len(t) > 40 or len(t.split()) >= 5:
        return True
    return len(t) >= 8 and any(m in t for m in _CONCEPT_HINT_MARKERS)


def classify_object(text: str, *, known_entity: bool = False,
                    object_kind: str | None = None) -> tuple[str, str]:
    """Return ``(kind, reason)`` for a claim object / mention.

    Order of authority:
      1. identity already established (``known_entity``) → entity;
      2. deterministic guards (value / source artefact / technical identifier);
      3. the LLM's declared ``object_kind`` (validated against :data:`LEGAL_OBJECT_KINDS`);
      4. otherwise ``unknown`` — never a keyword-based ``concept`` verdict.
    """
    t = (text or '').strip()
    if not t:
        return (UNKNOWN, 'empty')
    if known_entity:
        return (ENTITY, 'resolved to an existing/declared entity')
    if is_source_artefact(t):
        return (LITERAL, 'source artefact (path/url/email)')
    if is_value(t):
        return (LITERAL, 'is a value')
    if is_technical_identifier(t):
        return (ENTITY, 'deterministic technical identifier')
    if object_kind in LEGAL_OBJECT_KINDS:
        return (object_kind, 'declared by extraction object_kind')
    return (UNKNOWN, 'unclassified (no deterministic signal, no declared kind)')


def is_entity_like(text: str, *, object_kind: str | None = None) -> bool:
    """The single predicate Object Linking / Entity Resolution consult for *text*."""
    return classify_object(text, object_kind=object_kind)[0] == ENTITY
