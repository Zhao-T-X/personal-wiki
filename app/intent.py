"""Intent: which of four things a sentence is asking for — decided cheaply, on purpose.

One Box is not a chat box; it is one entry point in front of workflows that already
exist (Knowledge / Ask / Research / Correct). So this module does exactly one thing: it
says which of them a sentence belongs to, how sure it is, and which existing endpoint
should do the work. It implements none of them.

Two rules shape it:

* **No model call per message.** Everything here is markers, sentence shape, and the
  signals the query router already extracts from the registry. Asking a model "what did
  the user mean?" would add latency to every sentence to answer a question the text
  usually answers itself — and One Box must not become the slowest way to use the wiki.
* **A wrong guess has to be cheap to fix.** The result carries its intent, confidence
  and reason, so the UI can ask once instead of guessing twice, and the caller can
  re-route without the user retyping anything.

``UNKNOWN`` is a real outcome, not a failure. "苹果。" is not a question, not a
statement about knowledge and not a research topic; the honest reply is to ask which
one it is, with a few shortcuts, and to execute none of them.

The marker lists are *phrasings* ("帮我记", "不对"), never domain vocabulary — what a
predicate or a type is called stays in the registry, and nothing here re-declares it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

KNOWLEDGE = 'knowledge'
ASK = 'ask'
RESEARCH = 'research'
CORRECT = 'correct'
UNKNOWN = 'unknown'

INTENTS = (KNOWLEDGE, ASK, RESEARCH, CORRECT, UNKNOWN)

# Intents that are offered before they are acted on, however confident the reading is.
# A wrong Ask costs a rephrase; a wrong write costs the user's trust in their own
# knowledge base — and a research run costs time and tokens. Only reading is free, so
# only reading acts on its own.
CONFIRM_INTENTS = frozenset({KNOWLEDGE, RESEARCH, CORRECT})

# Phrasings, not ontology.
_CORRECT_MARKS = ('不对', '错了', '记错', '纠正', '应该是', '已更新为', '已变更为', '并不是', '其实是', '改成')
_KNOWLEDGE_MARKS = ('记住', '记一下', '帮我记', '记进', '记入', '收录', '存进', '存入知识库',
                    '我的偏好是')
_RESEARCH_MARKS = ('研究', '调研', '分析一下', '比较一下', '探究', '来龙去脉', '为什么', '有什么变化', '梳理')
_QUESTION_MARKS = ('？', '?', '是谁', '是什么', '什么是', '多少', '哪些', '几点', '怎么样', '如何', '吗')

# What to offer when the sentence could mean several things. Shortcuts, never a guess.
_SUGGESTIONS = ('问一个事实问题', '研究这个主题', '把一段内容存成知识')


@dataclass(frozen=True)
class Intent:
    """What a sentence is asking for, and how confident that reading is."""

    intent: str
    confidence: float
    reason: str
    text: str
    needs_confirmation: bool = False
    suggestions: tuple[str, ...] = ()
    context_claim_id: str | None = None
    steps: list[dict] = field(default_factory=list)
    summary: str = ''

    def to_dict(self) -> dict:
        return {
            'intent': self.intent,
            'confidence': round(self.confidence, 2),
            'reason': self.reason,
            'text': self.text,
            'needs_confirmation': self.needs_confirmation,
            'suggestions': list(self.suggestions),
            'context_claim_id': self.context_claim_id,
            'summary': self.summary,
            'steps': self.steps,
        }


def _marked(text: str, marks: tuple[str, ...]) -> str | None:
    return next((m for m in marks if m in text), None)


def classify(text: str, *, context_claim_id: str | None = None) -> Intent:
    """Read a sentence as one of the four intents, or admit that it cannot.

    Order matters and is deliberate: an explicit phrasing first ("记住", "不对"), then
    the shape of the sentence, then — for a plain declarative about something the wiki
    knows — a statement of fact, which is a correction *in intent* because the only
    thing a user can mean by asserting something is that the wiki should hold it.
    """
    sentence = ' '.join(str(text or '').split())
    if not sentence:
        return Intent(UNKNOWN, 0.0, '空输入', sentence, suggestions=_SUGGESTIONS)

    # A follow-up on something the user is looking at: no subject named, but the
    # context supplies it. This is what makes "这个不对，应该是……" work.
    if context_claim_id and _marked(sentence, _CORRECT_MARKS):
        return _planned(CORRECT, 0.9, f'针对当前查看的知识的更正（「{_marked(sentence, _CORRECT_MARKS)}」）',
                        sentence, context_claim_id=context_claim_id)

    if (mark := _marked(sentence, _KNOWLEDGE_MARKS)):
        return _planned(KNOWLEDGE, 0.85, f'明确要求记住（「{mark}」）', sentence)

    if (mark := _marked(sentence, _CORRECT_MARKS)):
        return _planned(CORRECT, 0.85, f'更正表述（「{mark}」）', sentence,
                        context_claim_id=context_claim_id)

    if (mark := _marked(sentence, _RESEARCH_MARKS)):
        return _planned(RESEARCH, 0.8, f'研究表述（「{mark}」）', sentence)

    if (mark := _marked(sentence, _QUESTION_MARKS)):
        return _planned(ASK, 0.85, f'问句（「{mark}」）', sentence)

    # A declarative naming a known subject and a registered predicate is a statement
    # about the world; the only thing that can mean is "hold this". Confirmed first,
    # because it writes.
    intent = _from_signals(sentence)
    if intent is not None:
        return intent

    return Intent(UNKNOWN, 0.0, '既不是问句，也没有可识别的断言或研究意图', sentence,
                  suggestions=_SUGGESTIONS)


def _from_signals(sentence: str) -> Intent | None:
    """The registry-driven reading: does this sentence name something we know *about*?

    Reuses the query router's signal extraction rather than reimplementing subject and
    predicate matching — the same extraction that makes "苹果 CEO 是谁" a lookup also
    makes "苹果的 CEO 是约翰·特努斯" recognisable as a statement about the same slot.
    """
    try:
        from .workflows.ask_workflow import plan_question
        signals, _plan = plan_question(sentence)
    except Exception:  # noqa: BLE001 - routing must never break the request
        return None
    if signals.predicates:
        # A registered relation named in a non-question is a statement about the world.
        # The subject is reported when we recognise it, but a sentence does not have to
        # be about something already stored to be worth holding — that is how new
        # subjects get introduced in the first place.
        slot = (f'{signals.subject} · {signals.predicates[0]}' if signals.subject
                else signals.predicates[0])
        return _planned(CORRECT, 0.7, f'关于「{slot}」的断言', sentence)
    return None


def _planned(intent: str, confidence: float, reason: str, sentence: str, *,
             context_claim_id: str | None = None) -> Intent:
    steps, summary = _route(intent, sentence)
    return Intent(intent, confidence, reason, sentence,
                  needs_confirmation=intent in CONFIRM_INTENTS,
                  context_claim_id=context_claim_id, steps=steps, summary=summary)


def _route(intent: str, sentence: str) -> tuple[list[dict], str]:
    """Which *existing* endpoint carries this out, and what to tell the user meanwhile.

    A description, not an implementation. One Box routes; the workflows do the work —
    so there is no second Ask, a second Research or a second import living in here, and
    a fix to any of them reaches One Box for free.
    """
    if intent == ASK:
        return ([{'method': 'POST', 'endpoint': '/api/ask', 'body': {'question': sentence}}],
                '正在查询你的知识…')
    if intent == KNOWLEDGE:
        return ([{'method': 'POST', 'endpoint': '/api/documents',
                  'body': {'title': sentence[:60] or '随手记', 'content': sentence,
                           'source_type': 'note'}}],
                '正在整理为知识…')
    if intent == CORRECT:
        return ([{'method': 'POST', 'endpoint': '/api/correction/plan', 'body': {'text': sentence}}],
                '正在对照现有知识…')
    if intent == RESEARCH:
        return ([{'method': 'POST', 'endpoint': '/api/research',
                  'body': {'question_text': sentence, 'question_id': None}}],
                '正在研究…')
    return [], ''
