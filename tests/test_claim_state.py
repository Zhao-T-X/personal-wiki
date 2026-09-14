"""ClaimStateResolver: the single definition of "current knowledge" (pure logic)."""
from app.domain.claim_state import SUPERSEDED, current, history, is_current, resolve


def _claim(**kw):
    base = {'id': 'c1', 'subject_name': 'OpenAI', 'predicate': 'ceo_of',
            'status': 'verified', 'object_name': 'Alice'}
    base.update(kw)
    return base


def test_is_current_treats_only_superseded_as_history():
    assert is_current(_claim(status='verified'))
    assert is_current(_claim(status='candidate'))
    assert not is_current(_claim(status=SUPERSEDED))


def test_superseded_is_dropped_when_a_current_claim_covers_the_slot():
    claims = [_claim(id='old', status=SUPERSEDED, object_name='Sam'),
              _claim(id='new', status='verified', object_name='Alice')]
    out = resolve(claims)
    assert [c['id'] for c in out] == ['new']


def test_superseded_survives_when_nothing_current_covers_it():
    """History is not deleted: 'who *was* the CEO' still needs evidence."""
    out = resolve([_claim(id='old', status=SUPERSEDED, object_name='Sam')])
    assert len(out) == 1
    assert out[0]['lifecycle'] == SUPERSEDED


def test_unrelated_claims_pass_through_unchanged():
    claims = [_claim(id='a', subject_name='A', predicate='uses', status='candidate', object_name='B'),
              _claim(id='b', subject_name='C', predicate='uses', status=SUPERSEDED, object_name='D')]
    out = resolve(claims)
    assert [c.get('lifecycle') for c in out] == [None, SUPERSEDED]
    assert resolve([]) == []


def test_current_and_history_partition():
    claims = [_claim(id='a', status='verified'), _claim(id='b', status=SUPERSEDED)]
    assert [c['id'] for c in current(claims)] == ['a']
    assert [c['id'] for c in history(claims)] == ['b']
