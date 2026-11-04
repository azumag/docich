"""An active order no plan owns is released (v414).

g1133 2026-11-05: the bot kept opening the Y view for `A:bf8c3dee:M97e81e` - 588
records, always attempt 1 - long after the plan had replaced that step. An
abandoned step that is neither in the plan nor in the launched-order records
and never got a status keeps the map loop busy while nothing can start it.
"""
from docich.hanjuku.flows import map as M


def memory(active, plan_steps=('A:x:M1',), launched=None, status=None):
    return {'chapter': 6, 'tick': 100, '_records': [], 'active': active, 'picked': [],
            'chart_plan': {'orders': [{'step': s} for s in plan_steps]},
            'world': {'launched_orders': dict(launched or {})},
            'orders': dict(status or {})}


def records(mem):
    return [r['decision'] for r in mem['_records']]


def test_an_abandoned_active_order_is_released():
    mem = memory('A:x:OLD')
    assert M._release_stale_active(mem) is True
    assert mem['active'] is None
    assert records(mem) == ['stale_active_released']


def test_an_order_in_the_plan_is_kept():
    mem = memory('A:x:M1')
    assert M._release_stale_active(mem) is False
    assert mem['active'] == 'A:x:M1'


def test_a_launched_retry_is_kept():
    mem = memory('A:x:OLD', launched={'A:x:OLD': {'step': 'A:x:OLD'}})
    assert M._release_stale_active(mem) is False


def test_an_order_with_a_status_is_kept():
    mem = memory('A:x:OLD', status={'A:x:OLD': 'launched'})
    assert M._release_stale_active(mem) is False


def test_no_plan_never_releases_anything():
    mem = memory('A:x:OLD', plan_steps=())
    assert M._release_stale_active(mem) is False
