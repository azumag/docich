"""g684 eggless Trevis: wall summons need resources before healthy melee.

Native panel proves names/HP/armies; menu/list sequences are synthetic glyphs.
Neither opening nor selection proves card use, enemy egg loss, or victory.
"""
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_bot import decide
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_survival import memory, menu, battle
from test_hanjuku_card_damage_gate import cards, panel

FIXTURE = Path(__file__).parent / 'fixtures/hanjuku/g684-trevis-alfalfa-panel.png'


def trevis():
    mem = memory(66, 38)
    mem['battle'].update(ally='トレビス', enemy='アルファルファ', start_ally_hp=66,
                        start_enemy_hp=38, side='attack', castle='ゴーメン',
                        planned_cards=[], cards_selected=[], cards_unclassified=[])
    return mem


def test_native_eggless_general_checks_resources_before_first_wall_damage():
    frame = read_png(FIXTURE)
    assert frame.digest() == '26e8f7a51f8ec0c24cda67e8a4a96965ba0ff20c885b11fd2da4abc2b4018ded'
    screen = parse(frame)
    assert screen.field_soldiers == (6, 6)
    actions, state = decide(frame, {'policy': trevis()})
    assert actions == [p.pad('b')]
    cur = state['policy']['battle']
    assert cur['survival']['opens'] == 1
    assert cur['cards_used'] == [] and not cur.get('egg_battle')


def test_real_card_list_licenses_control_then_bounded_melee_without_fake_receipts():
    frame = read_png(FIXTURE)
    actions, state = decide(frame, {'policy': trevis()})
    assert actions == [p.pad('b')]
    mem = json.loads(json.dumps(state['policy']))
    assert p.battle_menu_step(menu(('きりふだ', 'たいきゃく')), mem) == [p.pad('a')]
    assert p.card_list_step(cards(['クースカン']), mem) == [p.pad('a')]
    cur = mem['battle']
    assert cur['cards_selected'] == ['クースカン'] and cur['cards_used'] == []
    assert cur['card_assessment']['reason'] == 'control_then_melee_not_a_kill'
    cur['enemy_hp'] = 19  # a subsequent synthetic complete panel observes it
    assert p.battle_step(panel(mem, soldiers=(6, 6)), mem) == p._power_mash(mem, cur)
    assert cur['card_melee_followup']['bursts'] == 1
    assert not cur.get('enemy_egg_dropped') and cur['cards_used'] == []


def test_confirmed_no_resources_retreats_before_healthy_wall_melee():
    mem = trevis()
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('たいきゃく',)), mem) == [p.pad('b')]
    assert mem['battle']['survival']['exhausted']
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('たいきゃく',)), mem) == [p.pad('a')]
    assert mem['battle']['hero_retreat']['selected'] == 1
    assert not mem['battle'].get('defeat_owner')


@pytest.mark.parametrize('own', ['トレビス', 'リースリング', 'マカデミア'])
def test_intrinsically_eggless_generals_share_the_wall_risk_rule(own):
    mem = trevis(); mem['battle']['ally'] = own
    assert p._unarmed_wall_risk(mem['battle'])


def test_observed_zero_uses_applies_to_egg_capable_general_and_is_rechecked():
    mem = trevis(); mem['battle'].update(ally='ヴィーナス', ally_hp=82, start_ally_hp=82)
    mem['egg_uses'] = {'ヴィーナス': 0}
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert mem['battle']['own_egg_zero_observed'] is True
    assert p._unarmed_wall_risk(mem['battle'])
    mem['egg_uses']['ヴィーナス'] = 4  # only a separate observed count can do this
    p.battle_menu_step(menu(), mem)
    assert mem['battle']['own_egg_zero_observed'] is False
    assert not p._unarmed_wall_risk(mem['battle'])


@pytest.mark.parametrize('changes', [
    {'ally': 'ヴィーナス'}, {'ally': 'どうし'}, {'ally': '未読'},
    {'enemy': 'ミント'}, {'enemy': '未読'}, {'enemy': 'クイーン'},
    {'side': None}, {'side': 'defense'}, {'planned_cards': ['イッテツーン']},
    {'egg_battle': True},
])
def test_wall_inspection_requires_known_eggless_attack_and_preserves_chart(changes):
    cur = trevis()['battle']; cur.update(changes)
    assert not p._unarmed_wall_risk(cur)


def test_unread_menu_cursor_never_selects_or_creates_resources():
    mem = trevis(); p.battle_step(battle(mem), mem)
    for _ in range(13):
        actions = p.battle_menu_step(menu(('きりふだ', 'たいきゃく'), selected=None), mem)
        assert actions != [p.pad('a')]
    assert mem['battle']['survival']['exhausted']
    assert mem['battle']['cards_used'] == [] and not mem['battle'].get('hero_retreat', {}).get('selected')
