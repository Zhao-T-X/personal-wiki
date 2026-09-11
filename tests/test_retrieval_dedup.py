from __future__ import annotations

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_RELOAD_MODULES = (
    'app.runtime.task',
    'app.runtime',
    'app.context.tokens',
    'app.context.budget',
    'app.context.policies',
    'app.context.items',
    'app.context.value',
    'app.context.manager',
    'app.context.planner',
    'app.context.compiler',
    'app.context.providers.base',
    'app.context.providers.evidence',
    'app.context.providers',
    'app.context',
)


def _reload(tmp_path):
    os.environ['DATABASE_PATH'] = str(tmp_path / 'wiki.db')
    os.environ['SETTINGS_PATH'] = str(tmp_path / 'settings.json')
    import app.config
    import app.db
    importlib.reload(app.config)
    importlib.reload(app.db)
    for name in _RELOAD_MODULES:
        importlib.reload(importlib.import_module(name))
    app.db.init_db()
    return app.db


def _hit(chunk_id, doc_id='d1', quote='', score=1.0, content='body'):
    from app.context.providers import EvidenceHit

    return EvidenceHit.from_dict({'chunk_id': chunk_id, 'document_id': doc_id, 'title': 't',
                                  'quote': quote, 'chunk_content': content, 'score': score})


def test_duplicate_chunks_are_dropped_once(tmp_path):
    _reload(tmp_path)
    from app.context.providers import dedup_hits

    kept, dropped = dedup_hits([_hit('c1', quote='q1'), _hit('c1', quote='q1'), _hit('c2', quote='q2')])

    assert [h.chunk_id for h in kept] == ['c1', 'c2']
    assert dropped == [{'id': 'c1', 'reason': 'duplicate_chunk', 'source': 'd1'}]


def test_identical_quotes_collapse_to_the_better_scoring_hit(tmp_path):
    _reload(tmp_path)
    from app.context.providers import dedup_hits

    quote = '库存分页通过 IBU_WMS_SERVER 常量拼装请求路径。'
    kept, dropped = dedup_hits([
        _hit('c1', quote=quote, score=0.5),
        _hit('c2', quote=quote, score=0.9),
        _hit('c3', quote='另一句完全不同的话。'),
    ])

    assert [h.chunk_id for h in kept] == ['c2', 'c3']      # the better hit represents the quote
    assert dropped[0]['reason'] == 'duplicate_quote'
    assert dropped[0]['kept'] == 'c2'


def test_near_identical_quotes_are_treated_as_duplicates(tmp_path):
    _reload(tmp_path)
    from app.context.providers import dedup_hits

    kept, dropped = dedup_hits([
        _hit('c1', quote='库存分页通过 IBU_WMS_SERVER 常量拼装请求路径，并使用 esbApiClient 调用。'),
        _hit('c2', quote='库存分页通过 IBU_WMS_SERVER 常量拼装请求路径, 并使用 esbApiClient 调用！'),
    ])

    assert len(kept) == 1
    assert dropped[0]['reason'] == 'duplicate_quote'


def test_hits_without_quotes_are_not_collapsed_together(tmp_path):
    _reload(tmp_path)
    from app.context.providers import dedup_hits

    kept, _ = dedup_hits([_hit('c1', quote=''), _hit('c2', quote='')])
    assert [h.chunk_id for h in kept] == ['c1', 'c2']


def test_short_quotes_are_only_matched_exactly(tmp_path):
    _reload(tmp_path)
    from app.context.providers import dedup_hits

    # 'quote 0' vs 'quote 1' differ by one character; fuzzy matching must not
    # treat one-char-different short strings as the same evidence.
    kept, dropped = dedup_hits([_hit('c1', quote='quote 0'), _hit('c2', quote='quote 1')])
    assert [h.chunk_id for h in kept] == ['c1', 'c2']
    assert dropped == []


def test_one_document_cannot_crowd_out_the_rest(tmp_path):
    _reload(tmp_path)
    from app.context.providers import dedup_hits

    hits = [_hit(f'c{i}', doc_id='d1', quote=f'quote {i}') for i in range(5)]
    hits.append(_hit('c9', doc_id='d2', quote='other doc'))
    kept, dropped = dedup_hits(hits, per_document_cap=2)

    assert [h.document_id for h in kept] == ['d1', 'd1', 'd2']
    assert sum(1 for d in dropped if d['reason'] == 'document_cap') == 3


def test_dedup_before_render_keeps_the_context_small(tmp_path):
    _reload(tmp_path)
    from app.context import PLANNER
    from app.context.providers import EvidenceProvider, apply_escalation, dedup_hits
    from app.context.providers import evidence_items
    from app.runtime import TaskContext, features_for

    quote = '库存分页通过 IBU_WMS_SERVER 常量拼装请求路径。'
    raw = [_hit('c1', quote=quote), _hit('c1', quote=quote), _hit('c2', quote=quote),
           _hit('c3', quote='另一句完全不同的话。')]
    kept, dropped = dedup_hits(raw)
    apply_escalation(kept)

    task = TaskContext(task_type='ask', agent='KnowledgeAgent',
                       features=features_for('KnowledgeAgent', 'ask'))
    items = EvidenceProvider(kept).provide(task, PLANNER.plan(task))

    assert len(raw) == 4 and len(items) == 2
    assert len(dropped) == 2
    # Two deduped quote-level items cost less than 100 tokens; the raw pack (four
    # chunks of body text) would have cost four figures.
    assert sum(i.estimated_tokens for i in items) < 100
