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


def test_confirmed_no_resources_keeps_clear_hp_lead_before_the_wall_summon():
    mem = trevis()
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('たいきゃく',)), mem) == [p.pad('b')]
    assert mem['battle']['survival']['exhausted']
    assert p.battle_step(battle(mem), mem) == p._power_mash(mem, mem['battle'])
    assert not p._hero_retreat_needed(mem['battle'])
    assert p._hero_retreat_menu(menu(('たいきゃく',)), mem, mem['battle']) is None
    assert not mem['battle'].get('hero_retreat')
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


@pytest.mark.parametrize('own,hp', [('トレビス', 66), ('タルタル', 40), ('アマンディーヌ', 59)])
@pytest.mark.parametrize('uses', [1, 4, 5])
def test_acquired_live_egg_uses_override_initially_eggless_general(own, hp, uses):
    mem = trevis(); mem['battle'].update(ally=own, ally_hp=hp, start_ally_hp=hp)
    mem['egg_uses'] = {own: uses}
    actions = p.battle_step(battle(mem), mem)
    assert not p._unarmed_wall_risk(mem['battle'])
    assert actions != [p.pad('b')]
    assert not mem['battle'].get('survival')
    assert mem['egg_uses'][own] == uses


def test_acquired_egg_count_is_rechecked_after_json_and_new_observed_zero():
    mem = trevis(); mem['egg_uses'] = {'トレビス': 1}
    assert p.battle_step(battle(mem), mem) != [p.pad('b')]
    mem = json.loads(json.dumps(mem))
    mem['egg_uses']['トレビス'] = 0
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert p._unarmed_wall_risk(mem['battle'])


@pytest.mark.parametrize('uses', [None, True, '1', 1.0, 6, -1])
def test_other_general_or_invalid_count_cannot_claim_acquired_egg(uses):
    mem = trevis(); mem['egg_uses'] = {'ヴィーナス': 4, 'トレビス': uses}
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert mem['battle']['own_egg_positive_observed'] is False


def test_menu_rechecks_acquired_egg_before_stale_healthy_retreat_guard():
    mem = trevis()
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    mem['battle']['survival'].update(exhausted=True, cards_uncertain=False)
    mem['battle']['own_egg_zero_observed'] = True
    mem['egg_uses'] = {'トレビス': 1}
    p.battle_menu_step(menu(), mem)
    assert mem['battle']['own_egg_positive_observed'] is True
    assert mem['battle']['own_egg_zero_observed'] is False
    assert not p._hero_retreat_needed(mem['battle'])


@pytest.mark.parametrize('fresh', [None, 'other', 1, 0])
def test_selection_recheck_blocks_old_positive_until_named_observation(fresh):
    from test_hanjuku_chart_bot import sortie_canvas
    mem = trevis()
    # Actual observed-count producer, then actual menu selection producer.
    p.observe_events(sortie_canvas('トレビス', 1), mem)
    mem['battle']['ally_hp'] = 25
    assert p.battle_step(battle(mem), mem) == [p.pad('b')]
    assert p.battle_menu_step(menu(('たまごをつかう', 'たいきゃく')), mem) == [p.pad('a')]
    assert mem['egg_recheck'] == ['トレビス']
    assert mem['egg_uses']['トレビス'] == 1  # selection does not prove a use
    p.battle_end(mem, 'map'); p.battle_end(mem, 'map')
    mem = json.loads(json.dumps(mem))
    if fresh == 'other': p.observe_events(sortie_canvas('ゼウス', 4), mem)
    elif type(fresh) is int: p.observe_events(sortie_canvas('トレビス', fresh), mem)
    # A subsequent synthetic complete attack panel; keep its named context,
    # but never manufacture a count decrement or consumption receipt.
    mem['battle'] = trevis()['battle']
    first = p.battle_step(battle(mem), mem)
    mem = json.loads(json.dumps(mem))
    second = p.battle_step(battle(mem), mem)
    if fresh in (None, 'other', 0):
        assert first == [p.pad('b')]
        assert not any(a.get('buttons') == ['a'] for a in second)
        assert mem['battle']['own_egg_positive_observed'] is False
    else:
        assert first != [p.pad('b')]
        assert mem['battle']['own_egg_positive_observed'] is True
    assert mem['egg_uses']['トレビス'] == (fresh if type(fresh) is int else 1)
    assert ('トレビス' in mem.get('egg_recheck', [])) == (fresh in (None, 'other'))
