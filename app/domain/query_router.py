"""Query Router — decide *how* a question should be answered before answering it.

QA 首先是 Retrieval / 结构化查询问题，其次才是 Generation 问题。不是每个问题都该
直接丢给大模型：简单事实查询在知识库里**本来就有确定答案**，让模型去「读一遍再复述」
既慢又可能引入幻觉。

    Question -> Query Router -> ┌ FACT_LOOKUP          0 LLM（直查 Claim）
                                ├ STRUCTURED_REASONING  少量 LLM（含时序/多跳）
                                ├ EVIDENCE_SYNTHESIS    常规检索 + 生成
                                └ RESEARCH              多步骤研究

路由是**确定性**的（ADR-010）：它是纯函数，只读 ``QuerySignals``，不看数据库、不调模型。
信号（主语、谓词候选、是否存在直接 Claim）由调用方解析后传入，因此路由器本身可被完整
单测，且同样的输入永远得到同样的路由。

Rule order matters and is deliberate — the first matching rule wins:

1. 研究性标记（未来/预测/规划/风险…）      -> RESEARCH
2. 多跳（A 的 B 的 C）                      -> STRUCTURED_REASONING
3. 时序（上一任/以前/was…）                 -> STRUCTURED_REASONING
4. 唯一主语 + 唯一已注册谓词 + 存在直接 Claim -> FACT_LOOKUP（0 LLM）
5. 综合标记（为什么/如何/对比/评估…）        -> EVIDENCE_SYNTHESIS
6. 兜底                                    -> EVIDENCE_SYNTHESIS

规则 4 刻意要求**唯一**谓词：多个谓词竞争说明问题有歧义，那不是「事实直查」，
不能靠猜一个谓词给出一个看起来确定的答案。

Pure logic: no database, no framework, no LLM (docs/adr/ADR-002, ADR-010, ADR-013).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

FACT_LOOKUP = 'fact_lookup'
STRUCTURED_REASONING = 'structured_reasoning'
EVIDENCE_SYNTHESIS = 'evidence_synthesis'
RESEARCH = 'research'

ROUTES: tuple[str, ...] = (FACT_LOOKUP, STRUCTURED_REASONING, EVIDENCE_SYNTHESIS, RESEARCH)

# Marker vocabularies. Kept in one place so the routing rules stay readable and
# a new marker is a data edit rather than a new ``if`` branch.
RESEARCH_MARKERS: tuple[str, ...] = (
    '预测', '未来', '规划', '风险', '建议', '策略', '趋势', '展望',
    'forecast', 'predict', 'roadmap', 'strategy', 'recommend', 'outlook',
)
SYNTHESIS_MARKERS: tuple[str, ...] = (
    '为什么', '为何', '如何', '怎么样', '怎么', '对比', '比较', '评估', '分析',
    '总结', '综述', '影响', '利弊', '区别', '有什么',
    'why', 'how', 'compare', 'evaluate', 'analyze', 'analyse', 'summar',
    'impact', 'pros and cons', 'difference',
)
TEMPORAL_MARKERS: tuple[str, ...] = (
    '上一任', '前任', '之前', '以前', '原来', '曾经', '当时', '历史上', '更早',
    'former', 'previous', 'prior', 'used to', 'was ', 'earlier', 'before',
)
# "A 的 B 的 C" style chaining: two possessive hops in one question. A single
# "的" ("苹果公司的 CEO") is one hop and stays a fact lookup; two of them, with
# only the bridging relation between, means the answer needs a second lookup.
_MULTI_HOP_CHAIN = re.compile(r'的[^，。？！、：；（）「」【】“”]{1,10}的')
MULTI_HOP_MARKERS: tuple[str, ...] = ('of the ', 'of whose')


@dataclass(frozen=True)
class QuerySignals:
    """What the caller already knows about the question. Pure data.

    ``subject`` is a resolved entity name (or ``None`` when the question names no
    entity the knowledge base knows); ``predicates`` are *registered* predicate
    candidates the question maps to.
    """

    question: str
    subject: str | None = None
    predicates: tuple[str, ...] = ()
    direct_claim_available: bool = False
    temporal: bool = False
    multi_hop: bool = False


@dataclass(frozen=True)
class QueryPlan:
    """The routing verdict, plus why — so the answer can explain itself."""

    route: str
    reasons: tuple[str, ...] = ()
    signals_used: dict = field(default_factory=dict)

    @property
    def llm_expected(self) -> bool:
        """False only for a direct structured lookup: those need no model."""
        return self.route != FACT_LOOKUP

    def to_dict(self) -> dict:
        return {
            'route': self.route,
            'llm_expected': self.llm_expected,
            'reasons': list(self.reasons),
        }


def _contains(question: str, markers: tuple[str, ...]) -> str | None:
    haystack = str(question or '').casefold()
    for marker in markers:
        if marker.casefold() in haystack:
            return marker
    return None


def has_temporal_intent(question: str) -> bool:
    """Does the question ask about a *past* state ("who was", "上一任")?"""
    return _contains(question, TEMPORAL_MARKERS) is not None


def has_multi_hop_intent(question: str) -> bool:
    """Does the question chain two relations ("A 的 B 的 C")?"""
    text = str(question or '')
    if _MULTI_HOP_CHAIN.search(text):
        return True
    return _contains(text, MULTI_HOP_MARKERS) is not None


def classify(signals: QuerySignals) -> QueryPlan:
    """Route one question. Deterministic and side-effect free."""
    question = str(signals.question or '')
    reasons: list[str] = []

    research_hit = _contains(question, RESEARCH_MARKERS)
    if research_hit:
        reasons.append(f'research marker: {research_hit!r}')
        return QueryPlan(route=RESEARCH, reasons=tuple(reasons),
                         signals_used={'marker': research_hit})

    if signals.multi_hop:
        reasons.append('multi-hop relation chain')
        return QueryPlan(route=STRUCTURED_REASONING, reasons=tuple(reasons))
    temporal_hit = _contains(question, TEMPORAL_MARKERS)
    if signals.temporal or temporal_hit:
        reasons.append(f'temporal intent: {temporal_hit!r}' if temporal_hit else 'temporal intent')
        return QueryPlan(route=STRUCTURED_REASONING, reasons=tuple(reasons),
                         signals_used={'marker': temporal_hit})

    if (signals.direct_claim_available and signals.subject
            and len(signals.predicates) == 1):
        reasons.append('one subject, one registered predicate, a current claim exists')
        return QueryPlan(route=FACT_LOOKUP, reasons=tuple(reasons),
                         signals_used={'subject': signals.subject,
                                       'predicate': signals.predicates[0]})

    synthesis_hit = _contains(question, SYNTHESIS_MARKERS)
    if synthesis_hit:
        reasons.append(f'synthesis marker: {synthesis_hit!r}')
    else:
        reasons.append('no direct lookup possible; grounded synthesis needed')
    return QueryPlan(route=EVIDENCE_SYNTHESIS, reasons=tuple(reasons),
                     signals_used={'marker': synthesis_hit})
