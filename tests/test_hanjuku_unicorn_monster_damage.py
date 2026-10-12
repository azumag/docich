"""g719 owned Unicorn menus; policy replay is separate from natural outcomes."""
from dataclasses import replace
import json
from pathlib import Path

import pytest
from docich.hanjuku.flows.battle_menus import monster_menu_step
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.world import NAME
from docich.hanjuku_pixels import read_png
from docich.hanjuku_screen import parse
from test_hanjuku_chart_bot import monster_menu_frame

RGB = {5268: '2e60cbe7b383af7280ce2144c6070253ecabf1c6152061f7680a84fd983943cc',
       5283: '417c227915638ab199cc386b56b61f9d74a9fe28bf63d68937d6f742e848ba86',
       5292: '799200bbcb3d7fc345f18550fd7febdbafaed25e94c3d8bbea5056698b2db002',
       5301: 'c0d5bae53e09523b28bf9be62e3d15177cfe3f3498c97b015127ba385a1c7276'}


def native(uid=5268):
    f = read_png(Path(__file__).parent / f'fixtures/hanjuku/g719-unicorn-{uid}.png')
    assert f.digest() == RGB[uid]
    return parse(f)


def memory():
    return {'chapter': 1, 'battle': {'ally': NAME, 'enemy': 'ソーピニヨン',
                                    'side': 'defense', 'castle': 'アルマムーン'}}


@pytest.mark.parametrize('uid', [5268, 5283, 5292])
def test_owned_native_unicorn_pair_moves_to_regular_two_hit_attack(uid):
    s = native(uid); m = memory()
    assert s.menu_cursor == 176
    assert monster_menu_step(s, m) == [pad('down')]
    assert m['monster_menu_choice'] == 'skill2'
    # The second input requires its newly measured cursor; this is a
    # synthetic continuation, not a new native screenshot or use receipt.
    s.menu_cursor = 192
    assert monster_menu_step(s, m) == [pad('a')]
    assert m['battle']['independent']['action'] == 'skill2'


def test_same_hp_old_cached_first_skill_migrates_on_hotload_and_saved_replay():
    m = memory(); monster_menu_step(native(), m)
    m['monster_menu_choice'] = 'skill1'
    assert monster_menu_step(native(), m) == [pad('down')]
    assert m['monster_menu_choice'] == 'skill2'
    m = json.loads(json.dumps(m))
    assert monster_menu_step(native(), m) == [pad('down')]
    assert m['monster_menu_choice'] == 'skill2'


def test_native_low_hp_still_returns_to_egg_instead_of_forcing_damage():
    m = memory(); s = native(5301)
    assert monster_menu_step(s, m) == [pad('a')]
    assert m['monster_menu_choice'] == 'retreat'


def test_general_opponent_does_not_receive_the_monster_damage_rule():
    s = parse(monster_menu_frame(['つのでつく', 'うしろあしキック'],
              ally=('ユニコーン', 429), enemy=('カシュー', 39), cursor=0))
    m = memory()
    assert monster_menu_step(s, m) == [pad('a')]
    assert m['monster_menu_choice'] == 'skill1'


def test_enemy_owned_unicorn_menu_waits_and_does_not_apply_our_damage_rule():
    s = parse(monster_menu_frame(['つのでつく', 'うしろあしキック'],
              ally=(NAME, 90), enemy=('ユニコーン', 429), cursor=0))
    m = memory()
    assert monster_menu_step(s, m) == []
    assert not m.get('monster_menu_choice')
    assert m['_records'][-1]['observed_metric']['owner'] == 'enemy'


@pytest.mark.parametrize('side,value', [('ally', None), ('enemy', None), ('ally', 0),
                                      ('enemy', 0), ('ally', True), ('enemy', True)])
def test_unread_zero_or_bool_hp_never_enables_forced_two_hits(side, value):
    s = native()
    s.egg_rows = [replace(r, hp=value) if r.side == side else r for r in s.egg_rows]
    m = memory(); monster_menu_step(s, m)
    assert m.get('monster_menu_choice') != 'skill2'


@pytest.mark.parametrize('enemy', ['カシュー', '不明', 'ユニコーン'])
def test_same_hp_owner_or_actor_change_discards_cached_damage_and_predicted_cursor(enemy):
    m = memory(); monster_menu_step(native(), m)
    m = json.loads(json.dumps(m)); m['monster_menu_cursor'] = 192
    s = native(); s.menu_cursor = None
    # Counterfactual parsed panel: actor changed with identical old HP.
    s.egg_rows = [replace(r, name=enemy) if r.side == 'enemy' else r for r in s.egg_rows]
    monster_menu_step(s, m)
    assert m.get('monster_menu_choice') != 'skill2'
    assert m.get('monster_menu_cursor') != 192


def test_unread_second_skill_does_not_activate_a_named_rule():
    s = native(); s.menu_rows = [s.menu_rows[0], s.menu_rows[2]]
    m = memory(); monster_menu_step(s, m)
    assert m['monster_menu_choice'] == 'skill1'


def test_ambiguous_two_unicorns_do_not_activate_the_owned_rule():
    s = native(); s.egg_rows = [replace(r, name='ユニコーン') for r in s.egg_rows]
    m = memory(); monster_menu_step(s, m)
    assert m['monster_menu_choice'] == 'skill1'


def test_combatant_reset_discards_owned_menu_scope():
    from docich.hanjuku.judgment.battle import reset_battle_controls

    m = memory()
    monster_menu_step(native(), m)
    assert m['monster_menu_choice_scope'][0] == 'ally'
    reset_battle_controls(m)
    assert 'monster_menu_choice_scope' not in m
    assert 'monster_menu_choice' not in m


def test_chapter_change_discards_previous_monster_scope():
    from docich.hanjuku.strategy.campaign import _enter_chapter

    m = memory()
    monster_menu_step(native(), m)
    _enter_chapter(m, 2, reason='test measured chapter transition')
    assert m['chapter'] == 2
    assert 'monster_menu_choice_scope' not in m
    assert 'monster_menu_choice' not in m
