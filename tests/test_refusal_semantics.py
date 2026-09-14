"""Refusal Semantics — one definition, layered detectors (ADR-014).

The five scenarios are the acceptance cases from the design review. Each pins the
*semantic* kind, not a detector's opinion, because the whole point of the change
was that the runtime and the evaluator must never disagree about the same answer.
"""
from __future__ import annotations

from app.domain.refusal import (INSUFFICIENT_EVIDENCE_MARKERS, REFUSAL_MARKERS,
                                RefusalKind, classify_refusal)


def _kind(answer: str, citations=None) -> RefusalKind:
    return classify_refusal(answer, citations=citations).kind


# --- the five acceptance scenarios -------------------------------------------

def test_substantive_short_answer_without_citations_is_not_a_refusal():
    """① 正常短回答 + 无引用 —— 绝不是拒答。

    This is precisely what the old ``len < 80`` heuristic got wrong: it excused
    every short answer from the citation requirement by calling it a refusal.
    """
    decision = classify_refusal('苹果目前的 CEO 是 John Ternus。', citations=[])
    assert decision.kind is RefusalKind.NON_REFUSAL
    assert decision.is_refusal is False
    assert decision.is_safety_stop is False


def test_explicit_refusal():
    """② 明确拒答"""
    decision = classify_refusal('我无法回答这个问题。')
    assert decision.kind is RefusalKind.REFUSAL
    assert decision.is_refusal is True


def test_insufficient_evidence_is_not_a_refusal():
    """③ 知识不足 —— 这是高质量的安全行为，不是拒答。"""
    decision = classify_refusal('当前知识库中没有足够证据回答这个问题。')
    assert decision.kind is RefusalKind.INSUFFICIENT_EVIDENCE
    assert decision.is_refusal is False
    assert decision.is_safety_stop is True          # ...but it does stop safely


def test_explained_refusal_is_still_a_refusal():
    """④ 有一点解释但仍然拒答"""
    assert _kind('这个问题涉及当前信息，我无法可靠确认。') is RefusalKind.REFUSAL


def test_answer_with_a_caveat_is_not_a_refusal():
    """⑤ 正常回答但带 caveat"""
    answer = '根据现有知识，John Ternus 是当前 CEO，但证据更新时间较早。'
    assert _kind(answer) is RefusalKind.NON_REFUSAL


# --- the structural cues ------------------------------------------------------

def test_empty_answer_is_unknown_not_a_refusal():
    """An empty answer neither refused nor answered — saying otherwise was a guess."""
    decision = classify_refusal('')
    assert decision.kind is RefusalKind.UNKNOWN
    assert decision.is_safety_stop is False
    assert decision.confidence is None


def test_offering_citations_makes_it_an_answer():
    assert _kind('知识库中没有找到证据', [{'chunk_id': 'c1'}]) is RefusalKind.NON_REFUSAL


def test_inline_citation_markers_make_it_an_answer():
    assert _kind('答案见 [doc:d1 chunk:c1]') is RefusalKind.NON_REFUSAL


def test_a_statement_about_the_data_beats_one_about_the_responder():
    """"I cannot answer *because the KB lacks evidence*" is about the data."""
    decision = classify_refusal('我无法回答，因为知识库中没有足够证据。')
    assert decision.kind is RefusalKind.INSUFFICIENT_EVIDENCE
    assert decision.markers


def test_the_two_marker_vocabularies_do_not_overlap():
    """A phrase must mean exactly one thing, or precedence becomes a coin flip."""
    assert not (set(INSUFFICIENT_EVIDENCE_MARKERS) & set(REFUSAL_MARKERS))


# --- the definition is shared, the detectors are layered ----------------------

def test_both_consumers_agree_on_the_kind():
    """Runtime and evaluation must return the same kind for the same answer."""
    from app.domain.citation_validation import validate_citations
    from app.evaluation.qa_eval import refusal_decision, refusal_kind

    samples = [
        '苹果目前的 CEO 是 John Ternus。',
        '我无法回答这个问题。',
        '当前知识库中没有足够证据回答这个问题。',
        '',
        '答案见 [doc:d1 chunk:c1]',
    ]
    for answer in samples:
        expected = classify_refusal(answer, citations=[]).kind.value
        assert refusal_kind(answer, []) == expected, answer

    # The layering is visible, and only in the ``detector`` label.
    assert refusal_decision('x is the CEO').detector == 'evaluation'
    assert classify_refusal('x is the CEO', detector='runtime').detector == 'runtime'


def test_citation_report_exposes_the_shared_verdict():
    from app.domain.citation_validation import validate_citations

    report = validate_citations('q', '当前知识库中没有足够证据回答这个问题。', [])
    assert report.refusal['kind'] == 'insufficient_evidence'
    assert report.refusal['detector'] == 'runtime'
    assert 'insufficient_evidence' in report.flags
    assert 'refusal' not in report.flags          # the two are not synonyms
    # A safety stop asserts nothing, so it is fully covered by construction.
    assert report.dimensions['coverage'] == 1.0
