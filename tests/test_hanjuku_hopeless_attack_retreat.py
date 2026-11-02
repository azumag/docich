"""#3042: an attack with spent resources and a 4x melee gap retreats at once.

g1075-1d5f65e9 2026-10-30 00:57 JST (v321): ビシソワーズ (27, combat 3)
attacked フヌイヌム's ゼウス (85, combat 13) after using her egg and cards.
The rescue menu (g1075-retreat-menu-egg-spent.png: gray たまごをつかう,
きりふだ and たいきゃく) closed at HP 27 vs 73 because the HP threshold
(12) had not been reached. The next frames read 27, 18 (g1075-retreat-
panel-zeus-18-53.png), 8 and 0 within about two seconds; the retreat menu
was requested at 8 and never seen. Owner 2026-10-30: retreat even weak
generals when the attack is this hopeless.
"""
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'


def native(name):
    frame = read_png(FIXTURES / name)
    return parse(frame, phase=classify(frame))


def max_hp(name):
    return p.reference.general_strength('しゅじんこう' if name == p.NAME else name)[0]


def attack(ally='ビシソワーズ', enemy='ゼウス', hp=27, enemy_hp=73, **changes):
    battle = {'ally': ally, 'enemy': enemy, 'side': 'attack', 'castle': 'フヌイヌム',
              'ally_hp': hp, 'enemy_hp': enemy_hp,
              'start_ally_hp': max_hp(ally), 'start_enemy_hp': max_hp(enemy),
              'step': None, 'planned_cards': [], 'cards_used': [], 'cards_selected': [],
              'cards_unclassified': [], 'card_evidence_version': 1,
              'survival': {'opens': 1, 'menu_ticks': 0, 'cards_checked': 3,
                           'cards_attempted': ['ミックミー'], 'cards_exhausted': True,
                           'egg_attempted': True, 'exhausted': True, 'cards_uncertain': False}}
    battle.update(changes)
    return battle


def decisions(mem, name):
    return [r for r in mem.get('_records', []) if r['decision'] == name]


def test_native_spent_attack_retreats_before_the_hp_threshold():
    cur = attack()
    cur['survival']['exhausted'] = False
    mem = {'chapter': 4, 'battle': cur}
    menu = native('g1075-retreat-menu-egg-spent.png')
    assert menu.kind == 'battle_menu' and menu.menu_cursor == 176

    # The first menu still records that nothing untried remains.
    assert p.battle_menu_step(menu, mem) == [p.pad('b')]
    assert decisions(mem, 'battle_survival_unavailable')

    panel = native('g1075-retreat-panel-zeus-18-53.png')
    assert (panel.battle.ally, panel.battle.ally_hp) == ('ビシソワーズ', 18)
    assert (panel.battle.enemy, panel.battle.enemy_hp) == ('ゼウス', 53)
    assert 18 > p.GENERAL_CRITICAL_RETREAT_HP  # v321 kept pressing A here
    assert p.battle_step(panel, mem) == [p.pad('b')]
    record, = decisions(mem, 'battle_hero_retreat_open')
    assert record['observed_metric']['retreat_assessment']['reason'] == 'hopeless_attack_melee'
    assert not decisions(mem, 'battle_melee')

    # The reopened menu moves to たいきゃく (two rows below the cursor).
    assert p.battle_menu_step(menu, mem) == [p.pad('down')]


@pytest.mark.parametrize('hp,enemy_hp,expected', [
    (27, 73, True),    # 81 vs 949
    (27, 25, True),    # 81 vs 325
    (27, 24, False),   # 81 vs 312: under 4x
    (27, 12, False),   # 15:27 / 16:30 wins were about 2x behind
])
def test_only_a_four_times_measured_gap_is_hopeless(hp, enemy_hp, expected):
    cur = attack(hp=hp, enemy_hp=enemy_hp)
    needed = p._hero_retreat_needed(cur)
    assert (cur['retreat_assessment']['reason'] == 'hopeless_attack_melee') is expected
    assert needed or not expected


# Negative cases: the existing HP / value rules keep these.

@pytest.mark.parametrize('changes', [
    {'side': 'defense'},
    {'ally': 'どうし'}, {'ally': 'しゅじんこう'},
    {'enemy': 'クイーン', 'start_enemy_hp': 70, 'enemy_hp': 70},
    {'egg_battle': True}, {'card_summon_observed': True}, {'event_duel': True},
    {'card_flow': {'stage': 'list', 'card': 'ミックミー'}},
    {'planned_cards': ['ミックミー']},
    {'hero_retreat': {'selected': 1}},
])
def test_other_contexts_do_not_use_the_hopeless_rule(changes):
    cur = attack(**changes)
    p._hero_retreat_needed(cur)
    assert cur['retreat_assessment']['reason'] != 'hopeless_attack_melee'


@pytest.mark.parametrize('survival', [
    {'exhausted': False},
    {'exhausted': True, 'cards_uncertain': True},
])
def test_unspent_or_uncertain_resources_are_tried_first(survival):
    cur = attack()
    cur['survival'].update(survival)
    p._hero_retreat_needed(cur)
    assert cur['retreat_assessment']['reason'] != 'hopeless_attack_melee'


def test_an_untried_positive_egg_is_not_thrown_away():
    cur = attack(own_egg_positive_observed=True)
    cur['survival']['egg_attempted'] = False
    p._hero_retreat_needed(cur)
    assert cur['retreat_assessment']['reason'] != 'hopeless_attack_melee'
