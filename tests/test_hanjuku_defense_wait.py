"""Sole-general starvation (#2846): wait for a second general, then a bounded break.

Chapter 3 head as observed: one owned castle with one general, no marching
generals. No model request while waiting; one monthly fallback sortie allowed.
Synthetic memory only.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from docich import hanjuku_chart as chart
from docich.hanjuku.strategy import replan
from docich.hanjuku.strategy import orders

HOME = chart.home_castle(3)


def starved_memory(month='8-1', garrison=None):
    return {'chapter': 3, 'month': month, 'tick': 1000, 'captured': [], 'home_lost': False,
            'garrison': garrison if garrison is not None else {HOME: ['アルテミス']},
            'sorties': {}, 'lost': [], 'general_location_unknown': [], 'house_eggs': {},
            'orders': {}, '_records': []}


def kinds(mem):
    return [r['decision'] for r in mem['_records']]


def test_first_starved_tick_holds_without_model_request():
    mem = starved_memory()
    replan._off_chart(mem)
    assert kinds(mem) == ['chart_defense_wait']
    assert 'chart_adjust' not in mem


def test_same_month_adds_no_records():
    mem = starved_memory()
    for _ in range(5):
        replan._off_chart(mem)
    assert kinds(mem) == ['chart_defense_wait']


def test_month_change_without_prospects_breaks_sole_general():
    mem = starved_memory()
    mem['orders'] = {o['step']: 'launched' for o in chart.orders(3)}
    replan._off_chart(mem)
    mem['month'] = '8-2'
    replan._off_chart(mem)
    assert 'chart_defense_wait_eval' in kinds(mem)
    assert 'sortie_defense_break' in kinds(mem)
    brk = [r for r in mem['_records'] if r['decision'] == 'sortie_defense_break'][0]
    assert brk['source'] == HOME and brk['general'] == 'アルテミス'
    order = mem['chart_adjust']['interim_order']
    assert orders.next_order(mem) == order
    assert orders._source_spare_missing(order, mem, allow_probe=True) is False


def test_defense_break_does_not_authorize_foreign_or_expired_order():
    import copy
    mem = starved_memory()
    replan._off_chart(mem)
    mem['month'] = '8-2'
    replan._off_chart(mem)
    order = mem['chart_adjust']['interim_order']
    for field, value in [('step', 'I:foreign:1'), ('general', 'ゼウス'),
                         ('target', 'foreign'), ('source', 'foreign')]:
        assert orders._reserve_source_guard({**order, field: value}, mem)
    for change in [{'month': '8-3'}, {'lost': [HOME]},
                   {'defense_break_blocked': [HOME]}, {'defense_break_sources': {}},
                   {'general_location_unknown': [order['general']]}, {'garrison': {HOME: []}},
                   {'sorties': {'other': {'general': order['general'], 'status': 'en_route',
                                         'target': order['target'], 'tick': mem['tick']}}},
                   {'general_override': {order['step']: 'ゼウス'}}]:
        altered = copy.deepcopy(mem)
        altered.update(change)
        assert orders._reserve_source_guard(order, altered)


def test_prospects_extend_once_then_cap_forces_break():
    mem = starved_memory()
    original = replan._joiner_prospects
    replan._joiner_prospects = lambda m: True
    try:
        replan._off_chart(mem)
        mem['month'] = '8-2'
        replan._off_chart(mem)
        assert 'sortie_defense_break' not in kinds(mem)
        mem['month'] = '8-3'
        replan._off_chart(mem)
        assert 'sortie_defense_break' in kinds(mem)
    finally:
        replan._joiner_prospects = original


def test_two_generals_keep_normal_path():
    mem = starved_memory(garrison={HOME: ['アルテミス', 'ゼウス']})
    assert replan._defense_gate(mem) is None
    assert 'chart_defense_wait' not in kinds(mem)


def test_lost_break_source_is_never_used_again():
    mem = starved_memory()
    replan._off_chart(mem)
    mem['month'] = '8-2'
    replan._off_chart(mem)
    mem['lost'] = [HOME]
    replan._defense_gate(mem)
    assert 'defense_break_lost' in kinds(mem)
    assert HOME in mem['defense_break_blocked']
    assert replan.interim_candidates(mem, allow_sole=True) == {}
