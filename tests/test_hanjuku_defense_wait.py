"""Sole-general starvation (#2846): wait for a second general, never empty it.

Chapter 3 head as observed: one owned castle with one general, no marching
generals. No model request while waiting. Owner 2026-11-06 abolished the
monthly fallback sortie: an emptied castle falls to the next enemy.
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
            'world': {'sorties': {}},
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


def test_month_change_without_prospects_never_empties_the_sole_castle():
    # Owner 2026-11-06: an emptied castle falls to the next enemy, so the
    # starvation wait no longer sends the last general out.
    mem = starved_memory()
    _plan_region(mem)['orders'] = {o['step']: 'launched' for o in chart.orders(3)}
    replan._off_chart(mem)
    mem['month'] = '8-2'
    replan._off_chart(mem)
    assert 'chart_defense_wait_eval' in kinds(mem)
    assert 'sortie_defense_break' not in kinds(mem)
    assert (mem.get('chart_adjust') or {}).get('interim_order') is None


def test_a_starved_sole_castle_gets_no_interim_order():
    mem = starved_memory()
    replan._off_chart(mem)
    mem['month'] = '8-2'
    replan._off_chart(mem)
    mem['month'] = '8-3'
    replan._off_chart(mem)
    assert (mem.get('chart_adjust') or {}).get('interim_order') is None
    assert 'sortie_defense_break' not in kinds(mem)


def test_joiner_prospects_never_authorize_an_emptied_castle():
    mem = starved_memory()
    original = replan._joiner_prospects
    replan._joiner_prospects = lambda m: True
    try:
        replan._off_chart(mem)
        mem['month'] = '8-2'
        replan._off_chart(mem)
        mem['month'] = '8-3'
        replan._off_chart(mem)
        assert 'sortie_defense_break' not in kinds(mem)
        assert (mem.get('chart_adjust') or {}).get('interim_order') is None
    finally:
        replan._joiner_prospects = original


def test_two_generals_keep_normal_path():
    mem = starved_memory(garrison={HOME: ['アルテミス', 'ゼウス']})
    assert replan._defense_gate(mem) is None
    assert 'chart_defense_wait' not in kinds(mem)


def test_a_lost_sole_castle_never_sends_its_last_general():
    # Owner 2026-11-06: an emptied castle falls; the starvation wait no longer
    # sends the last general, lost source or not.
    mem = starved_memory()
    replan._off_chart(mem)
    mem['month'] = '8-2'
    replan._off_chart(mem)
    mem['lost'] = [HOME]
    replan._defense_gate(mem)
    assert 'sortie_defense_break' not in kinds(mem)
    assert replan.interim_candidates(mem, allow_sole=True) == {}
