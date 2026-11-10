"""A replaced plan drops its unstarted steps (v594).

g1163 chapter 8 2026-11-09: `A:046e819d:M5f4f03` stayed pending while
`A:8e54a3af:M5f4f03` (the same general, new plan) was already launched, so the
broadcast showed a sortie plan for a general who had already left.
"""
from docich.hanjuku.strategy import replan

NEW_ORDERS = [{'step': 'A:new:M1', 'source': 'あるまむーん', 'general': '乙',
               'target': 'おかざき', 'cards': [], 'after': None}]
OLD_ORDERS = [{'step': 'A:old:M1', 'source': 'あるまむーん', 'general': '甲',
               'target': 'しらかわ', 'cards': [], 'after': None},
              {'step': 'A:old:M2', 'source': 'あるまむーん', 'general': '甲',
               'target': 'えど', 'cards': [], 'after': None}]


def memory(statuses, sorties):
    return {'chapter': 8, 'tick': 5000, '_records': [], 'orders': dict(statuses),
            'sorties': dict(sorties), 'world': {'sorties': dict(sorties)},
            'chart_plan': {'source': 'mechanical', 'orders': list(OLD_ORDERS)}}


def test_an_unstarted_old_step_is_superseded():
    mem = memory({'A:old:M1': 'pending', 'A:old:M2': 'launched'}, {})
    replan._supersede_unstarted_steps(mem, NEW_ORDERS)
    assert _plan_region(mem)['orders']['A:old:M1'] == 'superseded'
    assert _plan_region(mem)['orders']['A:old:M2'] == 'launched'
    assert any(r['decision'] == 'plan_steps_superseded' for r in mem['_records'])


def test_a_step_with_its_own_sortie_is_left_alone():
    mem = memory({'A:old:M1': 'pending'},
                 {'A:old:M1': {'general': '甲', 'status': 'en_route', 'tick': 4000}})
    replan._supersede_unstarted_steps(mem, NEW_ORDERS)
    assert _plan_region(mem)['orders']['A:old:M1'] == 'pending'


def test_a_step_carried_into_the_new_plan_is_kept():
    mem = memory({'A:old:M1': 'pending'}, {})
    replan._supersede_unstarted_steps(mem, [{'step': 'A:old:M1'}])
    assert _plan_region(mem)['orders']['A:old:M1'] == 'pending'
