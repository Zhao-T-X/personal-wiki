"""Token estimation for context planning.

The providers used by LLM-Wiki (DeepSeek / OpenAI-compatible) do not expose a
local tokenizer here, and pulling one in would add a heavy dependency for what is
only a *planning* signal. So this module provides a cheap deterministic estimate:

- CJK / full-width characters count as ~1 token each (they are dense),
- everything else counts as ~1 token per 4 characters.

Actual usage reported by the provider is the source of truth for accounting
(`usage.prompt_tokens`); this estimate is for budgeting and tracing before a call.
"""
from __future__ import annotations

import re
from typing import Iterable

_CJK = re.compile(r'[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]')
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str | None) -> int:
    """Estimate the token count of one string. Never negative."""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + (other + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def estimate_many(texts: Iterable[str | None]) -> int:
    """Estimate the combined token count of several strings."""
    return sum(estimate_tokens(t) for t in texts)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Return the longest prefix of ``text`` that fits in ``max_tokens``.

    Cuts on a line boundary when one is available near the limit, so a trimmed
    section still ends on a complete rule or sentence where possible.
    """
    if max_tokens <= 0:
        return ''
    if estimate_tokens(text) <= max_tokens:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimate_tokens(text[:mid]) <= max_tokens:
            low = mid
        else:
            high = mid - 1
    cut = text[:low]
    newline = cut.rfind('\n')
    if newline > len(cut) // 2:
        cut = cut[:newline]
    return cut.rstrip()
