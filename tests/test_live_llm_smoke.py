"""Live LLM smoke — a handful of SHORT sentences against the real model.

Structure-only sanity check: the model returns the five envelope keys and every claim
carries a predicate. It deliberately pins no semantics.

The semantic contract smoke (Step 13) lives in ``tests/test_live_semantic_contract.py``
and is the one marked ``smoke``, so ``pytest -m smoke`` runs exactly one live suite and
stays inside its cost budget. This file runs only when you ask for all live tests:

    pytest -m live_llm

Keep the inputs tiny — these are the files that spend money on every run.
"""
import asyncio

import pytest

from app.llm import extract

pytestmark = [pytest.mark.live_llm]

# Short, semantic-boundary micro cases: entity, literal, concept, negation, technical.
_SENTENCES = [
    ('ceo', '苹果现任 CEO 是 John Ternus。'),
    ('literal', '最大 chunk 为 8192 tokens。'),
    ('concept', '渐进式加载是一种上下文机制。'),
    ('negation', '该服务不支持 Windows。'),
    ('technical', 'GomsInventoryPagingRequestDTO 用于库存分页查询。'),
]


@pytest.mark.parametrize('cid,text', _SENTENCES, ids=[s[0] for s in _SENTENCES])
def test_smoke_extraction(cid, text):
    result = asyncio.run(extract([{'id': cid, 'content': text}]))
    assert set(result) == {'entities', 'claims', 'events', 'ideas', 'questions'}
    # Structure only — we never pin the model's exact wording (that is what replay is for).
    for claim in result['claims']:
        assert claim.get('predicate')
