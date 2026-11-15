"""The camp menu's いどう is a field-cursor move to a visible own castle.

Owner 2026-11-15: with an own castle on screen, いどう selects it in the
field instead of walking the whole-island picker.
"""
from test_hanjuku_chart_bot import Canvas, camp_menu

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import recall as recall_flow
from docich.hanjuku.state.regions import flow as _flow
from docich.hanjuku_screen import Screen

CASTLE = (255, 40, 60)          # the measured own-roof red
A_TARGET = (60, 60)             # ジョンリギ at cam (491, 514)
B_TARGET = (76, 211)            # キカンドン at the same cam


def castle_frame():
    c = Canvas()
    for target in (A_TARGET, B_TARGET):
        for dy in range(7):
            for dx in range(7):
                c.put(target[0] - 3 + dx, target[1] + 2 + dy, CASTLE)
    return c.frame()


def memory():
    mem = {'chapter': 1, 'captured': ['ジョンリギ', 'キカンドン'], '_records': []}
    _flow(mem)['recall'] = {'stage': 'menu', 'steps': 0, 'target': [80, 90]}
    return mem


def test_the_visible_castle_maps_to_an_owned_name():
    visible = recall_flow._visible_own_castle(memory(), castle_frame())
    assert visible is not None
    assert visible['castle'] in ('ジョンリギ', 'キカンドン')


def test_the_menu_chooses_idou_and_records_the_visible_castle():
    mem = memory()
    actions = recall_flow.camp_recall_step(camp_menu(0), mem, castle_frame())
    assert any(r.get('decision') == 'camp_recall_visible_move' for r in mem['_records'])
    assert actions  # いどう is under the hand: one confirm
    assert _flow(mem)['recall']['stage'] == 'dest'


def test_the_field_cursor_confirm_requests_the_recall():
    mem = memory()
    frame = castle_frame()
    visible = recall_flow._visible_own_castle(mem, frame)
    state = _flow(mem)['recall']
    state.update(stage='dest', move_visible=visible)
    assert visible['castle'] == 'ジョンリギ'          # the first roof in scan order
    screen = Screen([], None, '', kind='map', cursor=A_TARGET, marker=None)
    recall_flow.camp_recall_step(screen, mem, frame)
    assert any(r.get('decision') == 'camp_recall_visible_move_confirm' for r in mem['_records'])
    assert _flow(mem)['recall']['stage'] == 'await_dispatch'
