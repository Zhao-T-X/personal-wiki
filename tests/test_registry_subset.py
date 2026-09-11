"""Context Runtime P2c: registry subset matching (spec §5).

The full predicate registry never enters a prompt. When a claim predicate is
out of vocabulary, the deterministic matcher reduces it to the 2-5 registered
candidates worth considering, and that subset travels with the validation
error into the extraction repair instruction.
"""
from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD_MODULES = ('app.ontology', 'app.extraction')


def _reload(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'wiki.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config
    importlib.reload(app.config)
    for name in _RELOAD_MODULES:
        importlib.reload(importlib.import_module(name))


def test_matcher_returns_bounded_registered_candidates(tmp_path):
    _reload(tmp_path)
    from app.ontology import CLAIM_PREDICATES, match_claim_predicates

    subset = match_claim_predicates('该方法显著提高了检索准确率，并优化了响应速度', limit=5)
    assert 1 <= len(subset) <= 5
    assert set(subset) <= CLAIM_PREDICATES
    assert 'improves' in subset                      # 提高 / 优化 -> improves


def test_matcher_understands_english_text(tmp_path):
    _reload(tmp_path)
    from app.ontology import match_claim_predicates

    subset = match_claim_predicates('RAG reduces hallucinations and prevents outdated answers')
    assert 'reduces' in subset
    assert 'prevents' in subset


def test_matcher_is_honest_when_there_is_no_signal(tmp_path):
    _reload(tmp_path)
    from app.ontology import match_claim_predicates

    assert match_claim_predicates('xyzzy qqq wrrp') == []
    assert match_claim_predicates('') == []


def test_validation_error_carries_the_candidate_subset(tmp_path):
    _reload(tmp_path)
    from app.extraction import normalize_extraction

    bad = {
        'entities': [{'name': 'RAG', 'types': ['Concept']},
                     {'name': 'Accuracy', 'types': ['Concept']}],
        'claims': [{'subject': 'RAG', 'predicate': 'optimises', 'object': 'Accuracy',
                    'content': 'RAG optimises Accuracy.', 'evidence_quote': 'RAG optimises Accuracy.'}],
        'events': [], 'ideas': [], 'questions': [],
    }
    try:
        normalize_extraction(bad)
        assert False, 'unregistered predicate must be rejected'
    except ValueError as exc:
        message = str(exc)
        assert 'Unsupported claim predicate: optimises' in message
        assert 'Closest registered predicates' in message
        assert 'improves' in message                  # the repair LLM sees candidates


def test_registered_predicate_still_passes_normally(tmp_path):
    _reload(tmp_path)
    from app.extraction import normalize_extraction

    good = {
        'entities': [{'name': 'RAG', 'types': ['Concept']},
                     {'name': 'Accuracy', 'types': ['Concept']}],
        'claims': [{'subject': 'RAG', 'predicate': 'Improves', 'object': 'Accuracy',
                    'content': 'RAG improves Accuracy.', 'evidence_quote': 'RAG improves Accuracy.',
                    'confidence': 0.9, 'source_chunk': 'chunk-1'}],
        'events': [], 'ideas': [], 'questions': [],
    }
    out = normalize_extraction(good)
    assert out['claims'][0]['predicate'] == 'improves'


def test_repair_instruction_includes_the_validation_error(tmp_path):
    _reload(tmp_path)
    import importlib as il
    from app.llm import _repair_instruction
    il.reload(il.import_module('app.llm'))

    text = _repair_instruction({'claims': []}, error='Unsupported claim predicate: optimises. '
                                                      'Closest registered predicates: improves, increases.')
    assert 'Validation error to fix' in text
    assert 'Closest registered predicates: improves, increases' in text
