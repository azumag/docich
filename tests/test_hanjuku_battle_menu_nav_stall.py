"""g713 command menu: an identical frame is not a licence to re-press.

UID16230..16232 of issue #2076 recorded the same command box (たまごをつかう /
きりふだ / たいきゃく, cursor on y176) with byte-identical RGB while one down
per observation was sent and the cursor never moved. These tests pin the
bounded recovery: hold on an unchanged frame, retry the same single press a
finite number of times, then close the menu. No A is ever emitted.
"""
from docich.hanjuku.flows.battle_menus import (BATTLE_MENU_STALL_RETRIES,
                                               BATTLE_MENU_STALL_WAIT,
                                               _battle_menu_move)
from docich.hanjuku_font import TextLine
from docich.hanjuku_screen import Screen

SHA = 'ec79c498bbc98f6c1c8401d4d581872d5cb52d3919f4c646e4a9ac1f932ad330'


def command_box(cursor=176, *, sha=SHA):
    rows = [TextLine(176, ((176, 'たまごをつかう'),)),
            TextLine(192, ((176, 'きりふだ'),)),
            TextLine(208, ((176, 'たいきゃく'),))]
    return Screen(rows, None, 'たまごをつかうきりふだたいきゃく',
                  kind='battle_menu', menu_rows=rows, menu_cursor=cursor,
                  frame_sha256=sha)


def memory():
    return {'chapter': 1, 'battle': {'ally': 'ゼウス', 'enemy': 'クイーン',
                                     'ally_hp': 85, 'enemy_hp': 70}}


def test_identical_frame_waits_retries_then_closes_without_confirming():
    menu, mem = command_box(), memory()
    assert _battle_menu_move(menu, mem, 'きりふだ')['buttons'] == ['down']
    for _ in range(BATTLE_MENU_STALL_WAIT):
        assert _battle_menu_move(menu, mem, 'きりふだ') == []
    for retry in range(BATTLE_MENU_STALL_RETRIES):
        assert _battle_menu_move(menu, mem, 'きりふだ')['buttons'] == ['down']
        assert mem['_records'][-1]['decision'] == 'battle_menu_nav_retry'
        assert mem['_records'][-1]['observed_metric']['retry'] == retry + 1
        for _ in range(BATTLE_MENU_STALL_WAIT):
            assert _battle_menu_move(menu, mem, 'きりふだ') == []
    closing = _battle_menu_move(menu, mem, 'きりふだ')
    assert [a['buttons'] for a in closing] == [['b']]
    assert mem['_records'][-1]['decision'] == 'battle_menu_nav_unaccepted'
    assert 'battle_menu_move' not in mem


def test_never_emits_a_on_the_unaccepted_path():
    menu, mem = command_box(), memory()
    actions = [_battle_menu_move(menu, mem, 'きりふだ')
               for _ in range(BATTLE_MENU_STALL_WAIT + BATTLE_MENU_STALL_RETRIES + 6)]
    flat = [a for batch in actions if isinstance(batch, list) for a in batch]
    assert not any(a.get('buttons') == ['a'] for a in flat)


def test_a_real_cursor_move_restarts_the_cadence():
    mem = memory()
    assert _battle_menu_move(command_box(), mem, 'きりふだ')['buttons'] == ['down']
    for _ in range(BATTLE_MENU_STALL_WAIT):
        assert _battle_menu_move(command_box(), mem, 'きりふだ') == []
    moved = command_box(192, sha='0fde35ecc161cf19760540d102cda235ad10bbf389a3b94fb76899ff4a861615')
    assert _battle_menu_move(moved, mem, 'きりふだ') == 'here'
    assert 'battle_menu_move' not in mem


def test_here_still_confirms_the_observed_target_row():
    mem = memory()
    assert _battle_menu_move(command_box(192), mem, 'きりふだ') == 'here'


def test_screen_without_a_digest_keeps_the_legacy_cadence():
    mem = memory()
    menu = command_box(sha=None)
    assert menu.frame_sha256 is None
    assert _battle_menu_move(menu, mem, 'きりふだ')['buttons'] == ['down']
    assert _battle_menu_move(menu, mem, 'きりふだ')['buttons'] == ['down']
