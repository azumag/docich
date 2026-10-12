"""g713 disabled defense menu: inspect only, never infer an available lottery.

The three PNGs are original observed frames. The later okunote row below is
synthetic; no naturally accepted down/okunote receipt exists for this change.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from docich import hanjuku_policy as p
from docich.hanjuku_font import TextLine
from docich.hanjuku_pixels import Frame, read_png
from docich.hanjuku_screen import Screen, parse

ROOT = Path(__file__).parent / 'fixtures/hanjuku'
RGB = {
    30772: '8413d8cce38e23701cce5b08f756574c7377b0ea1e3ec0da2e5f00c2c5f8e8e4',
    30774: 'e84001092cac3ba52846d873e8cc9bbaf6cd01506889ec7ac203d9b153d37550',
    30780: 'da2426f40f083f92054e94ec0ec81a86a33fc3ec3f7833e14997837a5c52b38f',
}
IDENTITY = {'game': 'hanjuku-hero', 'runtime_id': 'g713-58fb2ff1',
            'generation': 713, 'lease_id': 'b21bde1f-f8bf-49a6-95da-9288f9605f69'}


def native(uid):
    frame = read_png(ROOT / f'g713-chevre-tartar-{uid}.png')
    assert frame.digest() == RGB[uid]
    return parse(frame)


def memory():
    return {'chapter': 1, 'tick': 100, '_run_identity': deepcopy(IDENTITY),
            'battle': {'ally': 'シェーブル', 'enemy': 'タルタル', 'side': 'defense',
                       'castle': 'キカンドン', 'start_ally_hp': 19, 'ally_hp': 19,
                       'start_enemy_hp': 40, 'enemy_hp': 40, 'step': None,
                       'cards_used': [], 'card_evidence_version': 1,
                       'planned_cards': []}}


def lottery_parent():
    row = TextLine(208, tuple((176+8*i, ch) for i, ch in enumerate('おくのて')))
    return Screen([row], None, 'おくのて', kind='battle_menu', menu_cursor=208)


def test_actual_two_disabled_rows_inspect_instead_of_abandoning_rescue():
    opening, menu = native(30772), native(30774)
    assert (opening.battle.ally, opening.battle.ally_hp, opening.battle.enemy_hp) == ('シェーブル', 19, 40)
    assert menu.kind == 'battle_menu' and menu.disabled_item_box
    assert not menu.hidden_battle_commands and not menu.text and menu.menu_cursor == 192
    mem = memory()
    assert p.battle_menu_step(menu, mem) == [p.pad('down')]
    assert not mem['battle'].get('okunote_only_observed')
    assert not mem['battle'].get('okunote_last_resort_selected')
    assert not mem.get('egg_zero_owners')  # disabled does not mean zero or broken
    assert mem['_records'][-1]['resulting_event'] == 'row_inspection_only'


def test_missing_lower_row_stops_after_two_downs_even_through_persistence():
    mem = memory(); menu = native(30774)
    assert p.battle_menu_step(menu, mem) == [p.pad('down')]
    mem = json.loads(json.dumps(mem))
    assert p.battle_menu_step(menu, mem) == [p.pad('down')]
    for _ in range(3):
        assert p.battle_menu_step(menu, mem) == [p.pad('b')]
    assert mem['battle']['disabled_defense_probe']['downs'] == 2


def test_probe_never_accepts_lottery_before_current_hp_loss_and_real_row():
    mem = memory(); menu = native(30774)
    assert p.battle_menu_step(menu, mem) == [p.pad('down')]
    # Merely discovering the synthetic real label cannot waive the existing
    # HP-loss rule or license an irreversible choice at the old opening HP.
    assert p.battle_menu_step(lottery_parent(), mem) == [p.pad('b')]
    assert mem['battle']['okunote_only_observed']
    assert not mem['battle'].get('okunote_last_resort_selected')
    # Original 30780 shows current HP1/40. The already-observed parent then
    # gets the existing one-time recheck, followed by the usual row/cursor gate.
    assert p.battle_step(native(30780), mem) == [p.pad('b')]
    assert mem['battle']['okunote_last_resort_reopened']
    assert p.battle_menu_step(lottery_parent(), mem) == [p.pad('a')]
    assert p.battle_menu_step(lottery_parent(), mem) == [p.pad('b')]


@pytest.mark.parametrize('changes', [
    {'ally': 'どうし'}, {'ally': 'しゅじんこう'}, {'ally': 'だいじん'},
    {'ally': '不明'}, {'enemy': '不明'}, {'enemy': 'クイーン'},
    {'side': 'attack'}, {'side': None}, {'castle': '前章の城'},
    {'ally_hp': 27}, {'ally_hp': 0}, {'ally_hp': True}, {'enemy_hp': None},
    {'start_ally_hp': 999}, {'planned_cards': ['クースカン']},
    {'card_flow': {'stage': 'menu'}}, {'egg_battle': True}, {'away': 1},
    {'card_hp_unread': True}, {'card_context_unclassified': True}, {'card_summon_observed': True},
    {'okunote_last_resort_selected': True}, {'okunote_egg_preempt': {'selected': True}},
])
def test_hero_attack_health_unknown_context_and_other_resources_do_not_probe(changes):
    mem = memory(); mem['battle'].update(changes)
    assert p._disabled_defense_probe(native(30774), mem, mem['battle']) is None
    assert 'disabled_defense_probe' not in mem['battle']


@pytest.mark.parametrize('bad', [True, -1, 3, None, '1'])
def test_saved_bad_budget_is_not_reset_into_more_navigation(bad):
    mem = memory(); p.battle_menu_step(native(30774), mem)
    mem['battle']['disabled_defense_probe']['downs'] = bad
    assert p.battle_menu_step(native(30774), mem) == [p.pad('b')]


def test_new_scope_cannot_borrow_saved_inspection():
    mem = memory(); p.battle_menu_step(native(30774), mem)
    mem['_run_identity']['generation'] += 1
    assert p.battle_menu_step(native(30774), mem) == [p.pad('b')]
    mem = memory(); mem['_run_identity'] = None
    assert p._disabled_defense_probe(native(30774), mem, mem['battle']) is None


def test_missing_cursor_and_a_new_live_resource_do_not_get_gray_permission():
    menu = native(30774); menu.menu_cursor = None
    mem = memory()
    assert p._disabled_defense_probe(menu, mem, mem['battle']) is None
    menu = lottery_parent(); menu.disabled_item_box = True
    assert p._disabled_defense_probe(menu, mem, mem['battle']) is None


def test_actual_army_strength_prevents_inspection_in_an_otherwise_close_match():
    mem = memory(); mem['battle'].update(card_soldiers_current=True,
                                       ally_soldiers=6, enemy_soldiers=0)
    assert p._disabled_defense_probe(native(30774), mem, mem['battle']) is None


def test_bool_in_saved_scope_cannot_match_the_integer_chapter():
    mem = memory(); p.battle_menu_step(native(30774), mem)
    mem['battle']['disabled_defense_probe']['chapter'] = True
    assert p.battle_menu_step(native(30774), mem) == [p.pad('b')]


@pytest.mark.parametrize('kind', ['unknown', 'text', 'battle_menu_pending'])
def test_fading_probe_waits_boundedly_then_closes_without_unknown_a(kind):
    mem = memory(); p.battle_menu_step(native(30774), mem)
    fade = Screen([], None, '', kind=kind)
    for _ in range(3):
        assert p.defender_egg_pending_step(fade, mem) == [{'type': 'wait', 'ms': 50}]
    mem = json.loads(json.dumps(mem))
    assert p.defender_egg_pending_step(fade, mem) == [p.pad('b')]
    for _ in range(3):
        assert p.defender_egg_pending_step(fade, mem) == [p.pad('b')]
    assert p.battle_menu_step(native(30774), mem) == [p.pad('b')]


def test_pending_gray_return_uses_remaining_budget_without_reset():
    mem = memory(); menu = native(30774)
    p.battle_menu_step(menu, mem)
    assert p.defender_egg_pending_step(menu, mem) == [p.pad('down')]
    assert p.defender_egg_pending_step(menu, mem) == [p.pad('b')]
    assert mem['battle']['disabled_defense_probe']['downs'] == 2


def test_pending_probe_does_not_borrow_a_different_actor_or_run():
    for field, value in [('ally', 'どうし'), ('enemy', 'クミン')]:
        mem = memory(); p.battle_menu_step(native(30774), mem)
        mem['battle'][field] = value
        assert p.defender_egg_pending_step(Screen([], None, '', kind='text'), mem) == [p.pad('b')]
    mem = memory(); p.battle_menu_step(native(30774), mem)
    mem['_run_identity']['generation'] += 1
    assert p.defender_egg_pending_step(Screen([], None, '', kind='unknown'), mem) == [p.pad('b')]


def test_bot_integration_keeps_unknown_fade_from_default_confirmation():
    from docich.hanjuku_bot import decide
    frame = read_png(ROOT / 'g713-chevre-tartar-30774.png')
    actions, state = decide(frame, {'policy': memory()}, run_identity=IDENTITY)
    assert actions == [p.pad('down')]
    # Derived blank fade, not an original battle screenshot.
    fade = Frame(256, 224, bytes(256*224*3))
    for _ in range(3):
        actions, state = decide(fade, state, run_identity=IDENTITY)
        assert actions == [{'type': 'wait', 'ms': 50}]
        state = json.loads(json.dumps(state))
    actions, state = decide(fade, state, run_identity=IDENTITY)
    assert actions == [p.pad('b')]
    for _ in range(3):
        actions, state = decide(fade, state, run_identity=IDENTITY)
        assert actions == [p.pad('b')]
    actions, state = decide(frame, state, run_identity=IDENTITY)
    assert actions == [p.pad('b')]


def test_measured_battle_end_releases_close_only_owner_to_result_handler():
    for field in ('ally_hp', 'enemy_hp'):
        mem = memory(); p.battle_menu_step(native(30774), mem)
        mem['battle'][field] = 0
        assert p.defender_egg_pending_step(Screen([], None, '結果', kind='text'), mem) is None
        assert not mem['battle']['disabled_defense_probe']['pending']
