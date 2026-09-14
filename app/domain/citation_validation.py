"""Citation Validation — the last quality-engineering item from the v0.2 brief.

The QA pipeline already produces a *grounded* answer plus chunk-level
``citations``. This module checks, at the answer-text level, whether that
answer is actually backed by the sources it cites — the part the brief said
"答案文本级校验须 LLM".

Three dimensions are deterministic (no model, so they are testable and cannot
drift from the data):

* ``locatability`` — every cited chunk really exists and has content
* ``coverage``     — the answer cites its sources inline (``[doc:.. chunk:..]``),
                    and the cited chunks are the ones referenced in the text
* ``currentness``  — the cited chunk is not the source of an already *superseded*
                    claim (i.e. a later correction replaced that assertion)

The fourth, ``support`` (does the answer *follow* from the cited evidence, or
did the model add facts?), needs judgement, so it is delegated to an LLM — but
the LLM only *proposes*. The model call is injected as a ``Callable`` so this
Domain module stays free of any framework/model dependency (the rule: LLM 不入
Domain). When no callable is supplied (or the model is unavailable) the report
degrades honestly: ``support`` is ``None`` and the other three dimensions are
renormalised.

Result: a :class:`CitationReport` — overall score, grade, flags, and (when
available) a per-assertion grounding verdict. The API and the QA view surface it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .refusal import RefusalDecision, RefusalKind, classify_refusal

DIMENSIONS = ('locatability', 'coverage', 'currentness', 'support')

# Weights sum to 1.0. ``support`` is the LLM judgement and carries the most
# weight; if it is unavailable the remaining three are renormalised to 1.0.
_WEIGHTS: dict[str, float] = {
    'locatability': 0.25,
    'coverage': 0.30,
    'currentness': 0.15,
    'support': 0.30,
}

_INLINE_CITATION = re.compile(r'\[doc:[^\]]*chunk:[^\]]*\]', re.IGNORECASE)
_CHUNK_IN_MARKER = re.compile(r'chunk:\s*([^\]\s,]+)', re.IGNORECASE)


@dataclass
class CitationReport:
    dimensions: dict[str, float | None]
    overall: float
    grade: str
    flags: list[str]
    grounding: dict | None = None
    issues: list[str] = field(default_factory=list)
    # The shared refusal semantics verdict (kind/confidence/reason/detector), so
    # consumers never have to re-derive "was this a refusal?" from flags (ADR-014).
    refusal: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'dimensions': self.dimensions,
            'overall': round(self.overall, 4),
            'grade': self.grade,
            'flags': self.flags,
            'grounding': self.grounding,
            'issues': self.issues,
            'refusal': self.refusal,
        }


def _cited_ids(citations: list[dict]) -> set[str]:
    return {str(c.get('chunk_id') or '') for c in citations if c.get('chunk_id')}


def _referenced_ids(answer: str) -> set[str]:
    out: set[str] = set()
    for marker in _INLINE_CITATION.findall(answer or ''):
        m = _CHUNK_IN_MARKER.search(marker)
        if m:
            out.add(m.group(1))
    return out


def _refusal_decision(answer: str, citations: list[dict]) -> RefusalDecision:
    """The shared refusal semantics, classified by the *runtime* detector.

    This module used to decide for itself — and its shortcut ("no citations and
    under 80 characters is a refusal") mislabelled ordinary short answers. The
    definition now lives once, in ``domain/refusal`` (ADR-014); only the
    ``detector`` label distinguishes runtime from evaluation consumption.
    """
    return classify_refusal(answer, citations=citations, detector='runtime')


def _locatability_score(citations: list[dict], *, conn=None) -> float:
    if not citations:
        return 0.0
    from ..repositories import EvidenceRepository
    ok = 0
    for c in citations:
        cid = c.get('chunk_id')
        if not cid:
            continue
        ev = EvidenceRepository(conn).provenance(cid) if conn is not None else None
        # Without a DB handle we can only confirm the citation is well-formed.
        if conn is None:
            ok += 1
        elif ev and (ev.get('content') or '').strip():
            ok += 1
    return ok / len(citations)


def _coverage_score(answer: str, citations: list[dict], *, conn=None) -> float:
    cited = _cited_ids(citations)
    if _refusal_decision(answer, citations).is_safety_stop:
        # A safety stop asserts nothing, so there is nothing to trace. Note this is
        # now the *only* exemption: a short answer that simply forgot its citations
        # is no longer excused (it is untraceable, not a refusal).
        return 1.0
    if not cited:
        return 0.2  # an answer with no citations cannot be traced
    referenced = _referenced_ids(answer)
    if not referenced:
        return 0.45  # evidence was retrieved but the answer does not cite it inline
    mark_cov = len(cited & referenced) / len(cited)
    return 0.5 + 0.5 * mark_cov


def _currentness_score(citations: list[dict], *, conn=None) -> float:
    if not citations:
        return 0.0
    if conn is None:
        return 1.0  # nothing to supersede-check without a connection
    from ..repositories import ClaimRepository
    from .claim_state import is_current
    stale = 0
    total = 0
    for c in citations:
        cid = c.get('chunk_id')
        if not cid:
            continue
        total += 1
        claims = ClaimRepository(conn).for_chunk(cid, limit=10)
        # Currentness is defined once, in domain.claim_state (§30): a cited chunk
        # is stale when everything it backs has stopped being current.
        if any(not is_current(cl) for cl in claims):
            stale += 1
    if total == 0:
        return 0.0
    return (total - stale) / total


def _grade(overall: float) -> str:
    if overall >= 0.85:
        return 'A'
    if overall >= 0.70:
        return 'B'
    if overall >= 0.50:
        return 'C'
    return 'D'


def _parse_grounding(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    claims = data.get('claims') or data.get('assertions') or []
    assertions = [
        {'claim': str(a.get('claim') or a.get('text') or ''),
         'supported': bool(a.get('supported')),
         'citation': a.get('citation')}
        for a in claims if isinstance(a, dict)
    ]
    return {
        'grounded': bool(data.get('grounded')),
        'rationale': str(data.get('rationale') or ''),
        'assertions': assertions,
    }


def validate_citations(question: str, answer: str, citations: list[dict],
                       *, llm: Callable[[str], str] | None = None, conn=None) -> CitationReport:
    """Validate a QA answer against its citations.

    ``llm`` is an injected callable ``(prompt: str) -> str`` returning the
    model's JSON verdict (grounding). The Domain never imports the model; if
    ``llm`` is ``None`` or raises, ``support`` is left as ``None`` and the
    report keeps the three deterministic dimensions.
    """
    loc = _locatability_score(citations, conn=conn)
    cov = _coverage_score(answer, citations, conn=conn)
    curr = _currentness_score(citations, conn=conn)

    support: float | None = None
    grounding: dict | None = None
    llm_unavailable = False
    if llm is not None:
        try:
            prompt = _grounding_prompt(question, answer, citations, conn=conn)
            grounding = _parse_grounding(llm(prompt))
            if grounding is None:
                llm_unavailable = True
            else:
                supported = [a for a in grounding['assertions'] if a['supported']]
                pool = grounding['assertions'] or ([{'supported': grounding['grounded']}] if grounding.get('grounded') is not None else [])
                if pool:
                    support = len(supported) / len(pool)
                elif grounding.get('grounded'):
                    support = 1.0
                else:
                    support = 0.0
        except Exception:
            llm_unavailable = True
            grounding = None

    dims: dict[str, float | None] = {
        'locatability': round(loc, 4),
        'coverage': round(cov, 4),
        'currentness': round(curr, 4),
        'support': (round(support, 4) if support is not None else None),
    }

    if support is None:
        w = {k: _WEIGHTS[k] for k in ('locatability', 'coverage', 'currentness')}
        norm = sum(w.values())
        overall = sum(w[k] * dims[k] for k in w) / norm
    else:
        overall = sum(_WEIGHTS[k] * (dims[k] or 0.0) for k in DIMENSIONS)

    refusal = _refusal_decision(answer, citations)

    flags: list[str] = []
    # The two kinds are reported separately: "the knowledge base lacks evidence" is
    # a different (and better) outcome than "I refuse to answer" (ADR-014).
    if refusal.kind is RefusalKind.REFUSAL:
        flags.append('refusal')
    if refusal.kind is RefusalKind.INSUFFICIENT_EVIDENCE:
        flags.append('insufficient_evidence')
    if not citations:
        flags.append('no_citations')
    if citations and not _referenced_ids(answer):
        flags.append('no_inline_citations')
    if citations and _cited_ids(citations) - _referenced_ids(answer):
        flags.append('uncited_evidence')
    if citations and curr < 1.0:
        flags.append('stale_source')
    if llm_unavailable:
        flags.append('llm_unavailable')
    if support is not None and support < 0.6:
        flags.append('low_support')
    if grounding and any(not a['supported'] for a in grounding['assertions']):
        flags.append('unsupported_claims')

    issues: list[str] = []
    if 'no_citations' in flags:
        issues.append('回答没有任何引用，无法溯源。')
    if 'no_inline_citations' in flags:
        issues.append('检索到了证据，但回答正文里没有内联引用（[doc:.. chunk:..]）。')
    if 'uncited_evidence' in flags:
        issues.append('部分被引用的片段没有在回答正文里标注出来。')
    if 'stale_source' in flags:
        issues.append('引用的片段来自一条已被取代（superseded）的断言，知识可能已更新。')
    if 'unsupported_claims' in flags:
        issues.append('有断言在引用证据中找不到依据，可能存在幻觉。')
    if 'llm_unavailable' in flags:
        issues.append('语义校验（support）所需的语言模型不可用，仅给出确定性三项评分。')

    return CitationReport(dimensions=dims, overall=round(overall, 4),
                          grade=_grade(overall), flags=flags,
                          grounding=grounding, issues=issues,
                          refusal=refusal.to_dict())


def _grounding_prompt(question: str, answer: str, citations: list[dict], *, conn=None) -> str:
    """Build the LLM prompt: does the answer follow from the cited evidence?"""
    from ..repositories import EvidenceRepository
    blocks: list[str] = []
    for i, c in enumerate(citations, 1):
        cid = c.get('chunk_id')
        content = ''
        if conn is not None and cid:
            ev = EvidenceRepository(conn).provenance(cid)
            content = (ev or {}).get('content') or ''
        blocks.append(f"[{i}] doc:{c.get('document_id')} chunk:{cid} {c.get('title') or ''}\n{content}")
    evidence = '\n\n'.join(blocks) or '（无引用）'
    return (
        '你是引用校验器。判断下面的回答是否由其引用的证据支持，'
        '是否出现了证据中没有的断言（幻觉）。\n'
        '只回复 JSON：{"grounded": true|false, "rationale": "...", '
        '"claims": [{"claim": "回答中的一条事实断言", "supported": true|false, '
        '"citation": "对应的 [doc:.. chunk:..] 或 null}]}\n\n'
        f'问题：{question}\n\n回答：{answer}\n\n引用证据：\n{evidence}'
    )
