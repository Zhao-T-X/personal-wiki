"""Refusal Semantics — one definition of "what kind of answer is this", shared.

两个消费方曾经各有一套判断：

* ``domain/citation_validation.py`` 用宽松启发式（无引用且长度 < 80 即当作拒答）；
* ``evaluation/qa_eval.py`` 用严格标记契约。

它们对同一条答案可能给出不同结论，而结论差异会直接污染 Golden Dataset、Quality Gate
与回归指标——排查时无法判断是 Correction 错、QA 错、Citation 错还是 evaluator 错。

本模块统一的是**定义**，不是**检测策略**：

```text
        RefusalKind            语义契约（唯一）
             ↑
   classify_refusal()          由标记词表 + 结构化线索得出 kind
             ↑
   ┌─────────┴──────────┐
citation_validation      qa_eval
 (runtime detector)     (evaluation detector)
```

两个消费方都调用同一个 ``classify_refusal``，只在 ``detector`` 字段上标明来源；它们之
后**怎么使用**这个 kind 可以不同（运行时只想问「这条答案是否需要引用」；评测需要的是
数据集契约），但「它是什么」永远只有一个答案。

四种状态，且关键是 **INSUFFICIENT_EVIDENCE ≠ REFUSAL**：

* ``REFUSAL``              —— 拒绝回答（能力/策略上不答，如「我无法回答这个问题。」）
* ``INSUFFICIENT_EVIDENCE`` —— 明确说明知识库没有足够依据。这是**高质量的安全行为**，
  不是拒答；把它当成拒答会惩罚系统最值得鼓励的那条路径。
* ``NON_REFUSAL``          —— 给出了实质回答
* ``UNKNOWN``              —— 无法可靠判断（例如空答案：它既没拒绝也没回答）

**没有引用不等于拒答。** 一个没有引用的正常回答是
``NON_REFUSAL`` + 引用不完整，而不是 ``REFUSAL``。这正是旧启发式最有害的地方。

Pure logic: standard library only, no database, no framework, no LLM
(docs/adr/ADR-002, ADR-014).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

REFUSAL = 'refusal'
NON_REFUSAL = 'non_refusal'
INSUFFICIENT_EVIDENCE = 'insufficient_evidence'
UNKNOWN = 'unknown'


class RefusalKind(str, Enum):
    """The four answer kinds. ``str`` mixin keeps JSON/serialisation trivial."""

    REFUSAL = REFUSAL
    NON_REFUSAL = NON_REFUSAL
    INSUFFICIENT_EVIDENCE = INSUFFICIENT_EVIDENCE
    UNKNOWN = UNKNOWN


# --- the marker vocabularies -------------------------------------------------
# "The knowledge base has no evidence for this" — a *statement about the data*.
INSUFFICIENT_EVIDENCE_MARKERS: tuple[str, ...] = (
    '知识库中没有', '知识库没有', '没有找到', '没有足够证据', '没有足够依据',
    '证据不足', '资料不足', '无足够证据',
    'no sufficient evidence', 'insufficient evidence', 'no evidence',
    'no matching evidence', 'not found in the knowledge base',
    'unable to find', 'could not find',
)

# "I will not / cannot answer" — a *statement about the responder*.
REFUSAL_MARKERS: tuple[str, ...] = (
    '无法回答', '无法作答', '不能回答', '不予回答', '拒绝回答', '无法可靠',
    'cannot answer', 'unable to answer', 'must decline', 'i must refuse',
)

# An answer that carries inline source markers is *citing*, therefore answering.
_INLINE_CITATION = re.compile(r'\[doc:[^\]]*chunk:[^\]]*\]', re.IGNORECASE)

# Detector confidence: how explicit the evidence for the label was. This is NOT a
# model probability — nothing here is probabilistic, the label is deterministic.
_CONF_EXPLICIT = 0.95      # an explicit marker phrase
_CONF_STRUCTURAL = 0.9     # decided by a structural cue (citations / inline marks)
_CONF_NO_SIGNAL = 0.7      # no marker and no cue; a substantive answer by default


@dataclass(frozen=True)
class RefusalDecision:
    """The classified kind, plus why — enough to explain a disagreement later."""

    kind: RefusalKind
    confidence: float | None
    reason: str
    detector: str
    markers: tuple[str, ...] = field(default=())

    @property
    def is_refusal(self) -> bool:
        """True only for an actual policy/capability refusal."""
        return self.kind is RefusalKind.REFUSAL

    @property
    def is_insufficient_evidence(self) -> bool:
        return self.kind is RefusalKind.INSUFFICIENT_EVIDENCE

    @property
    def is_safety_stop(self) -> bool:
        """The answer makes no assertion, so there is nothing to ground.

        This — not "looks short" — is the only reason a citation requirement may
        be excused. ``INSUFFICIENT_EVIDENCE`` counts: the system correctly said it
        cannot answer, which is a success, not a failure.
        """
        return self.kind in (RefusalKind.REFUSAL, RefusalKind.INSUFFICIENT_EVIDENCE)

    def to_dict(self) -> dict:
        return {
            'kind': self.kind.value,
            'confidence': self.confidence,
            'reason': self.reason,
            'detector': self.detector,
            'markers': list(self.markers),
        }


def _find(text: str, markers: tuple[str, ...]) -> tuple[str, ...]:
    lowered = text.casefold()
    return tuple(marker for marker in markers if marker.casefold() in lowered)


def classify_refusal(answer: str, *, citations: list[dict] | None = None,
                     detector: str = 'contract') -> RefusalDecision:
    """Classify one answer into a :class:`RefusalKind`. Deterministic.

    Cue order (earlier cues win, and each is structural before it is lexical):

    1. empty answer                  -> ``UNKNOWN`` (it neither refused nor answered)
    2. citations supplied            -> ``NON_REFUSAL`` (evidence was offered)
    3. inline source markers present -> ``NON_REFUSAL`` (it is citing, so answering)
    4. "no evidence in the KB" phrase -> ``INSUFFICIENT_EVIDENCE``
    5. "I cannot answer" phrase       -> ``REFUSAL``
    6. otherwise                      -> ``NON_REFUSAL``
    """
    text = str(answer or '').strip()

    # 1. An empty answer carries no signal at all. Calling it a refusal used to be
    #    a convenient shortcut; it is not the same thing as declining to answer.
    if not text:
        return RefusalDecision(kind=RefusalKind.UNKNOWN, confidence=None,
                               reason='empty answer: no signal to interpret',
                               detector=detector)

    # 2/3. Structural cues: offering evidence means you are answering, not refusing.
    if citations:
        return RefusalDecision(kind=RefusalKind.NON_REFUSAL, confidence=_CONF_STRUCTURAL,
                               reason='citations were supplied, so evidence was offered',
                               detector=detector)
    if _INLINE_CITATION.search(text):
        return RefusalDecision(kind=RefusalKind.NON_REFUSAL, confidence=_CONF_STRUCTURAL,
                               reason='the answer cites sources inline, so it is answering',
                               detector=detector)

    # 4. A statement about the data is more specific than one about the responder,
    #    so it wins when both appear ("我无法回答，因为知识库中没有足够证据").
    insufficient = _find(text, INSUFFICIENT_EVIDENCE_MARKERS)
    if insufficient:
        return RefusalDecision(
            kind=RefusalKind.INSUFFICIENT_EVIDENCE, confidence=_CONF_EXPLICIT,
            reason='the answer states the knowledge base lacks sufficient evidence',
            detector=detector, markers=insufficient)

    # 5. A refusal about the responder. Note it is NOT penalised as a hallucination,
    #    but it also does not earn the "correctly said we lack evidence" credit.
    refusal = _find(text, REFUSAL_MARKERS)
    if refusal:
        return RefusalDecision(
            kind=RefusalKind.REFUSAL, confidence=_CONF_EXPLICIT,
            reason='the answer declines to answer',
            detector=detector, markers=refusal)

    # 6. No signal: an ordinary substantive answer (its citations are a separate
    #    question — see citation_validation — and do not make it a refusal).
    return RefusalDecision(kind=RefusalKind.NON_REFUSAL, confidence=_CONF_NO_SIGNAL,
                           reason='no refusal signal present', detector=detector)
