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
    'app.context.trace',
    'app.context.providers.base',
    'app.context.providers.evidence',
    'app.context.providers.knowledge',
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


CHUNK = (
    "GOMS 提供订单与库存分页查询。订单分页支持按时间过滤。"
    "库存分页通过 IBU_WMS_SERVER 常量拼装请求路径，并使用 esbApiClient 执行 POST 调用。"
    "当 GOMS 请求失败时，控制器抛出 GOMS_REQUEST_FAIL 异常。"
)


def _hit(**kwargs):
    from app.context.providers import EvidenceHit

    base = dict(chunk_id='c1', document_id='d1', title='GOMS 设计文档',
                quote='库存分页通过 IBU_WMS_SERVER 常量拼装请求路径，并使用 esbApiClient 执行 POST 调用。',
                chunk_content=CHUNK, score=1.0)
    base.update(kwargs)
    return EvidenceHit.from_dict(base)


def test_level_one_is_the_default_and_only_carries_the_quote(tmp_path):
    _reload(tmp_path)
    from app.context.providers import apply_escalation, render

    # Two quoted hits are enough grounding, so neither has to grow beyond L1.
    hit = apply_escalation([_hit(), _hit(chunk_id='c2', quote='another quote', claims=[])])[0]

    assert hit.level == 1
    assert hit.reason == 'quote is enough'
    text = render(hit)
    assert '"库存分页通过' in text
    assert 'esbApiClient' in text
    assert '订单分页支持按时间过滤' not in text          # the rest of the chunk stays out
    assert '[doc:d1 chunk:c1] GOMS 设计文档' in text
    assert len(text) < len(CHUNK)


def test_missing_quote_falls_back_to_the_paragraph(tmp_path):
    _reload(tmp_path)
    from app.context.providers import apply_escalation, render

    hit = apply_escalation([_hit(quote='')])[0]

    assert hit.level == 3
    assert 'no stored quote' in hit.reason
    assert CHUNK[:20] in render(hit)


def test_low_confidence_and_conditional_claims_escalate_to_level_two(tmp_path):
    _reload(tmp_path)
    from app.context.providers import apply_escalation

    low = _hit(claims=[{'predicate': 'uses', 'confidence': 0.4, 'source_quote': 'x'}])
    assert apply_escalation([low])[0].level == 2
    assert 'low confidence' in apply_escalation([low])[0].reason

    conditional = _hit(claims=[{'predicate': 'uses', 'confidence': 0.9, 'modality': 'conditional',
                                'source_quote': 'x'}])
    assert apply_escalation([conditional])[0].level == 2

    certain = _hit(claims=[{'predicate': 'uses', 'confidence': 0.9, 'modality': 'asserted',
                            'source_quote': 'x'}])
    other = _hit(chunk_id='c2', quote='another quote', claims=[])
    apply_escalation([certain, other])
    assert certain.level == 1


def test_conflicting_evidence_escalates_only_the_hits_involved(tmp_path):
    _reload(tmp_path)
    from app.context.providers import apply_escalation

    hits = [
        _hit(chunk_id='c1', claims=[{'subject_name': 'RAG', 'predicate': 'improves',
                                     'polarity': 'positive', 'confidence': 0.9,
                                     'source_quote': 'x'}]),
        _hit(chunk_id='c2', claims=[{'subject_name': 'RAG', 'predicate': 'improves',
                                     'polarity': 'negative', 'confidence': 0.9,
                                     'source_quote': 'y'}]),
        _hit(chunk_id='c3', quote='unrelated quote', claims=[]),
    ]
    apply_escalation(hits)

    by_id = {h.chunk_id: h for h in hits}
    assert by_id['c1'].level == 3 and by_id['c2'].level == 3
    assert 'conflicting evidence' in by_id['c1'].reason
    assert by_id['c3'].level == 1                       # not dragged up with them


def test_same_predicate_on_different_subjects_is_not_a_conflict(tmp_path):
    _reload(tmp_path)
    from app.context.providers import apply_escalation, conflicting_hits

    hits = [
        _hit(chunk_id='c1', claims=[{'subject_name': 'A', 'predicate': 'uses',
                                     'polarity': 'positive', 'source_quote': 'x'}]),
        _hit(chunk_id='c2', claims=[{'subject_name': 'B', 'predicate': 'uses',
                                     'polarity': 'negative', 'source_quote': 'y'}]),
    ]
    assert conflicting_hits(hits) == set()
    apply_escalation(hits)
    assert {h.level for h in hits} == {1}


def test_single_hit_escalates_because_evidence_is_thin(tmp_path):
    _reload(tmp_path)
    from app.context.providers import apply_escalation

    assert apply_escalation([_hit()])[0].level == 2      # only one hit → not enough


def test_two_quoted_hits_stay_at_level_one(tmp_path):
    _reload(tmp_path)
    from app.context.providers import apply_escalation

    hits = [_hit(chunk_id='c1', quote='quote one', claims=[]),
            _hit(chunk_id='c2', quote='quote two', claims=[])]
    apply_escalation(hits)
    assert [h.level for h in hits] == [1, 1]


def test_requested_level_wins_but_is_capped(tmp_path):
    _reload(tmp_path)
    from app.context.providers import apply_escalation

    hits = [_hit(chunk_id='c1', quote='a'), _hit(chunk_id='c2', quote='b')]
    apply_escalation(hits, requested=4)
    assert [h.level for h in hits] == [4, 4]
    assert 'requested by the caller' in hits[0].reason


def test_evidence_items_report_their_level_and_stay_small(tmp_path):
    _reload(tmp_path)
    from app.context.providers import EvidenceProvider, apply_escalation
    from app.context import PLANNER
    from app.runtime import TaskContext, features_for

    hits = apply_escalation([_hit(), _hit(chunk_id='c2', quote='another quote', claims=[])])
    task = TaskContext(task_type='ask', agent='KnowledgeAgent',
                       features=features_for('KnowledgeAgent', 'ask'))
    items = EvidenceProvider(hits).provide(task, PLANNER.plan(task))

    assert len(items) == 2
    assert all(item.type == 'evidence' for item in items)
    assert items[0].metadata['escalation_level'] == 1
    assert 'L1' in items[0].reason
    assert sum(i.estimated_tokens for i in items) < 200   # quotes, not chunks


def test_response_shape_stays_compatible_with_the_ui(tmp_path):
    _reload(tmp_path)
    from app.context.providers import apply_escalation

    hit = apply_escalation([_hit(), _hit(chunk_id='c2', quote='q2', claims=[])])[0]
    payload = hit.to_response()

    for key in ('id', 'document_id', 'title', 'content', 'method', 'start_offset', 'end_offset'):
        assert key in payload, f'the UI reads {key}'
    assert payload['id'] == 'c1'
    assert payload['content'] == hit.rendered
    assert payload['escalation_level'] == 1


def test_render_keeps_other_claims_but_not_the_quoted_one(tmp_path):
    _reload(tmp_path)
    from app.context.providers import apply_escalation, render

    quote = '库存分页通过 IBU_WMS_SERVER 常量拼装请求路径。'
    hit = _hit(quote=quote, claims=[
        {'subject_name': 'GomsOrderInfoHttpService', 'predicate': 'uses',
         'object_name': 'esbApiClient', 'source_quote': quote},
        {'subject_name': 'GomsPagingController', 'predicate': 'provides',
         'object_name': '库存分页查询接口', 'source_quote': 'another sentence'},
    ])
    apply_escalation([hit, _hit(chunk_id='c2', quote='q2', claims=[])])
    text = render(hit)

    assert 'GomsPagingController provides 库存分页查询接口' in text
    assert 'GomsOrderInfoHttpService uses esbApiClient' not in text   # already in the quote
