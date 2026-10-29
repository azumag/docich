"""A native foreground interruption is different from a broken Y cursor."""
import pytest

from docich.hanjuku.os_layer import navigation as nav
from docich.hanjuku.orchestrator import decide
from docich.hanjuku_screen import Screen
from test_hanjuku_chart_bot import Canvas


def state():
    return {'chapter': 1, 'active': 'X', 'tick': 100, 'cursor': [300, 300], '_records': []}


def screen(kind):
    return Screen([], None, '', kind=kind)


def test_zero_move_jump_interrupted_by_foreground_can_retry_without_confirming():
    mem = state()
    nav._start_y_jump(mem, {'step': 'X'}, 'アルマムーン', 'map')
    nav.observe_jump_foreground(screen('text'), mem)
    mem['tick'] += 70  # live event owned the screen for more than a minute
    assert nav._drop_stale_y_jump(screen('map'), mem) is False
    assert mem['y_jump_last']['foreground_interrupted'] is True
    assert nav._want_y_jump(mem, {'step': 'X'}, 'アルマムーン', 'map')
    assert mem['cursor'] == [300, 300] and 'near_goal' not in mem
    assert mem['y_jumps']['X:map'] == 1
    assert nav._start_y_jump(mem, {'step': 'X'}, 'アルマムーン', 'map') == [nav.pad('y')]
    assert mem['y_jumps']['X:map'] == 2


@pytest.mark.parametrize('kind', ['map', 'map_target', 'world_map', 'unknown'])
def test_map_or_unreadable_frames_do_not_release_a_zero_move_failure(kind):
    mem = state()
    mem['y_jump_last'] = {'step': 'X', 'mode': 'map', 'moves': 0, 'why': 'view_closed'}
    nav.observe_jump_foreground(screen(kind), mem)
    assert not nav._want_y_jump(mem, {'step': 'X'}, 'アルマムーン', 'map')


@pytest.mark.parametrize('why', ['cursor_stationary', 'cursor_unreadable'])
def test_foreground_event_never_releases_a_proven_cursor_failure(why):
    mem = state()
    mem['y_jump_last'] = {'step': 'X', 'mode': 'map', 'moves': 0, 'why': why}
    nav.observe_jump_foreground(screen('text'), mem)
    assert not nav._want_y_jump(mem, {'step': 'X'}, 'アルマムーン', 'map')


def test_fresh_event_recovers_a_hotloaded_receipt_but_not_an_exhausted_budget():
    mem = state()
    mem['y_jump_last'] = {'step': 'X', 'mode': 'map', 'moves': 0, 'why': 'view_closed'}
    nav.observe_jump_foreground(screen('month_menu'), mem)
    assert nav._want_y_jump(mem, {'step': 'X'}, 'アルマムーン', 'map')
    mem['y_jumps'] = {'X:map': nav.Y_JUMP_LIMIT}
    assert not nav._want_y_jump(mem, {'step': 'X'}, 'アルマムーン', 'map')


def test_foreground_does_not_release_another_orders_old_failure():
    mem = state()
    mem['y_jump_last'] = {'step': 'OLD', 'mode': 'map', 'moves': 0, 'why': 'view_closed'}
    nav.observe_jump_foreground(screen('text'), mem)
    assert not mem['y_jump_last'].get('foreground_interrupted')


def test_real_decide_records_foreground_before_dialogue_owner_returns():
    canvas = Canvas()
    canvas.text(16, 175, 'こまりますねえ')
    mem = state()
    mem['y_jump_last'] = {'step': 'X', 'mode': 'map', 'moves': 0, 'why': 'view_closed'}
    _, updated = decide(canvas.frame(), {'phase': 'field', 'policy': mem})
    assert updated['screen_kind'] == 'text'
    assert updated['policy']['y_jump_last']['foreground_interrupted'] is True
