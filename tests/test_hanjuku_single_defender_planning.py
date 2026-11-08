"""A measured one-general castle cannot produce an executable sortie plan.

2026-11-06: ボッサール holds only ポセイドン. The boss-wave planner's
post-conquest exception still offered a sortie, which the game canceled.
Check candidate creation, plan validation and pending-plan execution gates.
"""
from docich import hanjuku_chart as chart, hanjuku_chart_adjust as adjust
from docich.hanjuku.strategy import orders, replan


def memory(*, generals=('ポセイドン',), chapter=5):
    normal = [c for c in chart.castles(chapter)
              if not chart.is_boss_castle(c)]
    garrison = {c: [] for c in normal}
    garrison['ボッサール'] = list(generals)
    return {'chapter': chapter, 'tick': 1000, 'month': '50-5',
            'captured': normal, 'garrison': garrison, 'lost': [],
            'world': {'sorties': {}}, 'orders': {}, '_records': []}


def bossar_orders(mem):
    return [o for o in replan.interim_candidates(mem).values()
            if o['source'] == 'ボッサール']


def test_bossar_with_only_poseidon_is_not_planned_for_boss():
    assert bossar_orders(memory()) == []


def test_bossar_can_plan_a_boss_sortie_when_second_general_is_observed():
    planned = bossar_orders(memory(generals=('ポセイドン', 'ゼウス')))
    assert planned
    assert planned[0]['target'] == chart.boss_castle(5)
    assert planned[0]['general'] in ('ポセイドン', 'ゼウス')


def test_adjusted_plan_rejects_singleton_even_for_boss_and_move():
    request = {'garrison': {'ボッサール': ['ポセイドン']},
               'en_route': [], 'lost': []}
    order = {'source': 'ボッサール', 'general': 'ポセイドン',
             'target': chart.boss_castle(5)}
    assert adjust.startable(order, request) == 'source_last_defender'
    order['target'] = 'アイアイエー'
    order['purpose'] = 'move'
    assert adjust.startable(order, request) == 'source_last_defender'
    request['garrison']['ボッサール'].append('ゼウス')
    assert adjust.startable(order, request) is None


def test_existing_plan_and_source_probe_cannot_dispatch_last_defender():
    mem = memory()
    for purpose in ('attack', 'move'):
        order = {'step': 'A:manual:M1', 'source': 'ボッサール',
                 'general': 'ポセイドン', 'target': chart.boss_castle(5),
                 'cards': [], 'after': None, 'purpose': purpose,
                 'source_probe': True}
        assert orders._source_spare_missing(order, mem, allow_probe=True)


def test_garrison_rotation_does_not_empty_rear_castle_with_floor_zero(monkeypatch):
    mem = memory()
    mem['garrison']['アルマムーン'] = ['どうし']
    # The rear castle has a floor of zero; that is a preferred *minimum*,
    # not permission to sortie its only general.
    monkeypatch.setattr(replan, '_garrison_plan', lambda _mem: {
        'ボッサール': (100, 0, 0), 'アルマムーン': (0, 3, 1)})
    assert replan._garrison_rotation(
        mem, {'ボッサール', 'アルマムーン'}, set(), set()) == []
