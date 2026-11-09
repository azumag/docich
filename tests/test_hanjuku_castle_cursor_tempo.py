"""Castle-list feedback changes cadence, never the observed jump input sequence."""
from copy import deepcopy
from dataclasses import replace

import pytest

from docich.hanjuku.os_layer.commands import menu_navigation_reading, pad
from docich.hanjuku.os_layer.font import UNKNOWN
from docich.hanjuku.os_layer.screen import parse
from test_hanjuku_castle_jump import (
    CHAPTER_6_LONG, M, _castle_list, _castle_list_window, _jump, _memory,
)
from test_hanjuku_house import roster
from test_hanjuku_menu_cursor_tempo import entry, state


@pytest.mark.parametrize('names', [
    ('アルマムーン', 'ナキューメラ'),
    ('アルマームン', 'ナキューメラ'),
    ('あるまむーん', 'おかざき'),
])
def test_castle_names_in_the_roster_panel_get_single_row_feedback(names):
    # Synthetic pixels use the measured list layout; not an emulator timing test.
    for selected in range(len(names)):
        screen = parse(roster(names=names, selected=selected))
        assert screen.kind == 'text'
        before = deepcopy(screen)
        reading = menu_navigation_reading(screen)
        assert reading == {'kind': 'text', 'menu': 'castle_list',
                           'cursor': [168, 39 + selected * 16],
                           'directions': ['up', 'down']}
        for direction in ('up', 'down'):
            assert entry.observation_interval_ms(state(screen), [pad(direction)]) == 500
        assert screen == before


@pytest.mark.parametrize('change', [
    'no_prompt', 'no_return_hint', 'no_hand', 'wrong_pane', 'unknown_kind',
    'unknown_name', 'adjacent_unknown', 'duplicate_row', 'mixed_general',
])
def test_castle_feedback_requires_clean_names_panel_and_current_hand(change):
    screen = _castle_list(['アルマムーン', 'ナキューメラ'])
    if change == 'no_prompt':
        screen.text = 'コマンドにもどりますじゃ!'
    elif change == 'no_return_hint':
        screen.text = 'Aボタンでステータスひょうじ'
    elif change == 'no_hand':
        screen.hand = None
    elif change == 'wrong_pane':
        screen.hand = (26, 33, 46, 46)
    elif change == 'unknown_kind':
        screen.kind = 'unknown'
    elif change == 'unknown_name':
        screen = _castle_list(['アルマ', 'ナキューメラ'])
    elif change == 'adjacent_unknown':
        line = screen.lines[0]
        screen.lines[0] = replace(line, cells=line.cells + ((160, UNKNOWN),))
    elif change == 'duplicate_row':
        screen.lines.append(screen.lines[0])
    else:
        screen = _castle_list(['アルマムーン', 'ゼウス'])
    assert menu_navigation_reading(screen) is None
    assert entry.observation_interval_ms(state(screen), [pad('down')]) == 1500


@pytest.mark.parametrize('actions', [
    [], [pad('a')], [pad('b')], [pad('select')], [pad('left')], [pad('right')],
    [pad('down'), pad('down')], [pad('down'), pad('select')],
    [{**pad('down'), 'hold_ms': 200}], [{**pad('down'), 'buttons': ['down', 'a']}],
])
def test_castle_feedback_keeps_confirmations_batches_and_long_holds_at_normal_cadence(actions):
    screen = _castle_list(['アルマムーン', 'ナキューメラ'])
    assert state(screen)['menu_navigation_reading'] is not None
    assert entry.observation_interval_ms(state(screen), actions) == 1500


@pytest.mark.parametrize('change', ['old_step', 'different_screen'])
def test_castle_feedback_cannot_reuse_a_previous_observation(change):
    saved = state(_castle_list(['アルマムーン', 'ナキューメラ']))
    assert saved['menu_navigation_reading'] is not None
    if change == 'old_step':
        saved['step'] += 1
    else:
        saved['screen_kind'] = 'map'
    assert entry.observation_interval_ms(saved, [pad('down')]) == 1500


def test_long_castle_list_keeps_ten_downs_then_select_with_less_scheduled_wait():
    mem = _memory()
    mem['castle_jump'] = _jump('list', menu_presses=2, source='アブダレ')
    selected = offset = 0
    intervals = []
    for _ in range(20):
        screen = _castle_list_window(CHAPTER_6_LONG, offset, selected)
        actions = M.castle_jump_step(screen, mem, None)
        before = deepcopy(mem)
        interval = entry.observation_interval_ms(state(screen), actions)
        assert mem == before
        if actions == [pad('select')]:
            assert interval == 1500
            break
        assert actions == [pad('down')]
        intervals.append(interval)
        selected += 1
        offset = max(0, selected - 7)
    else:
        raise AssertionError('the source row was never reached')
    assert selected == 10
    assert mem['castle_jump']['phase'] == 'confirm'
    assert not [r for r in mem['_records'] if r['decision'] == 'castle_jump_failed']
    assert intervals == [500] * 10
    # Scheduling budget only; input count and the observed SELECT gate stay intact.
    assert sum(intervals) == 5000  # formerly ten 1500ms cycles
