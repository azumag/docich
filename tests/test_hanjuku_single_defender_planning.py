"""A measured one-general castle cannot produce an executable sortie plan.

2026-11-06: ボッサール holds only ポセイドン. The boss-wave planner's
post-conquest exception still offered a sortie, which the game canceled.
Check candidate creation, plan validation and pending-plan execution gates.

2026-11-08 (g1153 13:30): a staging move was planned out of メガパタ while it
held only チコリ and ヴィーナス was still marching in. The game refuses to
dispatch the last defender, so that plan contradicted the leave-one rule.
An arrival check may still open the list, but only as a read-only probe.
"""
from docich import hanjuku_chart as chart, hanjuku_chart_adjust as adjust
from docich.hanjuku.state.regions import world as _world, plan as _plan_region
from docich.hanjuku.strategy import orders, replan, routes


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


CHAPTER3 = ('アルマムーン', 'グリン', 'コラダイン', 'イーピヨル', 'メガパタ',
            'パタゴーヌ', 'アバロン', 'リグア', 'ドンダム')


def chapter3_memory(*, mekapata=('チコリ',), transfer=None):
    """Chapter 3 with メガパタ holding one measured general."""
    garrison = {'アルマムーン': ['どうし', 'ヘラ', 'ゼウス'],
                'グリン': ['アンディーブ'],
                'コラダイン': ['エシャロット'],
                'イーピヨル': ['ピオーネ', 'トレビス'],
                'メガパタ': list(mekapata),
                'パタゴーヌ': ['ガスパチョ'],
                'アバロン': ['アテナ'],
                'リグア': ['ユイートル']}
    mem = {'chapter': 3, 'tick': 5000, 'month': '8-4',
           'captured': [c for c in CHAPTER3 if c != 'ドンダム'],
           'lost': ['ドンダム'], 'garrison': garrison,
           'world': {'sorties': {}}, 'launched_orders': {}, 'orders': {},
           '_records': [], 'house_eggs': {}, 'general_location_unknown': []}
    if transfer:
        general, status, age = transfer
        step = 'A:test:M1'
        _world(mem)['sorties'][step] = {'source': 'アルマムーン', 'target': 'メガパタ',
                                'general': general, 'status': status,
                                'tick': mem['tick'] - age, 'purpose': 'move'}
        _world(mem)['launched_orders'][step] = {
            'purpose': 'move', 'source': 'アルマムーン', 'target': 'メガパタ',
            'general': general}
    return mem


def mekapata_orders(mem):
    return [o for o in replan.interim_candidates(mem).values()
            if o['source'] == 'メガパタ']


def test_a_still_marching_transfer_does_not_make_a_singleton_a_source():
    # While ヴィーナス is still walking the list cannot show a second general;
    # planning a move from メガパタ would contradict the leave-one rule.
    mem = chapter3_memory(transfer=('ヴィーナス', 'en_route', 10))
    assert not routes.arrival_probe_due(mem, 'メガパタ', cooldown=True)
    assert mekapata_orders(mem) == []


def test_an_arrival_due_at_a_singleton_is_planned_read_only():
    mem = chapter3_memory(transfer=('ヴィーナス', 'overdue', 900))
    assert routes.arrival_probe_due(mem, 'メガパタ', cooldown=True)
    probes = mekapata_orders(mem)
    assert probes
    assert all(o.get('roster_probe') is True for o in probes)
    assert all(o.get('source_probe') is True and o['general'] == replan.MOVE_ANY_GENERAL
               for o in probes)


def test_the_mechanical_plan_marks_the_singleton_arrival_probe_read_only():
    mem = chapter3_memory(transfer=('ヴィーナス', 'overdue', 900))
    replan._mechanical_chart(mem, mem.setdefault('chart_adjust', {}),
                             '0123456789abcdef', 'digest')
    plan = (_plan_region(mem).get('chart_plan') or {}).get('orders') or []
    probes = [o for o in plan if o['source'] == 'メガパタ']
    assert probes
    assert all(o.get('roster_probe') is True and o.get('source_probe') is True
               for o in probes)


def test_the_verified_sortie_does_not_inherit_the_probe_step():
    mem = chapter3_memory(transfer=('ヴィーナス', 'overdue', 900))
    probe = {'source': 'メガパタ', 'target': 'アルマムーン', 'purpose': 'move',
             'general': replan.MOVE_ANY_GENERAL, 'roster_probe': True}
    sortie = {'source': 'メガパタ', 'target': 'アルマムーン', 'purpose': 'move',
              'general': 'チコリ'}
    assert (replan._mechanical_step(probe, set(), mem)
            != replan._mechanical_step(sortie, set(), mem))
    # A later transfer into the same castle needs its own probe step.
    earlier = replan._mechanical_step(probe, set(), mem)
    _world(mem)['sorties']['A:test:M2'] = {'source': 'アルマムーン', 'target': 'メガパタ',
                                   'general': 'ヘラ', 'status': 'overdue',
                                   'tick': mem['tick'], 'purpose': 'move'}
    assert replan._mechanical_step(probe, set(), mem) != earlier


def test_a_read_only_probe_is_not_cancelled_before_reading_the_list():
    mem = chapter3_memory()
    probe = {'step': 'A:test:M1', 'source': 'メガパタ', 'general': 'しょうぐん',
             'target': 'アルマムーン', 'cards': [], 'after': None,
             'purpose': 'move', 'source_probe': True, 'roster_probe': True}
    assert orders._source_spare_missing(probe, mem, allow_probe=True) is False
    named = {**probe, 'general': 'チコリ'}
    named.pop('roster_probe')
    named.pop('source_probe')
    assert orders._source_spare_missing(named, mem, allow_probe=True) is True
