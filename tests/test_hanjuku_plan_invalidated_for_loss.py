"""A lost castle outranks a pending mechanical plan (retake first).

g1163 chapter 7 2026-11-09 17:52: マンシー fell while the mechanical plan held
one staffing move, so the retake waited for that order to finish.
"""
from docich.hanjuku.strategy import replan


def memory(orders=None, in_flight=None, lost=('マンシー',), with_retake=False,
           source='mechanical'):
    orders = orders or [{'step': 'A:x:M1', 'purpose': 'move', 'target': 'リデンブロック'}]
    if with_retake:
        orders = orders + [{'step': 'A:x:M2', 'purpose': 'retake', 'target': 'マンシー'}]
    statuses = {o['step']: 'pending' for o in orders}
    if in_flight:
        statuses[in_flight] = 'launched'
    return {'chapter': 7, 'tick': 1000, '_records': [], 'lost': list(lost),
            'orders': statuses,
            'chart_plan': {'source': source, 'orders': orders}}


def test_a_pending_plan_without_a_retake_is_discarded():
    mem = memory()
    assert replan.invalidate_plan_for_loss(mem) is True
    assert mem.get('chart_plan') is None
    assert any(r['decision'] == 'plan_invalidated_for_loss' for r in mem['_records'])


def test_a_plan_with_a_retake_is_kept():
    mem = memory(with_retake=True)
    assert replan.invalidate_plan_for_loss(mem) is False
    assert mem.get('chart_plan')


def test_an_in_flight_order_keeps_the_plan():
    mem = memory(in_flight='A:x:M1')
    assert replan.invalidate_plan_for_loss(mem) is False
    assert mem.get('chart_plan')


def test_without_a_loss_nothing_changes():
    mem = memory(lost=())
    assert replan.invalidate_plan_for_loss(mem) is False
    assert mem.get('chart_plan')
