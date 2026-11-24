"""v521: a summon with two zero-damage skills returns instead of repeating.

g1153 2026-11-08 17:45:26-17:47:25 JST: とうめいにんげん (イタズラがき /
イナイイナイバア) faced a transformed ゼウス. The enemy panel stayed at 85 HP
through seven own turns while the summon's HP fell 44 -> 31 -> 21 -> 11 -> 1,
and the log kept the choice (`monster_skill_stall` / monster_stall_both)
because returning would expose the general.

The menus here are synthetic frames in the shape the reader accepts. The
incident evidence is the live decision log
(run-soren-live/runtimes/g1153-e5c7fa38/hanjuku_decisions.previous.jsonl) and
the saved battle panel (decision-111.png, ゼウス 85 / ソーピニヨン 48); no
native menu frame of that turn was kept, so this test is not a replay of the
real pixels.
"""
from types import SimpleNamespace

from docich.hanjuku.flows import battle_menus
from docich.hanjuku.flows.battle_menus import (MONSTER_ATTACKLESS_TURNS, MONSTER_STALL_TURNS,
                                                _monster_stall_switch, monster_menu_step)
from docich.hanjuku.judgment.events import _monster_menu_attackless
from docich.hanjuku.os_layer.screen import parse
from test_hanjuku_chart_bot import monster_menu_frame

TOUMEI = ('イタズラがき', 'イナイイナイバア')


def menu(hp=44, enemy=('ゼウス', 85), cursor=0, skills=TOUMEI):
    return parse(monster_menu_frame(list(skills), ally=('とうめいにんげん', hp),
                                    enemy=enemy, cursor=cursor))


def memory():
    return {'chapter': 4, 'tick': 0,
            'battle': {'ally': 'ソーピニヨン', 'enemy': 'ゼウス', 'side': 'defense'}}


def decide(mem, screen):
    """One own turn: a later redraw forgets the cached choice."""
    mem['tick'] += 20
    mem.pop('monster_menu_choice', None)
    mem['_records'] = []
    buttons = monster_menu_step(screen, mem)
    return mem['monster_menu_choice'], buttons, [
        (r.get('decision'), r.get('strategy_variant')) for r in mem['_records']]


def test_the_two_zero_rows_are_read_from_the_opponent_column():
    ally = SimpleNamespace(name='とうめいにんげん', hp=44)
    general = SimpleNamespace(name='ゼウス', hp=85)
    monster = SimpleNamespace(name='コマイヌ', hp=85)
    assert _monster_menu_attackless('ally', ally, general, list(TOUMEI))
    assert _monster_menu_attackless('ally', ally, monster, list(TOUMEI))
    # Damage in either column keeps the menu out of the rule.
    woodball = SimpleNamespace(name='ウッドボール', hp=100)
    assert not _monster_menu_attackless('ally', woodball, general, ['たいあたり', 'ばくはつする'])
    # Fail closed: unknown monster, enemy-owned menu, unread spelling.
    assert not _monster_menu_attackless('ally', SimpleNamespace(name='なぞ', hp=10), general, list(TOUMEI))
    assert not _monster_menu_attackless('enemy', ally, general, list(TOUMEI))
    assert not _monster_menu_attackless('ally', ally, general, ['イタズラがき', 'ダークフォース'])
    assert not _monster_menu_attackless('ally', ally, None, list(TOUMEI))


def test_incident_sequence_tries_each_skill_once_then_returns():
    mem = memory()
    choice, buttons, records = decide(mem, menu(44))
    assert choice == 'skill2'                      # behind: effect skill first
    assert ('monster_skill_stall', 'monster_stall_skill1') not in records
    choice, _, records = decide(mem, menu(31))
    assert choice == 'skill1'                      # the other skill gets its turn
    assert ('monster_skill_stall', 'monster_stall_skill1') in records
    choice, _, records = decide(mem, menu(21))
    assert choice == 'retreat'
    assert ('monster_skill_stall', 'monster_stall_return') in records
    record = next(r for r in mem['_records'] if r.get('decision') == 'monster_skill_stall')
    assert record['observed_metric']['attackless'] is True
    assert mem['monster_stall']['turns'] == {'skill1': MONSTER_ATTACKLESS_TURNS,
                                             'skill2': MONSTER_ATTACKLESS_TURNS}


def test_return_reaches_the_egg_row_and_repeats_without_recounting():
    mem = memory()
    for hp in (44, 31, 21):
        decide(mem, menu(hp))
    assert mem['monster_menu_choice'] == 'retreat'
    turns = dict(mem['monster_stall']['turns'])
    # The confirm is sent once the cursor is confirmed on たまごに もどれ.
    choice, buttons, _ = decide(mem, menu(21, cursor=2))
    assert choice == 'retreat'
    assert buttons == [{'type': 'pad', 'buttons': ['a'], 'hold_ms': 100}]
    assert mem['monster_stall']['turns'] == turns     # no extra skill turn is spent


def test_a_damaging_menu_keeps_the_existing_two_skill_budget():
    mem = memory()
    mem['monster_stall'] = {'scope': ['ウッドボール', 'ゼウス'], 'enemy_hp': 85,
                            'turns': {'skill1': MONSTER_STALL_TURNS,
                                      'skill2': MONSTER_STALL_TURNS}}
    screen = parse(monster_menu_frame(['たいあたり', 'ばくはつする'],
                                      ally=('ウッドボール', 100), enemy=('ゼウス', 85), cursor=0))
    mem['tick'] += 20
    mem['_records'] = []
    monster_menu_step(screen, mem)
    assert mem['monster_menu_choice'] in ('skill1', 'skill2')
    assert ('monster_skill_stall', 'monster_stall_both') in [
        (r.get('decision'), r.get('strategy_variant')) for r in mem['_records']]


def test_special_monster_cycle_is_not_turned_into_a_return():
    mem = {'chapter': 3, 'tick': 0, 'battle': {'ally': 'ココット', 'enemy': 'ラズベリー'}}
    mem['monster_stall'] = {'scope': ['おめでとり', 'ラズベリー'], 'enemy_hp': 45,
                            'turns': {'skill1': MONSTER_ATTACKLESS_TURNS,
                                      'skill2': MONSTER_ATTACKLESS_TURNS}}
    screen = parse(monster_menu_frame(['ミカンをおす', 'あたためる'],
                                      ally=('おめでとり', 1000), enemy=('ラズベリー', 45), cursor=0))
    mem['tick'] += 20
    mem['_records'] = []
    monster_menu_step(screen, mem)
    assert mem['monster_menu_choice'] != 'retreat'


def test_the_return_latch_is_scoped_to_the_pair_and_enemy_hp():
    mem = memory()
    for hp in (44, 31, 21):
        decide(mem, menu(hp))
    assert mem['monster_menu_choice'] == 'retreat'
    # A different enemy panel HP is a new situation and starts a new count.
    choice, _, _ = decide(mem, menu(21, enemy=('ゼウス', 60)))
    assert choice in ('skill1', 'skill2')


def test_stall_switch_is_the_only_caller_entry_point_for_the_new_rule():
    mem = {'tick': 0}
    ally = SimpleNamespace(name='とうめいにんげん', hp=44)
    enemy = SimpleNamespace(name='ゼウス', hp=85)
    # The default stays on skill2 (behind); the switch and the return are both
    # decided from that same default on later turns.
    assert _monster_stall_switch(mem, 'ally', ally, enemy, 'skill2', 2,
                                 attackless=True) == 'skill2'
    mem['tick'] += 20
    assert _monster_stall_switch(mem, 'ally', ally, enemy, 'skill2', 2,
                                 attackless=True) == 'skill1'
    mem['tick'] += 20
    assert _monster_stall_switch(mem, 'ally', ally, enemy, 'skill2', 2,
                                 attackless=True) == 'retreat'
    mem['tick'] += 20
    assert _monster_stall_switch(mem, 'ally', ally, enemy, 'skill2', 2,
                                 attackless=True) == 'retreat'
    # Without the verified table the two-skill budget is unchanged.
    mem = {'tick': 0}
    for turn in range(4):
        mem['tick'] += 20
        action = _monster_stall_switch(mem, 'ally', ally, enemy, 'skill1', 2)
        assert action == ('skill1' if turn < MONSTER_STALL_TURNS else 'skill2')
    assert battle_menus.MONSTER_ATTACKLESS_TURNS == 1
