"""A retake/attack whose castle became ours is retired, not re-sent.

g1163 2026-11-13 14:24 still settled an "enemy castle" sortie into オオキク,
which had been ours since 14:11. The stale order only spent a launch and a
destination read; the corridor target had already moved to ナッテ.
"""
from docich.hanjuku.state.regions import plan as _plan_region, world as _world
from docich.hanjuku.strategy import orders

STEP = 'I:c9:ドンドン>オオキク:73'
OWNED = ['ハヤク', 'タオ', 'サヌト', 'オシロガ', 'ドンドン', 'オオキク']


def memory(target='オオキク', purpose='retake', status='en_route', tick=100):
    return {'chapter': 9, 'tick': 200, '_records': [], 'captured': list(OWNED),
            'orders': {STEP: 'launched'},
            'sorties': {STEP: {'general': 'ギー', 'source': 'ドンドン', 'target': target,
                               'purpose': purpose, 'status': status, 'tick': tick}},
            'world': {'launched_orders': {STEP: {'source': 'ドンドン', 'target': target,
                                                 'purpose': purpose}}}}


def settled(mem, name):
    return [r for r in mem['_records'] if r.get('decision') == name]


def test_a_retake_into_an_owned_castle_is_retired():
    mem = memory()
    orders._relaunch_overdue_march(mem)
    assert _plan_region(mem)['orders'][STEP] == 'failed'
    assert settled(mem, 'sortie_target_owned_settled')
    assert not settled(mem, 'sortie_march_overdue')


def test_an_attack_into_an_owned_castle_is_retired():
    mem = memory(purpose='attack')
    orders._relaunch_overdue_march(mem)
    assert _plan_region(mem)['orders'][STEP] == 'failed'
    assert settled(mem, 'sortie_target_owned_settled')


def test_a_move_into_an_owned_castle_keeps_its_move_rule():
    mem = memory(purpose='move')
    orders._relaunch_overdue_march(mem)
    assert not settled(mem, 'sortie_target_owned_settled')
    assert (_world(mem).get('sorties') or {})[STEP]['status'] == 'en_route'


def test_an_enemy_target_is_not_retired():
    mem = memory(target='ナッテ')
    orders._relaunch_overdue_march(mem)
    assert not settled(mem, 'sortie_target_owned_settled')
    assert _plan_region(mem)['orders'][STEP] == 'launched'
