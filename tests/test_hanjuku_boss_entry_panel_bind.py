"""A readable boss battle panel is entry evidence when the text was missed.

g1163 chapter 6 2026-11-09 13:36: ヘーゼル fought ハードロボ, boss_hp_seen was
recorded, but no attack_observed ever fired: the sortie stayed en route and the
wave was re-sent as overdue at 14:00 while the boss HP never moved.
"""
from types import SimpleNamespace

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import battle_menus as B
from docich.hanjuku.state.regions import world as _world

BOSS = chart.boss_castle(6)


def memory(step='A:x:M1', general='ヘーゼル', attack_step=None):
    return {'chapter': 6, 'tick': 5000, '_records': [],
            'sorties': {step: {'general': general, 'source': 'オケアナ',
                               'target': BOSS, 'status': 'en_route', 'tick': 4000}},
            'attack': {'general': general, 'castle': BOSS, 'side': 'attack',
                       'step': attack_step}}


def test_the_panel_binds_the_in_flight_boss_sortie():
    mem = memory()
    rows = [SimpleNamespace(name='ハードロボ', hp=152)]
    B.note_boss_monster_hp(mem, {'ally': 'ヘーゼル'}, rows)
    # The world region owns sorties after the first access (the top-level key
    # is migrated away), so read them through the same accessor.
    assert (_world(mem).get('sorties') or {})['A:x:M1']['status'] == 'arrived'
    assert mem['attack']['step'] == 'A:x:M1'
    assert any(r['decision'] == 'boss_entry_bound_by_panel' for r in mem['_records'])
    assert mem['boss_hp_seen']['hp'] == 152


def test_an_already_bound_attack_is_untouched():
    mem = memory(attack_step='A:x:M1')
    B.note_boss_monster_hp(mem, {'ally': 'ヘーゼル'},
                           [SimpleNamespace(name='ハードロボ', hp=152)])
    assert not any(r['decision'] == 'boss_entry_bound_by_panel' for r in mem['_records'])


def test_a_non_boss_panel_never_binds():
    mem = memory()
    mem['attack'] = {'general': 'ヘイゼル', 'castle': 'セレーネ', 'side': 'attack', 'step': None}
    B.note_boss_monster_hp(mem, {'ally': 'ヘイゼル'},
                           [SimpleNamespace(name='ハードロボ', hp=152)])
    assert mem['sorties']['A:x:M1']['status'] == 'en_route'
    assert 'boss_hp_seen' not in mem
