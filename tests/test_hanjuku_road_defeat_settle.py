"""A sortie whose general falls or retreats on the road settles at once.

g1163 2026-11-10 chapter 8: キール met an enemy in the field on the way to
おかざき and lost at 23:24 (ally HP 0). The order stayed 'launched' until the
march deadline (sortie_march_overdue at 23:47, 1268 ticks), so nobody was sent
to おかざき for 23 minutes. A 0-HP defeat removes the general from the roster
(g1097 2026-11-02), so the march is over the moment the battle ends.
"""
from docich.hanjuku.flows import battle
from docich.hanjuku.state.regions import world as _world
from docich.hanjuku.strategy import orders

STEP = 'A:x:M1'
TICK = 5000


def battle_memory(ally_hp, enemy_hp, *, ally='キール', castle=None, target='おかざき',
                  retreat=False, status='en_route', side=None):
    # Production road battles are bound to the step with side None and
    # castle None (g1163 23:21:55 battle_step_resolved → 23:24:06 result).
    cur = {'ally': ally, 'enemy': 'ヘーゼル', 'castle': castle, 'side': side,
           'step': STEP, 'ally_hp': ally_hp, 'enemy_hp': enemy_hp, 'away': 1,
           'card_consumption_complete': True, 'cards_used': [], 'cards_selected': []}
    if retreat:
        cur['hero_retreat'] = {'selected': True}
    return {'chapter': 8, 'tick': TICK, '_records': [], 'orders': {STEP: 'launched'},
            'world': {'sorties': {STEP: {'general': 'キール', 'source': 'あるまむーん',
                                         'target': target, 'status': status, 'tick': TICK - 100}},
                      'launched_orders': {STEP: {'step': STEP, 'source': 'あるまむーん',
                                                 'target': target}},
                      'garrison': {'あるまむーん': ['どうし', 'バーミセリ']}},
            'battle': cur}


def decisions(mem, name):
    return [r for r in mem['_records'] if r['decision'] == name]


def test_a_zero_hp_road_loss_marks_the_march_defeated():
    mem = battle_memory(0, 8)
    battle.battle_end(mem, 'field')
    sortie = _world(mem)['sorties'][STEP]
    assert sortie['road_defeat'] == TICK
    record = decisions(mem, 'sortie_road_defeat')[-1]
    assert record['general'] == 'キール'
    assert record['observed_metric']['target'] == 'おかざき'


def test_a_road_retreat_marks_the_march_defeated():
    mem = battle_memory(20, 40, retreat=True)
    battle.battle_end(mem, 'field')
    assert _world(mem)['sorties'][STEP]['road_defeat'] == TICK


def test_a_road_win_keeps_marching():
    mem = battle_memory(30, 0)
    battle.battle_end(mem, 'field')
    assert 'road_defeat' not in _world(mem)['sorties'][STEP]
    assert not decisions(mem, 'sortie_road_defeat')


def test_a_loss_with_hp_left_is_not_read_as_a_defeat():
    mem = battle_memory(12, 30)
    battle.battle_end(mem, 'field')
    assert 'road_defeat' not in _world(mem)['sorties'][STEP]


def test_another_generals_loss_does_not_settle_this_march():
    mem = battle_memory(0, 30, ally='ヴィーナス')
    battle.battle_end(mem, 'field')
    assert 'road_defeat' not in _world(mem)['sorties'][STEP]


def test_a_loss_at_the_target_still_closes_through_sortie_outcome():
    mem = battle_memory(0, 30, castle='おかざき', side='attack')
    battle.battle_end(mem, 'field')
    assert STEP not in (_world(mem).get('sorties') or {})
    assert not decisions(mem, 'sortie_road_defeat')


def test_road_defeat_settles_before_the_deadline_and_reopens_the_order():
    mem = battle_memory(0, 8)
    battle.battle_end(mem, 'field')
    mem['tick'] = TICK + 1
    orders._relaunch_overdue_march(mem)
    assert mem['orders'][STEP] == 'pending'
    assert _world(mem)['sorties'][STEP]['status'] == 'overdue'
    overdue = decisions(mem, 'sortie_march_overdue')[-1]
    assert overdue['observed_metric']['road_defeat'] is True
    assert '敗北・退却' in overdue['reason']
    # The general is not assumed home: his location is rechecked.
    assert 'キール' in _world(mem)['general_location_unknown']
    assert 'キール' not in _world(mem)['garrison']['あるまむーん']


def test_without_a_defeat_the_deadline_still_applies():
    mem = battle_memory(30, 0)
    battle.battle_end(mem, 'field')
    mem['tick'] = TICK + 1
    orders._relaunch_overdue_march(mem)
    assert mem['orders'][STEP] == 'launched'
    assert _world(mem)['sorties'][STEP]['status'] == 'en_route'
    assert not decisions(mem, 'sortie_march_overdue')


def test_a_defense_battle_never_settles_a_march():
    mem = battle_memory(0, 30, side='defense', castle='あるまむーん')
    battle.battle_end(mem, 'field')
    assert 'road_defeat' not in _world(mem)['sorties'][STEP]


def test_an_attack_side_road_loss_is_marked_and_closed_without_relaunch():
    # The attack-side retry already reopened the order; the settlement then
    # only closes the march record.
    mem = battle_memory(0, 30, side='attack', castle='ながはま')
    battle.battle_end(mem, 'field')
    assert _world(mem)['sorties'][STEP]['road_defeat'] == TICK
    mem['tick'] = TICK + 1
    orders._relaunch_overdue_march(mem)
    assert _world(mem)['sorties'][STEP]['status'] == 'overdue'
    assert mem['orders'][STEP] == 'pending'


def test_road_defeat_is_marked_once():
    mem = battle_memory(0, 8)
    battle.battle_end(mem, 'field')
    mem['battle'] = battle_memory(0, 8)['battle']
    mem['tick'] = TICK + 3
    battle.battle_end(mem, 'field')
    assert _world(mem)['sorties'][STEP]['road_defeat'] == TICK
    assert len(decisions(mem, 'sortie_road_defeat')) == 1
