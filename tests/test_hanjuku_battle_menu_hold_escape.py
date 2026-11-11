"""A battle menu that ignores the same press forever gets the opposite button.

g1163 2026-11-12 04:44-05:40: a battle command menu stayed on screen while the
plan kept pressing B, which that box ignores. The digest never changed,
`unchanged_seconds` reached 300 s and screen_stalled ended the corner — five
times in one hour. Repeating the same pad on an unchanged battle menu is not
progress.
"""
from docich.hanjuku import orchestrator as O
from docich.hanjuku.os_layer.screen import Screen


def screen(kind='battle_menu'):
    return Screen([], None, 'きりふだたいきゃく', kind=kind)


def test_a_repeated_ignored_press_is_escalated_once():
    mem = {}
    for _ in range(O.BATTLE_MENU_HOLD_ADVANCE - 1):
        assert O.battle_menu_hold_advance(screen(), mem, [O.pad('b')], 'same') is None
    assert O.battle_menu_hold_advance(screen(), mem, [O.pad('b')], 'same') == [O.pad('a')]
    record = mem['_records'][-1]
    assert record['decision'] == 'battle_menu_hold_advance'
    assert record['observed_metric']['from'] == ['b']
    # the counter restarts: the next frame does not press again
    assert O.battle_menu_hold_advance(screen(), mem, [O.pad('b')], 'same') is None


def test_a_changing_menu_never_escalates():
    mem = {}
    for i in range(O.BATTLE_MENU_HOLD_ADVANCE * 3):
        assert O.battle_menu_hold_advance(screen(), mem, [O.pad('b')], f'd{i}') is None


def test_an_empty_plan_keeps_holding_for_an_orphan_menu():
    """g741: no owner means no borrowed confirm; an empty plan is not escalated."""
    mem = {}
    for _ in range(O.BATTLE_MENU_HOLD_ADVANCE * 3):
        assert O.battle_menu_hold_advance(screen(), mem, [], 'same') is None


def test_a_multi_action_plan_is_not_escalated():
    mem = {}
    for _ in range(O.BATTLE_MENU_HOLD_ADVANCE * 2):
        assert O.battle_menu_hold_advance(
            screen(), mem, [O.pad('b'), O.pad('b')], 'same') is None


def test_the_egg_and_monster_command_boxes_are_escaped_too():
    for kind in ('egg_battle_menu', 'monster_menu'):
        mem = {}
        for _ in range(O.BATTLE_MENU_HOLD_ADVANCE - 1):
            assert O.battle_menu_hold_advance(screen(kind), mem, [O.pad('b')], 'same') is None
        assert O.battle_menu_hold_advance(screen(kind), mem, [O.pad('b')], 'same') == [O.pad('a')]


def test_another_screen_kind_is_not_escaped_here():
    mem = {}
    for _ in range(O.BATTLE_MENU_HOLD_ADVANCE * 2):
        assert O.battle_menu_hold_advance(screen('unknown'), mem, [O.pad('b')], 'same') is None
