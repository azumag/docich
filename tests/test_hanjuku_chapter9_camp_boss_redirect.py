"""A camped general on chapter 9 is re-sent at the moving boss, never recalled
home (owner 2026-11-09). The tent menu's いどう route is unmeasured in the real
game; these tests pin the flow's behaviour on synthetic screens only.
"""
import pytest

from docich import hanjuku_chart as chart
from docich.hanjuku.flows import map as flow_map
from docich.hanjuku.flows import post_month_status as pms
from docich.hanjuku.flows import recall
from docich.hanjuku.os_layer.commands import pad
from docich.hanjuku.os_layer.screen import Frame, Screen
from docich.hanjuku.state.regions import flow as _flow
from test_hanjuku_chart_bot import camp_menu

BOSS = chart.boss_castle(9)
BOSS_CELL = (500, 300)
FRAME = Frame(256, 224, b'\x00' * (256 * 224 * 3))


@pytest.fixture(autouse=True)
def boss_cell():
    chart.set_boss_cell(9, BOSS_CELL)
    yield
    chart.set_boss_cell(9, None)


def mem9(**extra):
    return {'chapter': 9, 'tick': 100, '_records': [], **extra}


def field(cursor=(60, 60)):
    return Screen(lines=[], hand=None, text='', kind='map', cursor=cursor)


def events(mem):
    return [r['decision'] for r in mem['_records']]


def test_the_goal_exists_only_with_a_fresh_skull_cell_on_chapter_nine():
    assert recall.boss_redirect_goal(mem9()) == BOSS
    chart.set_boss_cell(9, None)
    assert recall.boss_redirect_goal(mem9()) is None
    chart.set_boss_cell(9, BOSS_CELL)
    assert recall.boss_redirect_goal({'chapter': 5}) is None


def test_a_camp_is_picked_up_for_the_boss_not_for_a_castle():
    mem = mem9()
    camps = [{'target': [60, 60]}]
    recall._camp_recall_step(field(), mem, FRAME, observed_camps=camps)
    state = _flow(mem)['recall']
    assert state['boss'] is True and state['goal'] == BOSS
    assert 'camp_found' in events(mem)


def test_without_a_boss_cell_a_chapter_nine_camp_stays_put():
    chart.set_boss_cell(9, None)
    mem = mem9()
    assert recall._camp_recall_step(field(), mem, FRAME, observed_camps=[{'target': [60, 60]}]) is None
    assert 'recall' not in (mem.get('flow') or {}) and _flow(mem).get('recall') is None


def test_other_chapters_still_recall_camps_home():
    mem = {'chapter': 5, 'tick': 100, '_records': []}
    recall._camp_recall_step(field(), mem, FRAME, observed_camps=[{'target': [60, 60]}])
    state = _flow(mem)['recall']
    assert not state.get('boss') and 'goal' not in state


def test_the_tent_menu_cursor_goes_to_idou_not_kikan():
    mem = mem9(recall={'stage': 'menu', 'steps': 0, 'boss': True, 'goal': BOSS})
    assert recall._camp_recall_step(camp_menu(3), mem, None) == [pad('up')]   # きかん row -> up toward いどう
    assert _flow(mem)['recall']['stage'] == 'menu'
    assert recall._camp_recall_step(camp_menu(0), mem, None) == [pad('a')]
    assert _flow(mem)['recall']['stage'] == 'dest'


def test_the_ordinary_recall_still_walks_to_kikan():
    mem = {'chapter': 2, 'tick': 100, '_records': [], 'recall': {'stage': 'menu', 'steps': 0}}
    assert recall._camp_recall_step(camp_menu(0), mem, None) == [pad('down')]


def target_screen(marker=(70, 70)):
    s = Screen(lines=[], hand=None, text='', kind='map_target', cursor=marker)
    s.marker = marker
    return s


def dest_mem(**extra):
    return mem9(recall={'stage': 'dest', 'steps': 0, 'boss': True, 'goal': BOSS, 'target': [60, 60]}, **extra)


def test_the_marker_is_aimed_at_the_boss_by_a_y_jump():
    mem = dest_mem()
    actions = recall._camp_recall_step(target_screen(), mem, FRAME)
    assert actions == [pad('y')]
    assert mem['y_jump']['goal'] == BOSS and mem['y_jump']['step'] == 'RECALL'


def test_after_the_jump_the_boss_target_is_confirmed_and_never_an_own_roof():
    mem = dest_mem(y_jumped={'RECALL': BOSS})
    actions = recall._camp_recall_step(target_screen(), mem, FRAME)
    assert actions == [pad('a')]
    state = _flow(mem)['recall']
    assert state['stage'] == 'await_dispatch' and state['goal'] == BOSS


def test_a_jump_that_keeps_failing_gives_up_and_leaves_the_camp_alone():
    mem = dest_mem(y_jumps={'RECALL:target': recall.BOSS_JUMP_LIMIT})
    actions = recall._camp_recall_step(target_screen(), mem, FRAME)
    assert actions == [pad('b')]
    assert _flow(mem).get('recall') is None and 'camp_boss_redirect_aborted' in events(mem)


def test_a_boss_cell_that_ages_out_mid_redirect_is_never_confirmed():
    chart.set_boss_cell(9, None)
    mem = dest_mem(y_jumped={'RECALL': BOSS})
    for _ in range(2):
        assert recall._camp_recall_step(target_screen(), mem, FRAME) == []
    assert recall._camp_recall_step(target_screen(), mem, FRAME) == [pad('b')]
    assert 'camp_boss_redirect_aborted' in events(mem)


def test_a_whole_island_picker_aborts_instead_of_sending_the_unit_home():
    mem = dest_mem()
    actions = flow_map.world_map_step(Screen(lines=[], hand=None, text='', kind='world_map'), mem, FRAME)
    assert actions == [pad('b')]
    assert _flow(mem).get('recall') is None
    record = [r for r in mem['_records'] if r['decision'] == 'camp_boss_redirect_aborted'][0]
    assert record['resulting_event'] == 'boss_picker_is_world_map'


def test_the_monthly_pass_leaves_camps_alone_on_chapter_nine_only():
    statuses = {'ヘラ': {'location': 'camp'}}
    assert pms._tasks(statuses, ['ヘラ'], camp_stays=True) == []
    assert pms._tasks(statuses, ['ヘラ']) == [{'kind': 'recall', 'name': 'ヘラ'}]
    assert pms._camp_stays({'chapter': 9}) and not pms._camp_stays({'chapter': 3})
