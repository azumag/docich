"""#2989: an open rescue menu with only おくのて is not closed to wait.

g1075-1d5f65e9 2026-10-29 20:48 JST (v317): ゼウス (85) defended a castle
against ココット (24, egg). The rescue menu opened by battle_survival showed
gray たまごをつかう / きりふだ / たいきゃく with おくのて below
(g1075-okunote-only-rescue-menu.png: the ring frame between battle_start
and the enemy's たまごをつかう caption; the next ring frame is the same
85/24 panel). v317 recorded battle_okunote_egg_wait (open_at_hp 14) and
closed it, opened again by the HP window at enemy HP 14, and was a second
behind the enemy summon; ゼウス lost and the castle fell. The castle name
was not in the decisions read, so the test uses the chapter's home castle.
The later parent frames are the 14:35 リグア selection that won
(g1075-okunote-parent-2..4.png).
"""
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku.os_layer.phase import classify
from docich.hanjuku.os_layer.pixels import read_png
from docich.hanjuku.os_layer.screen import parse
from docich.hanjuku_egg_reference import general_max_hp
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen

FIXTURES = Path(__file__).resolve().parents[1] / 'tests' / 'fixtures' / 'hanjuku'


def native(name):
    frame = read_png(FIXTURES / name)
    return parse(frame, phase=classify(frame))


def memory(*, ally='ゼウス', ally_hp=85, enemy='ココット', enemy_hp=24, **changes):
    battle = {'enemy': enemy, 'ally': ally, 'castle': p.chart.home_castle(4), 'side': 'defense',
              'start_enemy_hp': enemy_hp, 'enemy_hp': enemy_hp,
              'start_ally_hp': ally_hp, 'ally_hp': ally_hp, 'step': None,
              'cards_used': [], 'cards_selected': [], 'cards_unclassified': [],
              'card_evidence_version': 1, 'planned_cards': [],
              'survival': {'opens': 1, 'menu_ticks': 0, 'cards_checked': 0,
                           'cards_attempted': [], 'egg_attempted': False, 'exhausted': False}}
    battle.update(changes)
    return {'chapter': 4, 'battle': battle}


def decisions(mem, name):
    return [r for r in mem.get('_records', []) if r['decision'] == name]


def test_native_okunote_only_menu_is_taken_instead_of_closed_to_wait():
    screen = native('g1075-okunote-only-rescue-menu.png')
    assert screen.kind == 'battle_menu' and screen.hidden_battle_commands
    assert screen.menu_cursor == 176
    mem = memory()
    cur = mem['battle']
    # The v317 deadline: enemy HP 24 is far above the HP window.
    waited = p._defender_egg_window(mem, {**cur, 'okunote_only_observed': True})
    assert not waited['due'] and waited['open_at_hp'] == 14

    assert p.battle_menu_step(screen, mem) == [p.pad('down')]
    assert not decisions(mem, 'battle_okunote_egg_wait')
    record, = decisions(mem, 'battle_okunote_egg_preempt')
    assert record['observed_metric']['reason'] == 'menu_open_contact_unmeasured'
    assert record['observed_metric']['max_self_damage'] == p.OKUNOTE_MAX_SELF_DAMAGE
    assert record['resulting_event'] == 'irreversible_risk_accepted'

    for n in (2, 3):
        assert p.battle_menu_step(native(f'g1075-okunote-parent-{n}.png'), mem) == [p.pad('down')]
    selected = native('g1075-okunote-parent-4.png')
    assert p.battle_menu_step(selected, mem) == [p.pad('a')]
    assert cur['okunote_egg_preempt']['selected']
    # A repeated parent frame is not another lottery.
    assert p.battle_menu_step(selected, mem) == [p.pad('b')]
    assert len(decisions(mem, 'battle_okunote_egg_preempt')) == 1


def test_the_chosen_route_survives_a_closed_menu_and_reopens_from_melee():
    mem = memory()
    p.battle_menu_step(native('g1075-okunote-only-rescue-menu.png'), mem)
    window = p._defender_egg_window(mem, mem['battle'])
    assert window['due'] and window['reason'] == 'menu_open_contact_unmeasured'


# Negative cases: the open menu alone does not take the lottery.

@pytest.mark.parametrize('ally', ['どうし', 'しゅじんこう'])
def test_hero_keeps_the_hp_and_position_deadlines(ally):
    mem = memory(ally=ally, ally_hp=general_max_hp('しゅじんこう'))
    assert p.battle_menu_step(native('g1075-okunote-only-rescue-menu.png'), mem) == [p.pad('b')]
    assert decisions(mem, 'battle_okunote_egg_wait')
    assert not decisions(mem, 'battle_okunote_egg_preempt')
    assert 'okunote_egg_preempt' not in mem['battle']


def test_enemy_without_egg_keeps_the_self_damage_decline():
    # 2026-10-30 00:47 アルマムーン: ミント has no egg; a 26 HP defender is
    # still not sent into the 88 HP self-damage lottery from the open menu.
    mem = memory(ally='ゼウス', ally_hp=26, enemy='ミント', enemy_hp=32)
    assert p.battle_menu_step(native('g1075-okunote-only-rescue-menu.png'), mem) == [p.pad('b')]
    assert decisions(mem, 'battle_okunote_risk_declined')
    assert not decisions(mem, 'battle_okunote_egg_preempt')


@pytest.mark.parametrize('changes', [
    {'side': 'attack'}, {'enemy': 'クイーン', 'enemy_hp': 70, 'start_enemy_hp': 70},
    {'ally': 'だいじん', 'ally_hp': 90, 'start_ally_hp': 90}, {'egg_battle': True},
    {'card_flow': {'stage': 'list', 'card': 'グリンボー'}},
])
def test_other_contexts_do_not_take_the_lottery_from_the_open_menu(changes):
    mem = memory(**changes)
    p.battle_menu_step(native('g1075-okunote-only-rescue-menu.png'), mem)
    assert not decisions(mem, 'battle_okunote_egg_preempt')
    assert not (mem['battle'].get('okunote_egg_preempt') or {}).get('selected')


@pytest.mark.parametrize('label', ['たまごをつかう', 'きりふだ', 'たいきゃく'])
def test_a_usable_resource_in_the_menu_is_not_replaced_by_the_lottery(label):
    lines = [TextLine(192, tuple((176 + 8*j, ch) for j, ch in enumerate(label))),
             TextLine(208, tuple((176 + 8*j, ch) for j, ch in enumerate('おくのて')))]
    screen = Screen(lines, None, label + 'おくのて', kind='battle_menu', menu_cursor=192)
    mem = memory()
    p.battle_menu_step(screen, mem)
    assert not mem['battle'].get('okunote_only_observed')
    assert not decisions(mem, 'battle_okunote_egg_preempt')
    assert 'okunote_egg_preempt' not in mem['battle']
