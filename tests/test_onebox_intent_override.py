"""One Box: a wrong reading has to stay cheap to fix (Step 19, P0-1).

The audit found the fix path itself broken: the UI let the user say 「其实我是想问」, then
cleared the plan's steps while still offering 「按这个执行」 — an action with nothing behind
it. Two invariants are asserted here so that cannot come back:

* **a chosen intent always routes to real work** — every overridable intent yields at least
  one executable step, and `needs_confirmation` reflects the same table the reading path
  uses (only reading acts on its own);
* **every endpoint the route table names is a registered endpoint** — the correction route
  pointed at ``/api/correction/plan``, which does not exist, so One Box's correction branch
  could never render. A planned step that 404s is a no-op wearing a plan's clothes.

No model call is involved anywhere in this file.
"""
from __future__ import annotations

import pytest


def _intent(text: str, **kwargs) -> dict:
    from app.intent import classify
    return classify(text, **kwargs).to_dict()


# --------------------------------------------------------------------------- #
# the override itself
# --------------------------------------------------------------------------- #

OVERRIDABLE = ('ask', 'knowledge', 'research', 'correct')

SENTENCE = '苹果的 CEO'


@pytest.mark.parametrize('intent', OVERRIDABLE)
def test_chosen_intent_always_produces_executable_steps(intent):
    plan = _intent(SENTENCE, intent=intent)

    assert plan['intent'] == intent
    assert plan['steps'], f'{intent} must route to something executable'
    assert all(step.get('endpoint') for step in plan['steps'])
    # 覆盖读取结果的理由要说清是「按你选择的意图」，而不是伪造一个判断依据。
    assert '按你选择的意图' in plan['reason']


def test_only_reading_acts_on_itself():
    """覆盖到问句可以直接执行；覆盖到三种写入都必须先确认。

    这与 ``CONFIRM_INTENTS`` 是同一份判断——如果这里和读取路径给出不同答案，
    用户在改判之后就会遇到与改判之前不一样的风险。
    """
    from app.intent import CONFIRM_INTENTS

    for intent in OVERRIDABLE:
        plan = _intent(SENTENCE, intent=intent)
        assert plan['needs_confirmation'] is (intent in CONFIRM_INTENTS), intent


def test_override_reuses_the_sentence_untouched():
    """改判只换路线，不重写用户的话——否则「不用重打一遍」就是空话。"""
    plan = _intent(SENTENCE, intent='research')

    assert plan['text'] == SENTENCE
    assert plan['steps'][0]['body']['question_text'] == SENTENCE


def test_empty_sentence_has_nothing_to_route_even_when_forced():
    plan = _intent('   ', intent='ask')

    assert plan['intent'] == 'unknown'
    assert plan['steps'] == []


@pytest.mark.parametrize('bogus', ['unknown', 'nonsense', '', None])
def test_a_non_destination_cannot_be_forced(bogus):
    """``unknown`` 不是一个目的地：强行选它没有执行路径，所以退回正常读取。"""
    plan = _intent('苹果的 CEO 是谁？', intent=bogus)

    assert plan['intent'] == 'ask'
    assert plan['steps']


# --------------------------------------------------------------------------- #
# the route table must name endpoints that exist
# --------------------------------------------------------------------------- #

def test_every_routed_endpoint_is_registered():
    """任何一条路线指向不存在的端点，就等于一个伪装成计划的 no-op。

    This is the regression that would have caught ``/api/correction/plan``.
    """
    from fastapi.routing import APIRoute

    from app.intent import _route, INTENTS
    from app.main import app

    registered = {r.path for r in app.routes if isinstance(r, APIRoute)}
    planned: list[str] = []
    for intent in INTENTS:
        steps, _summary = _route(intent, '一句话')
        planned.extend(step['endpoint'] for step in steps)

    assert planned, 'the route table must describe at least one endpoint'
    assert set(planned) <= registered, set(planned) - registered


def test_correction_route_points_at_the_real_planning_endpoint():
    """The specific path that used to 404, pinned so it cannot drift back."""
    from app.intent import _route, CORRECT

    steps, _summary = _route(CORRECT, '苹果的 CEO 是 John Ternus')

    assert steps[0]['endpoint'] == '/api/knowledge/corrections'


# --------------------------------------------------------------------------- #
# through the API, the way the UI actually calls it
# --------------------------------------------------------------------------- #

def _post(payload: dict) -> dict:
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app).post('/api/onebox/intent', json=payload).json()


def test_api_accepts_a_chosen_intent():
    body = _post({'text': SENTENCE, 'intent': 'ask'})

    assert body['intent'] == 'ask'
    assert body['needs_confirmation'] is False
    assert body['steps'], 'the UI must be able to execute what it just planned'
    assert body['steps'][0]['endpoint'] == '/api/ask'


def test_api_without_a_chosen_intent_still_reads_the_sentence():
    """缺省行为不变：没有人指定意图时，还是靠句子本身判断。"""
    body = _post({'text': '苹果的 CEO 是谁？'})

    assert body['intent'] == 'ask'
    assert body['reason'] != '按你选择的意图'


def test_api_rejects_a_correction_route_that_cannot_be_executed():
    """纠正的规划端点必须真的存在——这是「按这个执行」能被相信的前提。"""
    from fastapi.testclient import TestClient
    from app.main import app

    body = _post({'text': '苹果的 CEO 是 John Ternus', 'intent': 'correct'})
    assert body['intent'] == 'correct'

    step = body['steps'][0]
    # 规划端点只读、不落库，所以在这个测试里可以直接调用它（模型未配置时会明确报错，
    # 而不是 404 —— 404 才是我们要防的那种"以为自己有计划"）。
    response = TestClient(app).post(step['endpoint'], json=step['body'])
    assert response.status_code != 404, step['endpoint']
