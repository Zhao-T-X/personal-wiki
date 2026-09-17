"""Step 12 — the slash guardrail: ``slash presence ≠ path``.

The audit found 14 real objects that prose alone sent to ``literal``: any text with a
``/`` in it was declared a source artefact. The fix is *structural*: a source artefact
is a URL, a Windows path, a path-prefixed token, or a single ASCII token whose segments
are path-shaped and which carries an extension or nests two levels deep.

There is no keyword list here and none may be added. These tests lock both directions:
real paths still classify as ``literal``, and prose keeps whatever the LLM declared.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.object_classification import (CONCEPT, ENTITY, LITERAL, UNKNOWN,
                                       classify_object, is_entity_like,
                                       is_source_artefact)

AUDIT = Path(__file__).parent / 'golden_corpus' / 'object_kind_audit.json'

# Cases 1-4: structure is proof — these must never stop being source artefacts.
REAL_ARTEFACTS = [
    'docs/context/runtime.md',
    'https://example.com/a/b',
    '/home/user/project/main.py',
    r'C:\work\project\main.py',
]

# Cases 5-8 (+ the audit's other shapes): prose that merely contains a slash.
PROSE_WITH_SLASHES = [
    'Static Context / Dynamic Context',
    'LOAD / SUMMARIZE / RETRIEVE_LATER / NEVER_LOAD',
    '客户端/服务端架构',
    'A/B 测试',
    '概念/方法',
    'A/B',
    '用户导入的 Markdown / TXT / HTML 原文及其元信息',
]


@pytest.mark.parametrize('text', REAL_ARTEFACTS)
def test_real_source_artefacts_are_still_literal(text):
    assert is_source_artefact(text) is True
    assert classify_object(text)[0] == LITERAL


@pytest.mark.parametrize('text', PROSE_WITH_SLASHES)
def test_prose_with_slashes_is_not_a_source_artefact(text):
    assert is_source_artefact(text) is False
    assert classify_object(text)[0] != LITERAL


@pytest.mark.parametrize('text', PROSE_WITH_SLASHES)
def test_prose_keeps_the_llm_declared_kind(text):
    """Case 5/6/7/8: the LLM's verdict survives; the guardrail does not override it."""
    assert classify_object(text, object_kind=CONCEPT)[0] == CONCEPT


def test_case9_entity_is_not_downgraded_by_a_slash():
    """``Some/Name`` is not a path, so a declared entity stays an entity."""
    assert is_source_artefact('Some/Name') is False
    assert classify_object('Some/Name', object_kind=ENTITY)[0] == ENTITY
    assert is_entity_like('Some/Name', object_kind=ENTITY) is True


def test_case10_path_guardrail_overrides_the_llm():
    """A declared ``concept`` cannot turn a real path into knowledge: structure wins."""
    assert classify_object('docs/foo/bar.md', object_kind=CONCEPT)[0] == LITERAL


def test_a_slash_alone_proves_nothing():
    """The invariant this step exists for: no slash, no path."""
    for text in ('A / B', '1/2', 'and/or', '读/写'):
        assert is_source_artefact(text) is False, text
        assert classify_object(text)[0] != LITERAL, text


def test_source_artefact_shapes_are_all_covered():
    """The guardrail must cover every structural shape it covered before the fix."""
    for text in ('app/retrieval.py', 'app/retrieval.py::_current_claims',
                 'docs/adr/ADR-011.md', 'data/wiki.db', 'runtime.md',
                 'mailto:someone@example.com', './scripts/run.sh', 'https://x.dev/a?q=1'):
        assert is_source_artefact(text) is True, text
        assert classify_object(text)[0] == LITERAL, text


def test_known_audit_false_positives_all_disappear():
    """The 14 real objects from the Step 10 audit that were literal only because of '/'.

    The list is read from the audit artifact, so it is the audit's data — not a
    hand-written list that could be tuned until it passes.
    """
    if not AUDIT.exists():
        pytest.skip('run scripts/audit_extraction_diff.py to produce object_kind_audit.json')
    errors = json.loads(AUDIT.read_text(encoding='utf-8'))['object_kind_errors']
    assert errors, 'the audit found no slash errors: this regression test has no data'
    still_literal = [e['text'] for e in errors
                     if classify_object(e['text'])[0] == LITERAL]
    assert still_literal == [], f'still misclassified as literal: {still_literal}'
    for entry in errors:
        assert is_source_artefact(entry['text']) is False, entry['text']


def test_no_keyword_list_decides_the_class():
    """A regression guard on the *shape* of the fix, not on its output.

    ``classify_object`` with no declared kind must never return ``concept``: only the
    LLM makes that call. If someone adds a keyword rule, this fails.
    """
    for text in PROSE_WITH_SLASHES + ['完全无关的一句话描述']:
        assert classify_object(text)[0] in (UNKNOWN,), text
